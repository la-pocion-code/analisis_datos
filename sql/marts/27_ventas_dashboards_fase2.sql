-- ============================================================================
-- Hoja de VENTAS — fase 2: las 9 sub-páginas que faltaban del informe de Power BI
-- Archivo: sql/marts/27_ventas_dashboards_fase2.sql  (ejecutar DESPUÉS de 23).
--
-- La fase 1 (23_mv_dashboards.sql) cubrió el Resumen. Esta fase añade lo que las
-- demás hojas necesitan y que NO se podía calcular con lo concedido:
--
--   · LÍNEA y CATEGORÍA DE PRODUCTO  → viven en `bi_lineas`, que no estaba en
--     ninguna MV ni en ningún lookup. Se añaden a `v_lk_producto` (en 24).
--   · ZONA del canal mayorista       → `map_zona`, tampoco expuesta. Se añade a
--     `v_lk_tercero` (en 24).
--   · UNIDADES A NIVEL DE KIT        → `mv_ventas_kit_mes`.
--   · CLIENTE NUEVO vs RECURRENTE    → `mv_ventas_cliente_primera`.
--   · TASA DE RECOMPRA               → `mv_ventas_recompra`.
--   · CICLO DE VIDA DEL PRODUCTO     → `bi_producto_lanzamiento` + `bi_ciclo_vida`
--     (semillas: el dato NO existe en el DW, ver más abajo).
--
-- ⚠ Los dos lookups (`v_lk_producto`, `v_lk_tercero`) se amplían en
-- **24_rol_intranet.sql**, que es su casa, y NO aquí. Si se redefinieran en los dos
-- archivos, re-ejecutar 24 después de 27 fallaría con «cannot drop columns from
-- view» — y re-ejecutar 24 es obligatorio cada vez que se reconstruyen las MV.
--
-- ── MEDICIONES QUE JUSTIFICAN CADA DECISIÓN (2026-07-29/30, solo lectura) ─────
--
--   bi_lineas unida por el CÓDIGO de los corchetes .. 35/35 filas, 94,43 % del valor
--   bi_lineas unida por el NOMBRE completo ........... 16/35 filas, 39,90 %  ← MAL
--   productos vendidos sin línea .................... 5 (PCN32/33/34/35/36), 5,57 %
--   map_zona cubre .................................. MAYORISTA NV al 100 %
--   presupuesto por zona, junio-2026 ................ 2.400.000.001 (= el informe)
--   unidades de kit 2024/2025/2026 .................. 26.474 / 47.407 / 32.851
--   las mismas leídas de la vista explotada ......... 139.370 en 2026  ← INFLADO ×4,2
--   tasa de recompra 2026 global / SHOPIFY .......... 14,07 % / 12,87 %
--   primera venta por producto ....................... 2024-06 → 2026-06 (38 prods)
--
-- ── IDEMPOTENCIA ─────────────────────────────────────────────────────────────
-- Las MV se hacen DROP + CREATE (no existe CREATE OR REPLACE MATERIALIZED VIEW).
-- Las semillas usan CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING, así que
-- re-ejecutar el script NO pisa lo que haya editado el negocio.
--
-- ⚠ Tras ejecutar este archivo hay que RE-EJECUTAR 24_rol_intranet.sql: los GRANT
-- se pierden al recrear una MV.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- SEMILLA 1 — Fecha de lanzamiento por producto.
--
-- ⚠ Este dato NO ES DERIVABLE del data warehouse y no es una limitación temporal:
-- la primera venta registrada es de 2024-06 y solo hay 38 productos con historia,
-- mientras el informe muestra lanzamientos de 2021-09 y 2022-05. Es decir, en
-- Power BI es una tabla escrita a mano. Derivarlo con MIN(fecha_venta) daría a
-- [PCN07] tres años menos de antigüedad y lo movería de «Clásico» a «Maduro»,
-- cambiando su meta de crecimiento del 2 % al 5 %: un error silencioso y con
-- consecuencias, así que se prefiere el hueco explícito.
--
-- Se siembran las 17 fechas legibles en la captura del informe
-- (docs/dashboards/ref/ventas/productos.webp del repo de la intranet). El resto de
-- productos queda SIN fila, y la intranet muestra «sin fecha de lanzamiento» en vez
-- de inventar un ciclo de vida.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_producto_lanzamiento (
    codigo            TEXT PRIMARY KEY,
    fecha_lanzamiento DATE NOT NULL,
    notas             TEXT
);

COMMENT ON TABLE marts.bi_producto_lanzamiento IS
  'Fecha de lanzamiento comercial por codigo de producto. Dato de NEGOCIO, no '
  'derivable del DW (la historia de ventas arranca en 2024-06 y hay lanzamientos '
  'de 2021). La edita el negocio; el ETL no la toca.';

