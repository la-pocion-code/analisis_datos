-- ============================================================================
-- 37_devoluciones_dashboards.sql — DEVOLUCIONES (notas crédito de venta) para la intranet
--
-- ⚠ Re-ejecutar `24_rol_intranet.sql` DESPUÉS de este archivo: el `GRANT` a `intranet_ro` se
--   pierde al recrear la MV (DROP + CREATE). Y añadir la MV a `MVS_VENTAS` en
--   `refrescar_mv_dashboards.py`: sale del HECHO, que cambia en cada tick de 15 min.
--
-- POR QUÉ EXISTE
-- --------------
-- Hasta el 2026-09-08 **ningún** objeto concedido a `intranet_ro` contenía devoluciones: revisados
-- los 44 `GRANT` del archivo 24, cero coincidencias de `devol|nota_credito|refund`. La intranet solo
-- hace `SELECT` sobre esa whitelist, así que no había forma de responder «cuántas devoluciones tuvo
-- Shopify en agosto» — no por falta de prompt, sino por falta de fuente.
--
-- QUÉ ES UNA DEVOLUCIÓN AQUÍ
-- Una nota crédito de venta: `account.move` de tipo `out_refund`, que en el hecho es
-- `tipo_movimiento = 'out_refund'`. El canal está en `fact.categoria`.
--
-- ⚠⚠⚠ LAS TRES TRAMPAS. Están en los COMMENT de cada columna, pero se repiten aquí porque son la
-- diferencia entre un informe correcto y uno que cuenta el mismo dinero dos veces:
--
-- 1. **`venta` YA ES NETA DE DEVOLUCIONES. Nunca restar estas cifras de `venta`.** Dos mecanismos:
--    · Anulación total (`es_reverso`): `v_ventas_producto` excluye la factura **y** su nota crédito,
--      así que esa venta simplemente no está. En Shopify domina este caso — 12 de las 13
--      devoluciones de agosto de 2026 son anulación completa del pedido, no parcial.
--    · Nota crédito enlazada por el puente `map_nc_factura`: resta dentro de `venta` en el mes de
--      **su factura original** (`fecha_venta`), no en el mes de la nota crédito.
--    ⇒ Esta MV es **INFORMATIVA** (cuántas y cuánto), NO un ajuste a aplicar.
--
-- 2. **LA FECHA.** La devolución se cuenta por su PROPIA fecha (la `fecha_factura` de la nota
--    crédito), que es lo que el negocio entiende por «devoluciones del mes». Pero `mv_ventas_mes`
--    atribuye `venta` por `fecha_venta`. ⇒ Para la TASA, numerador y denominador tienen que estar en
--    la MISMA base, y por eso `facturado_con_iva` se calcula **aquí** por `fecha_factura` en vez de
--    dejar que el consumidor lo tome de otra MV. Con bases distintas la tasa miente.
--
-- 3. **LA NOTA CRÉDITO NO TRAE EL `#` DEL PEDIDO DE SHOPIFY**: su `ref` es
--    `'Reversión de: FE51933, CLIENTE SE RETRACTA DE LA COMPRA'`. Se puede atribuir al canal y al
--    cliente, **no al pedido**. No presentarla como «pedido devuelto» con número de pedido.
--
-- ⚠ El IVA sale del ASIENTO (clase 4 + cuenta 2408), **no de un factor cableado**: en gravado da
--   1,19 pero en EXPORTACION es 1,00 (ver `32_iva_ventas.sql`).
-- ⚠ Los importes se guardan **EN POSITIVO** aunque en el hecho sean negativos: una columna llamada
--   `devuelto` que viene negativa invita a sumarla a `venta` «para restar», que es justo el error.
--
-- IDEMPOTENCIA: no existe CREATE OR REPLACE MATERIALIZED VIEW ⇒ DROP + CREATE. Re-ejecutable.
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_devoluciones_mes;

