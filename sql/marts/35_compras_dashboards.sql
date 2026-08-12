-- ============================================================================
-- 35_compras_dashboards.sql — las MV de la hoja de COMPRAS de la intranet.
-- Archivo: sql/marts/35_compras_dashboards.sql. Idempotente (DROP + CREATE).
-- ⚠ Re-ejecutar `24_rol_intranet.sql` DESPUÉS: los GRANT a intranet_ro se pierden al recrear las MV.
--
-- Origen: v_compras_producto / v_compras_bi y dim_orden_compra (ver 34_compras.sql, que trae la
-- medición completa: qué cuenta como compra, las 3 lecturas del valor y por qué la OC solo cubre
-- el 14,9 %).
--
-- ── REGLAS DE USO (las mismas que ventas; el detalle en docs/dashboards_intranet.md) ──
-- · El valor SIEMPRE con `compra_subtotal` (base), `compra_subtotal_con_iva` o
--   `compra_total_pagado`. ⚠⚠ LAS TRES SON EL MISMO DINERO: no se suman entre sí.
-- · `documentos`, `proveedores`, `productos` y `ordenes` son COUNT(DISTINCT) ⇒ **NO son aditivos**:
--   viven en mv_compras_kpi_mes y no se pueden sumar entre meses ni entre empresas.
-- · ids nulos → -1 y textos → '(sin …)': lo exige el índice ÚNICO (NULL no compara igual a NULL,
--   así que una fila con NULL en el grano rompería REFRESH ... CONCURRENTLY).
--
-- ── LÍMITES DEL ORIGEN, medidos (hay que respetarlos al leer el tablero) ──────────────
-- · Las compras empiezan **2024-06-01** (el DW no tiene nada antes) ⇒ la variación vs año anterior
--   es limpia en **2026 vs 2025**; **2025 vs 2024 compara 12 meses contra 7**.
-- · **12 % de las líneas de base no tienen producto** (servicios, arriendos, honorarios): caen en
--   `'(sin producto)'`. No es un hueco de datos: un arriendo no es un producto.
-- · **Solo el 14,9 % de las líneas tiene OC.** Lead Time y total de OC hablan de ese subconjunto.
-- · Lead Time poblado en **859 de 1.066 OC (81 %)**: falta `effective_date` en 207. Y las 859 son
--   TODAS de estado `purchase`: las canceladas/borrador no tienen (nunca llegaron), así que el KPI
--   se acota solo.
-- ============================================================================

-- ════════════════════════════════════════════════════════════════════════════
-- mv_compras_mes — el grano de los desgloses y los top-N.
-- Responde: valor comprado, cantidad por producto (mes/año), % participación de proveedores,
-- categorías, top 10 proveedores, top 10 productos y variación vs año anterior.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_compras_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_compras_mes AS
SELECT
    date_trunc('month', v.fecha)::DATE                       AS fecha_mes,
    v.periodo_aaaamm,
    v.anio, v.mes,
    COALESCE(v.empresa_id, -1)                               AS empresa_id,
    COALESCE(v.proveedor_id, -1)                             AS proveedor_id,
    COALESCE(v.producto_id, -1)                              AS producto_id,
    COALESCE(NULLIF(btrim(v.producto_categoria), ''), '(sin categoria)') AS producto_categoria,
    -- naturaleza del gasto: permite separar inventario / gasto / activo fijo en el tablero
    COALESCE(NULLIF(btrim(v.clase_codigo), ''), '(sin clase)') AS clase_codigo,
    COALESCE(NULLIF(btrim(v.grupo_codigo), ''), '(sin grupo)') AS grupo_codigo,
    v.tiene_oc,
    -- medidas. ⚠ Las tres son el MISMO dinero: no se suman entre sí.
    SUM(v.compra_subtotal)                                   AS compra_subtotal,
    SUM(v.compra_subtotal_con_iva)                           AS compra_subtotal_con_iva,
    SUM(v.compra_total_pagado)                               AS compra_total_pagado,
    SUM(v.cantidad_neta)                                     AS cantidad,
    COUNT(*)                                                 AS lineas
