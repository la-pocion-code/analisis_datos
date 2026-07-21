-- ============================================================================
-- Explosión de KITS desde Odoo (mrp.bom tipo phantom). Idempotente.
-- Archivo: sql/marts/15b_kits.sql  (ejecutar DESPUÉS de 14 y 15). 100% desde Odoo.
--
-- Las ventas se necesitan de DOS formas (ver docs/guia_bi_ventas.md):
--   · KITS VENDIDOS      → `v_ventas_producto` (el kit es la unidad, tal como se factura).
--   · UNIDADES DE PRODUCTO → `v_ventas_explotada` (el kit se reparte en sus componentes).
-- ⚠ NO sumar las dos a la vez en el mismo visual: el valor se contaría doble.
--
--   dim_kit_componente  — kit (product.product) → componentes, cantidad POR UNIDAD DE KIT.
--                         Poblada por cargar_kits() (--dims). Ojo: Odoo tiene varias BOM phantom
--                         por kit; el ETL toma UNA sola (la más reciente) y normaliza por el lote.
--   v_precio_componente — precio de referencia por (producto, categoría de cliente).
--   v_ventas_explotada  — ventas con los kits descompuestos.
--
-- REPARTO DEL VALOR: el kit se vende a un valor único y hay que asignar a cada componente su parte
-- para sumarla a la venta de ese producto. Se prorratea por el PRECIO INDIVIDUAL del componente
-- (promedio DENTRO DE SU CATEGORÍA DE CLIENTE, porque el precio varía por canal), no a partes
-- iguales: repartir por igual desviaba 20-25% por producto (en PCNKIT12, PCN19 vale 40.349 suelto
-- y PCN03 25.478, pero a partes iguales ambos recibían 31.655).
-- El prorrateo es sobre el total de la línea ⇒ **el valor total siempre se conserva**.
-- ============================================================================

-- ── dim_kit_componente ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_kit_componente (
    kit_producto_id BIGINT  NOT NULL,   -- product.product del kit (BOM phantom)
    componente_id   BIGINT  NOT NULL,   -- product.product del componente (mrp.bom.line)
    cantidad        NUMERIC NOT NULL,   -- unidades del componente POR UNIDAD DE KIT
    PRIMARY KEY (kit_producto_id, componente_id)
);

-- ── v_precio_componente: precio individual por producto y categoría de cliente ──
-- Base del reparto. Solo ventas del producto SUELTO (excluye los kits) y con cantidad > 0 (fuera
-- notas crédito, que distorsionarían el promedio). Precio ponderado = valor / unidades.
CREATE OR REPLACE VIEW marts.v_precio_componente AS
SELECT v.producto_id,
       COALESCE(v.categoria, '(sin categoria)') AS categoria,
       SUM(v.venta_subtotal) / NULLIF(SUM(v.cantidad_neta), 0) AS precio_unitario_ref,
       SUM(v.cantidad_neta)  AS unidades_base
FROM marts.v_ventas_producto v
WHERE v.cantidad_neta > 0
  AND NOT EXISTS (SELECT 1 FROM marts.dim_kit_componente k
                   WHERE k.kit_producto_id = v.producto_id)   -- el kit no es referencia de sí mismo
GROUP BY 1, 2
HAVING SUM(v.cantidad_neta) > 0;

-- ── v_ventas_explotada ──────────────────────────────────────────────────────
-- Se recrea (no CREATE OR REPLACE): cambia la lista de columnas.
DROP VIEW IF EXISTS marts.v_ventas_explotada;

CREATE VIEW marts.v_ventas_explotada AS
-- No-kits: la línea pasa tal cual (el producto es su propio "componente").
SELECT v.*,
       v.producto_id      AS componente_id,
       v.producto_codigo  AS componente_codigo,
       v.producto         AS componente_nombre,
       'INDIVIDUAL'::text AS origen,
       v.cantidad_neta    AS cantidad_componente,
       v.venta_subtotal   AS venta_componente
FROM marts.v_ventas_producto v
WHERE NOT EXISTS (
    SELECT 1 FROM marts.dim_kit_componente k WHERE k.kit_producto_id = v.producto_id
)
UNION ALL
-- Kits: una fila por componente. Unidades = unidades_kit × cantidad_BOM.
-- Valor = valor_kit × peso/Σpeso, con peso = precio de referencia × cantidad_BOM.
-- Cascada del precio: (componente, categoría del cliente) → promedio global del componente → 1
-- (si ningún componente tiene precio, todos pesan igual ⇒ reparto a partes iguales).
SELECT * FROM (
    SELECT v.*,
           k.componente_id,
           p.codigo AS componente_codigo,
           p.nombre AS componente_nombre,
           'KIT'::text AS origen,
           v.cantidad_neta * k.cantidad AS cantidad_componente,
           v.venta_subtotal * (
               (COALESCE(pc.precio_unitario_ref, pg.precio_unitario_ref, 1) * k.cantidad)
               / NULLIF(SUM(COALESCE(pc.precio_unitario_ref, pg.precio_unitario_ref, 1) * k.cantidad)
                            OVER (PARTITION BY v.linea_id), 0)
           ) AS venta_componente
    FROM marts.v_ventas_producto v
    JOIN marts.dim_kit_componente k ON k.kit_producto_id = v.producto_id
    LEFT JOIN marts.dim_producto  p ON p.producto_id     = k.componente_id
    -- precio dentro de la categoría de cliente de la línea
    LEFT JOIN marts.v_precio_componente pc
           ON pc.producto_id = k.componente_id
          AND pc.categoria   = COALESCE(v.categoria, '(sin categoria)')
    -- respaldo: precio global del componente (todas las categorías)
    LEFT JOIN (
        SELECT producto_id,
               SUM(precio_unitario_ref * unidades_base) / NULLIF(SUM(unidades_base), 0) AS precio_unitario_ref
        FROM marts.v_precio_componente GROUP BY 1
    ) pg ON pg.producto_id = k.componente_id
) s;
