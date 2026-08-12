-- ============================================================================
-- 34_compras.sql — el hecho de COMPRAS: impuestos del asiento, vista a grano de línea,
-- dimensión de ORDEN DE COMPRA y el enlace desde el hecho.
-- Archivo: sql/marts/34_compras.sql. Idempotente (DROP+CREATE / ADD COLUMN IF NOT EXISTS).
--
-- Referencia del negocio: el informe de Odoo *Contabilidad > reportes > asientos compras con OC*.
-- TODO lo de estos comentarios está MEDIDO contra la base y la API, no supuesto.
--
-- ── QUÉ ES UNA COMPRA AQUÍ ───────────────────────────────────────────────────────────
-- Documentos de proveedor: `in_invoice` (71.850 líneas / 17.875 docs) y `in_refund` (2.526 / 642),
-- desde 2024-06-01 (el DW no tiene nada antes). De ahí, 31.820 líneas de BASE.
--
-- ⚠⚠ LA BASE DE UNA COMPRA NO ESTÁ EN UNA SOLA CLASE, al contrario que ventas (todo clase 4):
--
--   grupo 14 inventarios .......... 69.422 M
--   grupo 52/53/51 gastos ......... 39.337 M
--   grupo 61 costo ................  1.262 M
--   grupo 15/16/17 activos fijos ..  3.813 M      ← es INVERSIÓN, no compra recurrente
--   otros (13, 42…) ...............    ~2 M
--   ───────────────────────────────────────
--   TOTAL BASE ................... 114.917.171.112
--
-- Decisión de negocio (William): **cuenta todo lo que llega en un documento de proveedor**, y se
-- expone `clase_codigo`/`grupo_codigo` para poder separar en el tablero (p. ej. dejar fuera los
-- activos fijos, o mirar solo inventario). Por eso la base = TODO lo que no sea CxP, IVA ni
-- retención, y no una lista blanca de cuentas: una cuenta nueva de gasto entra sola.
--
-- ── LAS TRES LECTURAS DEL MISMO DINERO (espejo de ventas, todas en COP) ──────────────
--   compra_subtotal ........... base, SIN impuestos            ← la medida de COMPRAS
--   compra_subtotal_con_iva ... base + IVA (lo que dice la factura)
--   compra_total_pagado ....... base + IVA − retenciones       = lo que se le PAGA al proveedor
-- ⚠⚠ NO se suman entre sí: es el mismo dinero leído de tres formas.
--
-- La retención es plata que le retenemos al proveedor y consignamos a la DIAN por él: reduce lo que
-- le pagamos, pero NO reduce lo que compramos. Por eso `compra_subtotal` es la medida de compras y
-- `compra_total_pagado` responde a otra pregunta ("cuánto sale de caja").
--
-- Cuentas medidas (a 4 dígitos, no supuestas):
--   IVA descontable ...... 2408                                          17.875.249.985
--   retenciones .......... 2365 retefuente · 2367 reteIVA · 2368 reteICA  −5.719.901.655
--   CxP proveedores ...... 2205 nacionales · 2210 exterior              −126.975.945.414
--
-- Cuadre: base + IVA − retenciones = 127.072.519.442 vs CxP 126.975.945.414.
-- ⚠ **18.509 de 18.517 documentos cuadran AL PESO (99,957 %)**. Los 8 que no concentran los
-- 96.574.028 (0,084 % de la base) y NO es un fallo del modelo: son facturas de compra que además
-- llevan movimientos de inventario dentro del mismo documento (`143510 INVENTARIO EN
-- TRANSFORMACION` 83,7 M, `146535 INVENTARIO EN TRANSITO` 73,7 M, `613538 COSTO DE VENTAS Kids`
-- 13,3 M), así que su base excede lo que se le paga al proveedor. Se AÍSLAN en
-- `v_compras_descuadre` para que contabilidad los revise, igual que `v_nc_sin_asignar` en ventas.
-- 4 documentos más no tienen línea de CxP (no se paga a proveedor) y esos sí cuadran.
--
-- ⚠⚠ LA MONEDA: no se usan `subtotal`, `total_con_impuesto` ni `precio_unitario` del hecho — vienen
-- en la moneda del DOCUMENTO (ver 32_iva_ventas.sql) y hay compras al exterior (cuenta 2210,
-- 7.920 M). Todo sale de `debito`/`credito`, que están en COP.
--
-- ⚠ `in_refund` (nota crédito de proveedor = devolución) RESTA, no se excluye: `debito − credito`
-- ya le da signo negativo, y la cantidad se niega explícitamente. Mismo criterio que ventas.
--
-- ── LA ORDEN DE COMPRA ───────────────────────────────────────────────────────────────
-- ⚠⚠ **Solo el 14,9 % de las líneas de base tiene OC** (4.748 de 31.820). Por eso el informe de
-- Odoo se llama «con OC»: el 85 % del gasto se contabiliza SIN orden de compra. El hecho cubre
-- TODAS las compras y `tiene_oc` marca el subconjunto; cualquier KPI de OC (Lead Time, total OC)
-- habla de ese 15 % y el tablero debe decirlo.
--
-- ORDEN: aplicar después de 01..13. Luego 35_compras_dashboards.sql y re-ejecutar
-- 24_rol_intranet.sql (los GRANT). Poblar con `python etl_dw_marts.py --dims` (trae las OC) y
-- `--backfill-compras` (enlaza las líneas ya cargadas).
-- ============================================================================