FROM marts.v_compras_producto v
WHERE v.fecha IS NOT NULL
GROUP BY 1,2,3,4,5,6,7,8,9,10,11;

CREATE UNIQUE INDEX ux_mv_compras_mes ON marts.mv_compras_mes
    (periodo_aaaamm, empresa_id, proveedor_id, producto_id, producto_categoria,
     clase_codigo, grupo_codigo, tiene_oc);
CREATE INDEX ix_mv_compras_mes_anio      ON marts.mv_compras_mes (anio);
CREATE INDEX ix_mv_compras_mes_prov      ON marts.mv_compras_mes (proveedor_id);
CREATE INDEX ix_mv_compras_mes_prod      ON marts.mv_compras_mes (producto_id);
CREATE INDEX ix_mv_compras_mes_fecha     ON marts.mv_compras_mes (fecha_mes);

COMMENT ON MATERIALIZED VIEW marts.mv_compras_mes IS
  'Compras al grano mes x empresa x proveedor x producto x categoria x clase/grupo PUC. TRES '
  'medidas de valor que NO se suman entre si. `tiene_oc` esta en el grano para poder filtrar el '
  '15 % con orden de compra. Las compras arrancan 2024-06 => 2025 vs 2024 compara 12 meses con 7.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_compras_kpi_mes — los conteos DISTINTOS, que NO son aditivos.
-- Responde: total de proveedores usados, total de productos comprados, total de documentos y de OC.
-- Van aparte porque un COUNT(DISTINCT) no se puede sumar entre filas: el mismo proveedor aparece
-- en varios meses y sumarlo lo contaría dos veces.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_compras_kpi_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_compras_kpi_mes AS
SELECT
    date_trunc('month', v.fecha)::DATE            AS fecha_mes,
    v.periodo_aaaamm, v.anio, v.mes,
    COALESCE(v.empresa_id, -1)                    AS empresa_id,
    COUNT(DISTINCT v.factura_id)                  AS documentos,
    COUNT(DISTINCT v.proveedor_id)                AS proveedores,
    COUNT(DISTINCT v.producto_id)                 AS productos,
    COUNT(DISTINCT v.orden_compra_id)             AS ordenes_compra,
    COUNT(*)                                      AS lineas,
    SUM(v.compra_subtotal)                        AS compra_subtotal,
    SUM(v.compra_subtotal_con_iva)                AS compra_subtotal_con_iva,
    SUM(v.compra_total_pagado)                    AS compra_total_pagado
FROM marts.v_compras_producto v
WHERE v.fecha IS NOT NULL
GROUP BY 1,2,3,4,5;

CREATE UNIQUE INDEX ux_mv_compras_kpi_mes ON marts.mv_compras_kpi_mes (periodo_aaaamm, empresa_id);
CREATE INDEX ix_mv_compras_kpi_mes_anio ON marts.mv_compras_kpi_mes (anio);

COMMENT ON MATERIALIZED VIEW marts.mv_compras_kpi_mes IS
  'Conteos DISTINTOS de compras por mes x empresa. ⚠ NO ADITIVOS: no sumar `proveedores` ni '
  '`productos` entre meses ni entre empresas (el mismo proveedor aparece en varios y se contaria '
  'dos veces). Para el total de un periodo hay que recontar sobre v_compras_bi.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_compras_oc — grano ORDEN DE COMPRA.
-- Responde: Lead Time promedio, total de OC realizadas, estado y pedido vs facturado.
-- ⚠ Cubre las 1.066 OC, que son el 14,9 % de las líneas de compra. NO es "las compras".
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_compras_oc CASCADE;

