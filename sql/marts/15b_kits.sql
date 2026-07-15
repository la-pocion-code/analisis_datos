-- ============================================================================
-- Explosión de KITS desde Odoo (mrp.bom tipo phantom). Fase 3. Idempotente.
-- Archivo: sql/marts/15b_kits.sql  (ejecutar DESPUÉS de 14 y 15). 100% desde Odoo.
--
--   dim_kit_componente  — kit (product.product) → sus componentes, con cantidad del BOM.
--                         Poblada por cargar_kits() en el ETL (--dims la refresca).
--   v_ventas_explotada  — v_ventas_producto con los kits descompuestos en componentes:
--       * no-kit: la línea pasa tal cual (origen='INDIVIDUAL', el producto es su componente).
--       * kit:    una fila por componente; unidades = unidades_kit × cantidad_BOM y el valor se
--                 PRORRATEA por la cantidad del BOM (share del componente en el kit).
-- Análogo a ReportClassNew.explosion_ventas(), pero con las cantidades reales del BOM de Odoo.
-- ============================================================================

-- ── dim_kit_componente ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_kit_componente (
    kit_producto_id BIGINT  NOT NULL,   -- product.product del kit (BOM phantom)
    componente_id   BIGINT  NOT NULL,   -- product.product del componente (mrp.bom.line)
    cantidad        NUMERIC NOT NULL,   -- product_qty del componente en el BOM
    PRIMARY KEY (kit_producto_id, componente_id)
);

-- ── v_ventas_explotada ──────────────────────────────────────────────────────
CREATE OR REPLACE VIEW marts.v_ventas_explotada AS
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
-- Kits: una fila por componente; unidades × cantidad_BOM, valor prorrateado por cantidad_BOM.
SELECT v.*,
       k.componente_id,
       p.codigo AS componente_codigo,
       p.nombre AS componente_nombre,
       'KIT'::text AS origen,
       v.cantidad_neta  * k.cantidad AS cantidad_componente,
       v.venta_subtotal * (k.cantidad / NULLIF(SUM(k.cantidad) OVER (PARTITION BY v.linea_id), 0)) AS venta_componente
FROM marts.v_ventas_producto v
JOIN marts.dim_kit_componente k ON k.kit_producto_id = v.producto_id
LEFT JOIN marts.dim_producto  p ON p.producto_id     = k.componente_id;