COMMENT ON COLUMN marts.bi_producto_lanzamiento.codigo IS
  'dim_producto.codigo (default_code de Odoo), sin los corchetes.';

INSERT INTO marts.bi_producto_lanzamiento (codigo, fecha_lanzamiento, notas) VALUES
    ('KD01',  DATE '2025-07-14', 'Sembrado desde la captura del informe'),
    ('KD02',  DATE '2025-07-14', 'Sembrado desde la captura del informe'),
    ('KD03',  DATE '2025-07-14', 'Sembrado desde la captura del informe'),
    ('PCN01', DATE '2024-06-01', 'Sembrado desde la captura del informe'),
    ('PCN02', DATE '2024-06-01', 'Sembrado desde la captura del informe'),
    ('PCN03', DATE '2024-06-01', 'Sembrado desde la captura del informe'),
    ('PCN04', DATE '2024-06-01', 'Sembrado desde la captura del informe'),
    ('PCN07', DATE '2021-09-10', 'Sembrado desde la captura del informe'),
    ('PCN09', DATE '2022-05-01', 'Sembrado desde la captura del informe'),
    ('PCN10', DATE '2022-07-01', 'Sembrado desde la captura del informe'),
    ('PCN11', DATE '2024-06-04', 'Sembrado desde la captura del informe'),
    ('PCN12', DATE '2024-06-04', 'Sembrado desde la captura del informe'),
    ('PCN13', DATE '2024-06-04', 'Sembrado desde la captura del informe'),
    ('PCN14', DATE '2024-06-06', 'Sembrado desde la captura del informe'),
    ('PCN15', DATE '2023-03-02', 'Sembrado desde la captura del informe'),
    ('PCN19', DATE '2023-04-01', 'Sembrado desde la captura del informe'),
    ('PCN20', DATE '2023-06-20', 'Sembrado desde la captura del informe')
ON CONFLICT (codigo) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- SEMILLA 2 — Tramos del ciclo de vida y su meta de crecimiento.
--
-- ⚠ Los CORTES en meses (18 y 36) son una INFERENCIA a partir de los casos
-- visibles en el informe: antigüedad 12 → «Crecimiento», 25 → «Maduro», 37 y más
-- → «Clásico / Consolidado». Las metas (20 / 5 / 2 %) sí se leen literalmente.
-- Va en tabla y no en el código de la intranet precisamente para que el negocio
-- pueda corregir los cortes sin desplegar nada.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_ciclo_vida (
    orden                    SMALLINT PRIMARY KEY,
    etiqueta                 TEXT     NOT NULL,
    -- Límite superior del tramo en meses, inclusive. NULL = «sin límite» (el
    -- último tramo). Se lee ordenado por `orden` y se toma el primero que cumpla.
    meses_hasta              SMALLINT,
    crecimiento_esperado_pct NUMERIC(6, 2) NOT NULL
);

COMMENT ON TABLE marts.bi_ciclo_vida IS
  'Tramos de ciclo de vida por antiguedad en meses y su meta de crecimiento. '
  'Los cortes 18/36 son inferidos del informe: revisar con el negocio.';

INSERT INTO marts.bi_ciclo_vida (orden, etiqueta, meses_hasta, crecimiento_esperado_pct) VALUES
    (1, 'Crecimiento',            18,   20.00),
    (2, 'Maduro',                 36,    5.00),
    (3, 'Clásico / Consolidado',  NULL,  2.00)