CREATE MATERIALIZED VIEW marts.mv_compras_oc AS
WITH lin AS (   -- cantidades de la OC, agregadas por orden
    SELECT orden_compra_id,
           SUM(cantidad_pedida)    AS cantidad_pedida,
           SUM(cantidad_recibida)  AS cantidad_recibida,
           SUM(cantidad_facturada) AS cantidad_facturada,
           COUNT(*)                AS lineas_oc
    FROM marts.dim_oc_linea GROUP BY 1
),
fac AS (        -- lo que de verdad llegó a la contabilidad por esa OC
    SELECT orden_compra_id,
           SUM(compra_subtotal)     AS contabilizado_subtotal,
           SUM(cantidad_neta)       AS contabilizado_cantidad,
           COUNT(DISTINCT factura_id) AS documentos
    FROM marts.v_compras_producto WHERE orden_compra_id IS NOT NULL GROUP BY 1
)
SELECT
    oc.orden_compra_id,
    oc.numero,
    oc.estado,
    oc.estado_factura,
    -- ⭐ 'purchase' = confirmada. Para "total OC realizadas" filtrar por esto: hay 135 canceladas.
    (oc.estado = 'purchase')                     AS es_confirmada,
    COALESCE(oc.proveedor_id, -1)                AS proveedor_id,
    COALESCE(NULLIF(btrim(oc.proveedor), ''), '(sin proveedor)')  AS proveedor,
    COALESCE(NULLIF(btrim(oc.comprador), ''), '(sin comprador)')  AS comprador,
    COALESCE(oc.empresa_id, -1)                  AS empresa_id,
    oc.fecha_orden,
    date_trunc('month', oc.fecha_orden)::DATE    AS fecha_mes,
    EXTRACT(YEAR  FROM oc.fecha_orden)::SMALLINT AS anio,
    EXTRACT(MONTH FROM oc.fecha_orden)::SMALLINT AS mes,
    oc.fecha_aprobacion, oc.fecha_prevista, oc.fecha_llegada,
    -- ⚠ NULL en 207 de 1.066 (falta la llegada real). NO se rellena: promediar sobre NULL los
    -- excluye, que es lo correcto; rellenarlos con la fecha prevista inventaría un lead time.
    oc.lead_time_dias,
    -- puntualidad: + = llegó tarde respecto a lo prometido. Otra pregunta distinta al lead time.
    CASE WHEN oc.fecha_llegada IS NOT NULL AND oc.fecha_prevista IS NOT NULL
         THEN (oc.fecha_llegada - oc.fecha_prevista) END      AS dias_vs_prevista,
    oc.monto_sin_iva, oc.monto_total, oc.moneda,
    COALESCE(lin.lineas_oc, 0)                   AS lineas_oc,
    lin.cantidad_pedida, lin.cantidad_recibida, lin.cantidad_facturada,
    fac.contabilizado_subtotal, fac.contabilizado_cantidad,
    COALESCE(fac.documentos, 0)                  AS documentos_contables
FROM marts.dim_orden_compra oc
LEFT JOIN lin ON lin.orden_compra_id = oc.orden_compra_id
LEFT JOIN fac ON fac.orden_compra_id = oc.orden_compra_id;

CREATE UNIQUE INDEX ux_mv_compras_oc ON marts.mv_compras_oc (orden_compra_id);
CREATE INDEX ix_mv_compras_oc_prov  ON marts.mv_compras_oc (proveedor_id);
CREATE INDEX ix_mv_compras_oc_fecha ON marts.mv_compras_oc (fecha_mes);
CREATE INDEX ix_mv_compras_oc_anio  ON marts.mv_compras_oc (anio);

COMMENT ON MATERIALIZED VIEW marts.mv_compras_oc IS
  'Una fila por orden de compra (1.066). Lead Time = fecha_llegada - fecha_aprobacion, NULL en 207 '
  '(81 % poblado); las que lo tienen son todas confirmadas. ⚠ Para "total OC realizadas" filtrar '
  '`es_confirmada` (135 canceladas). ⚠ Las OC son el 14,9 % de las lineas de compra: esta MV NO '
  'es "las compras". `monto_*` viene de Odoo en la MONEDA DE LA OC; para valor en COP usar '
  '`contabilizado_subtotal`, que sale del hecho.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_compras_recompra — cada cuánto se compra y con qué frecuencia.