-- ── El enlace desde el hecho a la OC (columnas degeneradas, patrón de vendedor_id) ───
ALTER TABLE marts.fact_movimiento_contable
    ADD COLUMN IF NOT EXISTS orden_compra_id BIGINT,
    ADD COLUMN IF NOT EXISTS oc_linea_id     BIGINT;

COMMENT ON COLUMN marts.fact_movimiento_contable.orden_compra_id IS
  'purchase.order de la linea, resuelto desde account.move.line.purchase_line_id. NULL en el 85 % '
  'de las lineas de compra (se contabilizan sin OC) y en todo lo que no es compra.';
COMMENT ON COLUMN marts.fact_movimiento_contable.oc_linea_id IS
  'purchase.order.line (account.move.line.purchase_line_id). ⚠ MUCHOS A UNO: 4.748 lineas del '
  'hecho apuntan a 2.215 lineas de OC (facturacion parcial), asi que al comparar cantidad pedida '
  'contra facturada hay que AGREGAR el hecho primero.';

CREATE INDEX IF NOT EXISTS ix_fact_orden_compra
    ON marts.fact_movimiento_contable (orden_compra_id) WHERE orden_compra_id IS NOT NULL;


-- ── dim_orden_compra ────────────────────────────────────────────────────────────────
-- De purchase.order (1.066 filas). Refresco TOTAL en cada corrida: son pocas y así se reflejan
-- las canceladas y las borradas.
CREATE TABLE IF NOT EXISTS marts.dim_orden_compra (
    orden_compra_id  BIGINT PRIMARY KEY,
    numero           TEXT,
    estado           TEXT,          -- draft / sent / purchase / done / cancel
    estado_factura   TEXT,          -- invoice_status: no / to invoice / invoiced
    proveedor_id     BIGINT,
    proveedor        TEXT,
    comprador        TEXT,          -- user_id: quién la hizo
    empresa_id       BIGINT,
    fecha_orden      DATE,          -- date_order    (100 % poblado)
    fecha_aprobacion DATE,          -- date_approve  (98 %)
    fecha_prevista   DATE,          -- date_planned  (100 %) llegada PREVISTA
    fecha_llegada    DATE,          -- effective_date (81 %) llegada REAL
    monto_sin_iva    NUMERIC,
    monto_iva        NUMERIC,
    monto_total      NUMERIC,
    moneda           VARCHAR(8),
    lead_time_dias   INTEGER,
    _loaded_at       TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota')
);