ON CONFLICT (orden) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_kit_mes — UNIDADES Y VALOR A NIVEL DE KIT (hoja «Kits»)
-- Grano: mes × empresa × kit × categoría(canal).
--
-- ⚠ Se lee de `v_ventas_producto` (una fila por línea de factura) y NO de
-- `v_ventas_explotada` / `v_ventas_bi`. En la vista explotada un kit aparece una vez
-- POR COMPONENTE con la MISMA `cantidad_neta` del kit, así que sumarla multiplica:
-- medido en 2026, 139.370 unidades contra las 32.851 reales (×4,2). El valor sí
-- coincide en las dos vías porque la explosión prorratea `venta_componente`; las
-- unidades no.
--
-- ⚠ Estas cifras NO van a cuadrar con Power BI, y es correcto: el informe da 113.862
-- unidades históricas contra 106.732 aquí (−6,3 %) porque `v_ventas_producto` excluye
-- `es_reverso` —las anulaciones reales—. Por kit la diferencia es del 0,1 %
-- (PCNKIT12: 15.137 aquí vs 15.113 allí). Mismo criterio ya documentado para el
-- Resumen: la cifra buena es la de la intranet.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_kit_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_kit_mes AS
SELECT
    date_trunc('month', v.fecha_venta)::DATE                          AS fecha_mes,
    (EXTRACT(YEAR FROM v.fecha_venta) * 100
     + EXTRACT(MONTH FROM v.fecha_venta))::INTEGER                    AS periodo_aaaamm,
    EXTRACT(YEAR  FROM v.fecha_venta)::SMALLINT                       AS anio,
    EXTRACT(MONTH FROM v.fecha_venta)::SMALLINT                       AS mes,
    COALESCE(v.empresa_id, -1)                                        AS empresa_id,
    -- `producto_id` de v_ventas_producto ES el kit (la explosión a componentes
    -- ocurre después, en 15b_kits.sql). Se renombra para que no se confunda con el
    -- `componente_id` de las demás MV.
    v.producto_id                                                     AS kit_id,
    COALESCE(NULLIF(btrim(v.categoria), ''), '(sin categoria)')       AS categoria,
    SUM(v.cantidad_neta)                                              AS unidades_kit,
    SUM(v.venta_subtotal)                                             AS valor,
    COUNT(DISTINCT v.factura_id)                                      AS facturas  -- ⚠ NO aditivo
FROM marts.v_ventas_producto v
JOIN marts.dim_producto dp
      ON dp.producto_id = v.producto_id
     AND dp.es_kit IS TRUE
WHERE v.fecha_venta IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 6, 7;

CREATE UNIQUE INDEX ux_mv_ventas_kit_mes
    ON marts.mv_ventas_kit_mes (periodo_aaaamm, empresa_id, kit_id, categoria);

CREATE INDEX ix_mv_ventas_kit_mes_anio    ON marts.mv_ventas_kit_mes (anio);
CREATE INDEX ix_mv_ventas_kit_mes_kit     ON marts.mv_ventas_kit_mes (kit_id);
CREATE INDEX ix_mv_ventas_kit_mes_periodo ON marts.mv_ventas_kit_mes (periodo_aaaamm);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_kit_mes IS
  'Kits vendidos al grano mes x empresa x kit x categoria. unidades_kit son '
  'unidades DE KIT (leidas de v_ventas_producto, sin explotar). El nombre del kit '
  'sale de v_lk_producto por kit_id. facturas NO es aditivo.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_cliente_primera — PRIMERA Y ÚLTIMA COMPRA POR CLIENTE
-- Grano: tercero_id. ~120.930 filas.
--
-- Habilita «clientes nuevos vs antiguos» (hoja Página web), «clientes de los
-- últimos 6 meses» y «clientes con compra» (hoja Mayoristas).
--
-- ⚠ SESGO CONOCIDO: la historia de ventas arranca en 2024-06-01, así que todo
-- cliente cuya primera compra real sea anterior aparece como «nuevo» en ese mes.
-- Para 2025 y 2026 el dato es bueno; en 2024 la intranet tiene que avisarlo en vez
-- de dar el número a secas.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_cliente_primera CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_cliente_primera AS
SELECT
    COALESCE(v.tercero_id, -1)                                        AS tercero_id,
    MIN(v.fecha_venta)                                                AS primera_fecha,
    (EXTRACT(YEAR  FROM MIN(v.fecha_venta)) * 100
     + EXTRACT(MONTH FROM MIN(v.fecha_venta)))::INTEGER               AS primer_periodo,
    MAX(v.fecha_venta)                                                AS ultima_fecha,
    (EXTRACT(YEAR  FROM MAX(v.fecha_venta)) * 100
     + EXTRACT(MONTH FROM MAX(v.fecha_venta)))::INTEGER               AS ultimo_periodo,
    COUNT(DISTINCT v.factura_id)                                      AS facturas_historicas
FROM marts.v_ventas_bi v
WHERE v.fecha_venta IS NOT NULL
GROUP BY 1;

CREATE UNIQUE INDEX ux_mv_ventas_cliente_primera
    ON marts.mv_ventas_cliente_primera (tercero_id);

CREATE INDEX ix_mv_cliente_primera_periodo ON marts.mv_ventas_cliente_primera (primer_periodo);
CREATE INDEX ix_mv_cliente_ultimo_periodo  ON marts.mv_ventas_cliente_primera (ultimo_periodo);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_cliente_primera IS
  'Primera y ultima compra por cliente. Un cliente es NUEVO en un mes si '
  'primer_periodo = ese mes. OJO: la historia arranca en 2024-06, asi que en 2024 '
  'el conteo de nuevos esta inflado.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_recompra — TASA DE RECOMPRA