-- Responde: tiempo de recompra y frecuencia de compra, en los TRES ejes que pidió el negocio.
--
-- ⚠⚠ LLEVA COLUMNA `nivel` Y SUS NIVELES NO SE SUMAN, igual que mv_ventas_recompra: un
-- COUNT(DISTINCT documento) no se rueda hacia arriba. Comprar el producto A a un proveedor y el B
-- a otro son "1 compra" en el eje producto pero "2" en el eje proveedor. Se elige UN nivel.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_compras_recompra CASCADE;

CREATE MATERIALIZED VIEW marts.mv_compras_recompra AS
WITH base AS (   -- una fila por (eje, entidad, DÍA de compra): el grano de "una compra"
    SELECT 'proveedor'::text AS nivel,
           COALESCE(proveedor_id, -1) AS proveedor_id, -1::bigint AS producto_id,
           fecha, factura_id, compra_subtotal
    FROM marts.v_compras_producto WHERE fecha IS NOT NULL
    UNION ALL
    SELECT 'producto', -1::bigint, COALESCE(producto_id, -1), fecha, factura_id, compra_subtotal
    FROM marts.v_compras_producto WHERE fecha IS NOT NULL
    UNION ALL
    SELECT 'proveedor_producto', COALESCE(proveedor_id, -1), COALESCE(producto_id, -1),
           fecha, factura_id, compra_subtotal
    FROM marts.v_compras_producto WHERE fecha IS NOT NULL
),
compras AS (     -- deduplicado a nivel de DOCUMENTO: varias líneas del mismo documento son 1 compra
    SELECT nivel, proveedor_id, producto_id, factura_id,
           min(fecha) AS fecha, sum(compra_subtotal) AS valor
    FROM base GROUP BY 1,2,3,4
),
gaps AS (
    SELECT nivel, proveedor_id, producto_id, fecha, valor,
           fecha - LAG(fecha) OVER (PARTITION BY nivel, proveedor_id, producto_id
                                    ORDER BY fecha, factura_id) AS dias_desde_anterior
    FROM compras
)
SELECT nivel, proveedor_id, producto_id,
       COUNT(*)                                        AS compras,           -- frecuencia total
       MIN(fecha)                                      AS primera_compra,
       MAX(fecha)                                      AS ultima_compra,
       ROUND(AVG(dias_desde_anterior), 1)              AS dias_recompra_prom,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY dias_desde_anterior) AS dias_recompra_mediana,
       MAX(dias_desde_anterior)                        AS dias_recompra_max,
       -- días sin comprar a hoy: la señal de "se dejó de comprar". Se calcula en la intranet
       -- contra CURRENT_DATE, aquí solo va la última fecha (si no, quedaría congelado al refresco).
       SUM(valor)                                      AS compra_subtotal,
       -- frecuencia normalizada: compras por cada 30 días de vida de la relación
       CASE WHEN MAX(fecha) > MIN(fecha)
            THEN ROUND(COUNT(*)::numeric * 30 / (MAX(fecha) - MIN(fecha)), 2) END AS compras_por_mes
FROM gaps
GROUP BY 1,2,3;

CREATE UNIQUE INDEX ux_mv_compras_recompra
    ON marts.mv_compras_recompra (nivel, proveedor_id, producto_id);
CREATE INDEX ix_mv_compras_recompra_nivel ON marts.mv_compras_recompra (nivel);

COMMENT ON MATERIALIZED VIEW marts.mv_compras_recompra IS
  'Tiempo de recompra y frecuencia, en 3 ejes (nivel): proveedor / producto / proveedor_producto. '
  '⚠⚠ LOS NIVELES NO SE SUMAN: un COUNT(DISTINCT documento) no se rueda hacia arriba. Elegir UN '
  'nivel. "Una compra" = un DOCUMENTO (varias lineas del mismo documento cuentan una vez). '
  '`dias_recompra_prom` es NULL cuando solo hay una compra: no hay intervalo que medir, y eso NO '
  'es cero. Los dias sin comprar a hoy los calcula la intranet contra CURRENT_DATE.';