COMMENT ON TABLE marts.dim_orden_compra IS
  'Ordenes de compra (purchase.order). 1.066 filas: 925 confirmadas, 135 canceladas, 5 borrador, '
  '1 enviada. ⚠ Para "total OC realizadas" EXCLUIR las canceladas.';
COMMENT ON COLUMN marts.dim_orden_compra.lead_time_dias IS
  'fecha_llegada - fecha_aprobacion: dias entre confirmar la OC y que la mercancia llegue de '
  'verdad (definicion elegida por William). ⚠ NULL cuando falta alguna de las dos: poblado en 859 '
  'de 1.066 (81 %), porque effective_date viene vacio en 207. NO se rellena con la fecha prevista '
  'ni con hoy: eso inventaria un lead time. Medido: mediana 13 dias, media 21,9, max 208, y CERO '
  'negativos.';

CREATE INDEX IF NOT EXISTS ix_dim_oc_proveedor ON marts.dim_orden_compra (proveedor_id);
CREATE INDEX IF NOT EXISTS ix_dim_oc_fecha     ON marts.dim_orden_compra (fecha_orden);


-- ── dim_oc_linea ────────────────────────────────────────────────────────────────────
-- De purchase.order.line (2.215 filas). Sirve para comparar PEDIDO vs FACTURADO.
CREATE TABLE IF NOT EXISTS marts.dim_oc_linea (
    oc_linea_id      BIGINT PRIMARY KEY,
    orden_compra_id  BIGINT,
    producto_id      BIGINT,
    cantidad_pedida  NUMERIC,
    cantidad_recibida NUMERIC,
    cantidad_facturada NUMERIC,
    _loaded_at       TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota')
);

COMMENT ON TABLE marts.dim_oc_linea IS
  'Lineas de orden de compra. cantidad_pedida/recibida/facturada vienen de Odoo '
  '(product_qty / qty_received / qty_invoiced): permiten ver el cumplimiento sin recalcularlo.';

CREATE INDEX IF NOT EXISTS ix_dim_oc_linea_orden ON marts.dim_oc_linea (orden_compra_id);


-- ════════════════════════════════════════════════════════════════════════════
-- v_impuestos_compra — por DOCUMENTO de proveedor: base, IVA, retenciones y CxP.
-- Espejo de v_impuestos_asiento (ventas). TODO en COP: sale de debito/credito.
--
-- La base es "todo lo que no es CxP, IVA ni retencion" y NO una lista blanca de cuentas: asi una
-- cuenta de gasto nueva entra sola, sin tocar este archivo. Medido: en los documentos de proveedor
-- los unicos grupos de clase 2 que aparecen son 22, 23 y 24.
-- ════════════════════════════════════════════════════════════════════════════
DROP VIEW IF EXISTS marts.v_compras_bi;
DROP VIEW IF EXISTS marts.v_compras_producto;
DROP VIEW IF EXISTS marts.v_compras_descuadre;
DROP VIEW IF EXISTS marts.v_impuestos_compra;

CREATE VIEW marts.v_impuestos_compra AS
SELECT f.factura_id,
       SUM(CASE WHEN c.grupo_codigo NOT IN ('22','23','24') THEN f.debito - f.credito ELSE 0 END)
                                                                       AS base_asiento,
       SUM(CASE WHEN c.codigo LIKE '2408%' THEN f.debito - f.credito ELSE 0 END)
                                                                       AS iva_asiento,
       -- negativo: la retencion reduce lo que se paga (2365 retefuente, 2367 reteIVA, 2368 reteICA)
       SUM(CASE WHEN c.grupo_codigo = '23' THEN f.debito - f.credito ELSE 0 END)
                                                                       AS retencion_asiento,
       -- lo que queda por pagar al proveedor (2205 nacionales, 2210 exterior)
       SUM(CASE WHEN c.grupo_codigo = '22' THEN f.credito - f.debito ELSE 0 END)
                                                                       AS total_asiento
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.tipo_movimiento IN ('in_invoice', 'in_refund')
GROUP BY 1;