CREATE MATERIALIZED VIEW marts.mv_ventas_devoluciones_mes AS
WITH
-- Factor de IVA de CADA documento, medido de su propio asiento. Se calcula sobre TODOS los
-- documentos de venta (facturas y notas crédito) porque hace falta para las dos mitades.
doc AS (
    SELECT f.factura_id,
           max(f.tipo_movimiento)                                            AS tipo,
           max(f.es_reverso::int)                                            AS es_reverso,
           max(f.empresa_id)                                                 AS empresa_id,
           max(f.categoria)                                                  AS categoria,
           max(f.fecha_factura)                                              AS fecha_factura,
           sum(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)  AS base,
           sum(CASE WHEN c.codigo LIKE '2408%' THEN f.credito - f.debito
                    ELSE 0 END)                                              AS iva,
           sum(CASE WHEN c.clase_codigo = '4' THEN f.cantidad ELSE 0 END)    AS unidades
    FROM marts.fact_movimiento_contable f
    JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
    WHERE f.es_venta
      AND f.fecha_factura IS NOT NULL
    GROUP BY f.factura_id
),
-- Las devoluciones, por SU propia fecha.
dev AS (
    SELECT coalesce(empresa_id, -1)                                  AS empresa_id,
           (extract(year FROM fecha_factura) * 100
            + extract(month FROM fecha_factura))::int                AS periodo_aaaamm,
           coalesce(categoria, '(sin categoría)')                    AS categoria,
           count(*)                                                  AS documentos,
           count(*) FILTER (WHERE es_reverso = 1)                    AS documentos_anulacion,
           count(*) FILTER (WHERE es_reverso = 0)                    AS documentos_parcial,
           -- ⚠ `base` e `iva` vienen NEGATIVOS en el hecho (es una nota crédito) y se voltean;
           -- `cantidad`, en cambio, ya viene POSITIVA en la línea de la NC. Se usa abs() en las
           -- tres para que la MV no dependa del signo con que Odoo grabe cada campo: la primera
           -- versión negaba las unidades y salían en negativo.
           abs(sum(unidades))                                        AS unidades,
           abs(sum(base))                                            AS base,
           abs(sum(iva))                                             AS iva
    FROM doc
    WHERE tipo = 'out_refund'
    GROUP BY 1, 2, 3
),
-- El denominador de la tasa: lo FACTURADO en el mismo mes y en la MISMA base de fecha.
fac AS (
    SELECT coalesce(empresa_id, -1)                                  AS empresa_id,
           (extract(year FROM fecha_factura) * 100
            + extract(month FROM fecha_factura))::int                AS periodo_aaaamm,
           coalesce(categoria, '(sin categoría)')                    AS categoria,
           count(*)                                                  AS facturas,
           sum(base + iva)                                           AS facturado_con_iva
    FROM doc
    WHERE tipo = 'out_invoice'
    GROUP BY 1, 2, 3
)
SELECT
    d.empresa_id,
    d.periodo_aaaamm,
    (d.periodo_aaaamm / 100)::int                                    AS anio,
    (d.periodo_aaaamm % 100)::int                                    AS mes,
    d.categoria,
    d.documentos,
    d.documentos_anulacion,
    d.documentos_parcial,
    d.unidades,
    d.base,
    d.iva,
    d.base + d.iva                                                   AS con_iva,
    coalesce(f.facturas, 0)                                          AS facturas,
    coalesce(f.facturado_con_iva, 0)                                 AS facturado_con_iva
FROM dev d
LEFT JOIN fac f
       ON f.empresa_id = d.empresa_id
      AND f.periodo_aaaamm = d.periodo_aaaamm
      AND f.categoria = d.categoria;

-- El índice ÚNICO lo exige `REFRESH MATERIALIZED VIEW CONCURRENTLY`. Los textos van con COALESCE a
-- un centinela y los ids a -1 porque en un índice único los NULL se consideran distintos entre sí
-- y no garantizarían unicidad.
CREATE UNIQUE INDEX ux_mv_ventas_devoluciones_mes
    ON marts.mv_ventas_devoluciones_mes (periodo_aaaamm, empresa_id, categoria);

CREATE INDEX ix_mv_ventas_devoluciones_mes_cat
    ON marts.mv_ventas_devoluciones_mes (categoria, periodo_aaaamm);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_devoluciones_mes IS
  'DEVOLUCIONES (notas crédito de venta, out_refund) por empresa × mes × canal, contadas por la '
  'FECHA DE LA NOTA CRÉDITO. ⚠⚠ ES INFORMATIVA, NO UN AJUSTE: `venta` en mv_ventas_mes YA ES NETA '
  'de devoluciones, así que restar estas cifras de la venta cuenta el mismo dinero dos veces. En '
  'Shopify la mayoría son ANULACIONES COMPLETAS del pedido (12 de 13 en agosto de 2026), no '
  'devoluciones parciales. La nota crédito NO trae el # del pedido de Shopify: se atribuye al canal '
  'y al cliente, no al pedido. Medido 2026 en Shopify: tasa media 0,30 % (banda 0,19-0,42 %), '
  'máximo abril 9.486.570 en 56 NC, mínimo junio 1.700.430 en 9 NC.';

COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.periodo_aaaamm IS
  'Mes de la NOTA CRÉDITO (su propia fecha_factura), que es lo que el negocio entiende por '
  '«devoluciones del mes». ⚠ NO es la base de `venta` en mv_ventas_mes, que va por fecha_venta (la '
  'NC resta en el mes de SU factura original).';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.documentos IS
  'Notas crédito del mes. ⚠ Es un COUNT de documentos: NO es aditivo si se agrega por otra vía; '
  'sumar meses o canales da el total de documentos, pero no lo cruce con conteos de otra MV.';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.documentos_anulacion IS
  'De esos, los que ANULAN la factura completa (es_reverso). En estos el DW excluye de ventas la '
  'factura Y la nota crédito, así que la venta nunca existió: no hay nada que restar.';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.documentos_parcial IS
  'Los que NO son anulación completa. Estos sí restan dentro de `venta`, en el mes de su factura '
  'original (fecha_venta) vía el puente map_nc_factura.';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.base IS
  'Valor devuelto sin IVA, EN POSITIVO (en el hecho es negativo). Se guarda positivo a propósito: '
  'una columna negativa invita a sumarla a `venta` «para restar», que es justo el doble conteo.';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.iva IS
  'IVA devuelto, leído del ASIENTO (clase 4 + cuenta 2408) documento por documento. ⚠ NO es un '
  'factor cableado: en gravado sale 1,19 pero en EXPORTACION es 1,00 (ver 32_iva_ventas.sql).';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.facturado_con_iva IS
  'Lo FACTURADO en el mismo mes, canal y empresa, con IVA y por fecha_factura. Es el denominador '
  'de la tasa de devolución. ⚠ Se calcula AQUÍ y no se toma de otra MV justamente para que '
  'numerador y denominador estén en la MISMA base de fecha: con bases distintas la tasa miente.';
COMMENT ON COLUMN marts.mv_ventas_devoluciones_mes.facturas IS
  'Facturas emitidas en el mes/canal/empresa (out_invoice), para poder dar la tasa también por '
  'número de documentos y no solo por valor.';

-- ⚠⚠ CÓMO SE CONSUME (y los dos errores que produce hacerlo mal)
--
-- 1. **EL GRANO LLEVA EMPRESA.** Un canal puede facturar por las dos empresas (Shopify lo hace:
--    en febrero de 2026 salen 4.468 facturas por una y 491 por la otra), así que la vista del
--    CANAL exige **sumar las empresas**. Sin sumarlas, un mes aparece partido en dos filas y las
--    cifras se leen a la mitad. Ya sumadas, los conteos cuadran con lo medido: febrero 15 + 4 = 19
--    devoluciones, marzo 16 + 1 = 17, mayo 12 + 3 = 15.
--
-- 2. ⛔ **LA TASA NO ES UNA COLUMNA, Y ES A PROPÓSITO.** Una tasa no se suma ni se promedia entre
--    filas: hay que agregar primero y dividir después
--    (`SUM(con_iva) / SUM(facturado_con_iva)`). Si se guardara `tasa_pct` en la MV, promediar las
--    filas de un canal daría un número plausible y falso — en la empresa pequeña salen tasas del
--    20 % sobre 4 facturas, que al promediarse con la grande se comerían la realidad.
--
-- 3. ⚠ **`facturado_con_iva` PUEDE SER 0** y entonces la tasa **no es calculable**: hay meses con
--    notas crédito de facturas de otro mes y cero facturación propia (mayo de 2026 en una de las
--    empresas). El consumidor debe pintar un guion, nunca un 0 %.
--
-- 4. ⚠ **UN MES EN CURSO ESTÁ INCOMPLETO.** No compararlo con meses cerrados ni meterlo en
--    máximos, mínimos ni promedios.