-- Grano: anio × nivel × categoría × componente. Sentinelas por nivel.
--
-- Recompra = clientes que compraron **2 o más veces** (facturas distintas) sobre
-- los clientes que compraron. Verificado contra el informe: SHOPIFY 2026 da 12,87 %
-- aquí y 13,14 % allí (el informe va por fecha de factura e incluye reversos).
--
-- ⚠ **POR QUÉ HAY UNA COLUMNA `nivel` Y NO UN CUBO CON GROUPING SETS.** Un
-- `COUNT(DISTINCT factura_id)` por cliente NO se puede rodar hacia arriba: un
-- cliente que compró el producto A una vez y el B una vez tiene «1 vez» en cada
-- producto pero «2 veces» en el total, así que agregar el pre-agregado daría una
-- recompra falsa de 0 %. Cada nivel RECALCULA el conteo a su propio grano, y
-- `nivel` obliga a quien consulta a decir cuál quiere: sin esa columna, un
-- `WHERE categoria = 'SHOPIFY'` sumaría el detalle y el total a la vez.
--
-- ⚠ **NO lleva empresa a propósito.** Un cliente que recompra es un cliente que
-- recompra, sin importar qué sociedad le facturó; partirlo por empresa contaría dos
-- veces al que compró en las dos. La intranet debe devolver `null` + la razón si
-- alguien filtra por empresa sobre este KPI.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_recompra CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_recompra AS
WITH base AS (
    SELECT EXTRACT(YEAR FROM v.fecha_venta)::SMALLINT                 AS anio,
           COALESCE(NULLIF(btrim(v.categoria), ''), '(sin categoria)') AS categoria,
           COALESCE(v.componente_id, -1)                              AS componente_id,
           v.tercero_id,
           v.factura_id
    FROM marts.v_ventas_bi v
    WHERE v.fecha_venta IS NOT NULL
      AND v.tercero_id  IS NOT NULL
),
-- Nivel 1: el producto dentro de un canal.
n_prod_canal AS (
    SELECT anio, categoria, componente_id, tercero_id,
           COUNT(DISTINCT factura_id) AS veces
    FROM base GROUP BY 1, 2, 3, 4
),
-- Nivel 2: el canal completo (todos sus productos juntos).
n_canal AS (
    SELECT anio, categoria, tercero_id, COUNT(DISTINCT factura_id) AS veces
    FROM base GROUP BY 1, 2, 3
),
-- Nivel 3: el producto en todos los canales.
n_prod AS (
    SELECT anio, componente_id, tercero_id, COUNT(DISTINCT factura_id) AS veces
    FROM base GROUP BY 1, 2, 3
),
-- Nivel 4: el total del año.
n_total AS (
    SELECT anio, tercero_id, COUNT(DISTINCT factura_id) AS veces
    FROM base GROUP BY 1, 2
)
SELECT anio, 'producto_canal'::TEXT AS nivel, categoria, componente_id,
       COUNT(*)                             AS clientes,
       COUNT(*) FILTER (WHERE veces >= 2)   AS clientes_recompra
FROM n_prod_canal GROUP BY 1, 2, 3, 4
UNION ALL
SELECT anio, 'canal', categoria, -1,
       COUNT(*), COUNT(*) FILTER (WHERE veces >= 2)
FROM n_canal GROUP BY 1, 2, 3, 4
UNION ALL
SELECT anio, 'producto', '(todas)', componente_id,
       COUNT(*), COUNT(*) FILTER (WHERE veces >= 2)
FROM n_prod GROUP BY 1, 2, 3, 4
UNION ALL
SELECT anio, 'total', '(todas)', -1,
       COUNT(*), COUNT(*) FILTER (WHERE veces >= 2)
FROM n_total GROUP BY 1, 2, 3, 4;

CREATE UNIQUE INDEX ux_mv_ventas_recompra
    ON marts.mv_ventas_recompra (anio, nivel, categoria, componente_id);

CREATE INDEX ix_mv_recompra_nivel ON marts.mv_ventas_recompra (nivel, anio);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_recompra IS
  'Tasa de recompra = clientes_recompra / clientes, donde recompra es >=2 facturas '
  'distintas en el anio. SIEMPRE filtrar por `nivel` (producto_canal | canal | '
  'producto | total): los niveles NO se suman entre si. No lleva empresa a proposito.';


-- ============================================================================
-- SIGUIENTE PASO OBLIGATORIO: re-ejecutar 24_rol_intranet.sql
--   · concede SELECT sobre las 3 MV y las 2 semillas de este archivo,
--   · y redefine v_lk_producto (con linea/linea_categoria) y v_lk_tercero (zona).
-- ============================================================================