COMMENT ON VIEW marts.v_impuestos_compra IS
  'Por documento de proveedor: base (todo lo que no es 22/23/24), IVA (2408), retenciones (23xx, '
  'en negativo) y total a pagar (22xx). TODO EN COP. De aqui salen los factores de las tres '
  'lecturas de v_compras_producto.';


-- ════════════════════════════════════════════════════════════════════════════
-- v_compras_descuadre — los documentos donde base + IVA − retenciones ≠ CxP.
-- Se aislan en vez de esconderlos (patron de v_nc_sin_asignar / v_notas_debito_excluidas).
-- Medido: 8 de 18.517 documentos, 96.574.028 en total, todos por llevar movimientos de INVENTARIO
-- dentro de la propia factura de compra.
-- ════════════════════════════════════════════════════════════════════════════
CREATE VIEW marts.v_compras_descuadre AS
SELECT iv.factura_id,
       max(f.numero)         AS numero,
       max(f.fecha)          AS fecha,
       max(f.empresa_id)     AS empresa_id,
       max(t.nombre)         AS proveedor,
       iv.base_asiento, iv.iva_asiento, iv.retencion_asiento, iv.total_asiento,
       (iv.base_asiento + iv.iva_asiento + iv.retencion_asiento - iv.total_asiento) AS descuadre
FROM marts.v_impuestos_compra iv
JOIN marts.fact_movimiento_contable f ON f.factura_id = iv.factura_id
LEFT JOIN marts.dim_tercero t ON t.tercero_id = f.tercero_id
WHERE abs(iv.base_asiento + iv.iva_asiento + iv.retencion_asiento - iv.total_asiento) >= 1
  AND iv.total_asiento <> 0            -- los que no tienen CxP no se pagan a proveedor: no descuadran
GROUP BY iv.factura_id, iv.base_asiento, iv.iva_asiento, iv.retencion_asiento, iv.total_asiento;

COMMENT ON VIEW marts.v_compras_descuadre IS
  'Documentos de proveedor donde base + IVA - retenciones no da la CxP. Medido: 8 de 18.517 '
  '(99,957 % cuadra al peso), 96.574.028 en total, por llevar movimientos de inventario '
  '(143510 transformacion, 146535 transito, 613538 costo) dentro de la factura de compra. '
  'Para revisar en contabilidad, no para reportar.';


-- ════════════════════════════════════════════════════════════════════════════
-- v_compras_producto — grano LÍNEA DE BASE de un documento de proveedor.
-- Excluye CxP, IVA y retenciones (esas no son "lo comprado", son la contrapartida).
-- ════════════════════════════════════════════════════════════════════════════
CREATE VIEW marts.v_compras_producto AS
SELECT
    f.linea_id,
    f.factura_id,
    f.numero                AS numero_factura,
    f.referencia,
    f.tipo_movimiento,                                    -- in_invoice / in_refund
    (f.tipo_movimiento = 'in_refund')  AS es_devolucion,
    f.empresa_id,
    e.nombre                AS empresa_nombre,
    -- fechas: la CONTABLE es la de la compra (no hay aquí el enredo de la NC de ventas)
    f.fecha,
    f.fecha_factura,
    d.anio, d.mes, d.mes_nombre, d.periodo_aaaamm,
    -- proveedor
    f.tercero_id            AS proveedor_id,
    t.nombre                AS proveedor,
    t.ciudad, t.departamento, t.pais,
    -- producto (⚠ 12 % de las líneas de base NO tienen: servicios, arriendos, honorarios…)
    f.producto_id,
    p.codigo                AS producto_codigo,
    p.nombre                AS producto,
    p.categoria             AS producto_categoria,
    p.codigo_barras,
    -- naturaleza del gasto: permite separar inventario / gasto / activo fijo en el tablero
    c.clase_codigo, c.grupo_codigo,
    c.codigo                AS cuenta_codigo,
    c.nombre                AS cuenta_nombre,
    f.centro_costo_id,
    -- ── ORDEN DE COMPRA (⚠ solo el 14,9 % de las líneas la tiene) ──
    (f.orden_compra_id IS NOT NULL) AS tiene_oc,
    f.orden_compra_id,
    f.oc_linea_id,
    oc.numero               AS oc_numero,
    oc.estado               AS oc_estado,
    oc.fecha_orden          AS oc_fecha_orden,
    oc.fecha_aprobacion     AS oc_fecha_aprobacion,
    oc.fecha_prevista       AS oc_fecha_prevista,
    oc.fecha_llegada        AS oc_fecha_llegada,
    oc.lead_time_dias       AS oc_lead_time_dias,
    oc.comprador            AS oc_comprador,
    -- ── MEDIDAS: las tres lecturas, TODAS EN COP. ⚠ NO se suman entre sí ──
    (CASE WHEN f.tipo_movimiento = 'in_refund' THEN -f.cantidad ELSE f.cantidad END)
                                                          AS cantidad_neta,
    (f.debito - f.credito)                                AS compra_subtotal,
    (f.debito - f.credito)
        * (1 + iv.iva_asiento / NULLIF(iv.base_asiento, 0))            AS compra_subtotal_con_iva,
    (f.debito - f.credito)
        * (iv.total_asiento / NULLIF(iv.base_asiento, 0))              AS compra_total_pagado
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta   c ON c.cuenta_id = f.cuenta_id
JOIN marts.dim_fecha    d ON d.fecha_key = f.fecha_key
LEFT JOIN marts.dim_tercero  t ON t.tercero_id  = f.tercero_id
LEFT JOIN marts.dim_producto p ON p.producto_id = f.producto_id
LEFT JOIN marts.dim_empresa  e ON e.empresa_id  = f.empresa_id
LEFT JOIN marts.dim_orden_compra oc ON oc.orden_compra_id = f.orden_compra_id
LEFT JOIN marts.v_impuestos_compra iv ON iv.factura_id = f.factura_id
WHERE f.tipo_movimiento IN ('in_invoice', 'in_refund')
  AND c.grupo_codigo NOT IN ('22', '23', '24');

COMMENT ON VIEW marts.v_compras_producto IS
  'Compras a grano de linea de base (sin CxP/IVA/retencion). TRES lecturas en COP que NO se suman '
  'entre si: compra_subtotal (base) / compra_subtotal_con_iva / compra_total_pagado. in_refund '
  'resta. ⚠ tiene_oc solo en 14,9 % de las lineas. ⚠ 12 % sin producto (servicios, arriendos).';


-- ── v_compras_bi: version estrecha para los tableros (patron de v_ventas_bi) ─────────
CREATE VIEW marts.v_compras_bi AS
SELECT linea_id, factura_id, numero_factura, tipo_movimiento, es_devolucion,
       fecha, periodo_aaaamm, anio, mes,
       empresa_id, proveedor_id, producto_id, producto_categoria,
       clase_codigo, grupo_codigo,
       tiene_oc, orden_compra_id, oc_lead_time_dias,
       cantidad_neta, compra_subtotal, compra_subtotal_con_iva, compra_total_pagado
FROM marts.v_compras_producto;

COMMENT ON VIEW marts.v_compras_bi IS
  'Compras para tableros. El valor SIEMPRE con compra_subtotal (o _con_iva / _total_pagado); las '
  'tres estan en COP y NO se suman entre si.';
