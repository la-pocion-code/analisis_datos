-- ============================================================================
-- 32_iva_ventas.sql — el IMPUESTO de la línea de venta en el hecho.
-- Archivo: sql/marts/32_iva_ventas.sql. Idempotente (ADD COLUMN IF NOT EXISTS).
--
-- POR QUÉ: `fact.venta_neta` (= credito − debito de la línea de ingreso, clase 4) es el valor
-- SIN impuestos, porque en ese renglón el IVA no está: vive en OTRAS líneas del MISMO asiento.
-- Anatomía real de una factura (linea_id 14746722-27, jul-2026):
--
--   clase 4  41353801 VENTA DE COSMETICOS GRAVADO 19%   4 líneas de producto   166.890  ← base
--   clase 2  24080101 IVA GENERADO EN VENTAS 19%        el impuesto             31.709  ← IVA
--   clase 1  13050501 CLIENTES NACIONALES               la CxC = total          198.600  ← con IVA
--
-- ⚠⚠⚠ `total_con_impuesto` (= `price_total` de Odoo) **NO ES LA VENTA CON IVA** ⚠⚠⚠
-- Es base + IVA − **RETENCIONES**, o sea el **valor A COBRAR** (la CxC). Medido en el asiento
-- 82410 (NCR69): base 466.891 + IVA 88.709 (19,0 %) − retefuente 11.672 (2,5 %) = 543.928, que
-- es exactamente su `price_total` y exactamente su línea de `Clientes Nacionales`. Usarlo como
-- "venta con IVA" daba un factor de **1,1650** (= 1 + 0,19 − 0,025) en 5.555 líneas por **6.024
-- millones**, y 1,1550 donde la retención es del 3,5 %: subestimaba la venta, porque la retención
-- es un anticipo de NUESTRO impuesto de renta que el cliente consigna por nosotros y no reduce
-- la venta.
-- ⇒ La **venta con IVA** se calcula en `marts.v_iva_asiento` (sql/marts/14_ventas.sql) a partir
--   de las líneas de IVA del propio asiento (cuentas **2408**), que están en COP.
-- ⇒ Estas dos columnas se conservan porque responden bien a **otra** pregunta ("cuánto va a pagar
--   el cliente") y porque `moneda` es la salvaguarda de la trampa de abajo. No las usa ninguna
--   vista de ventas.
--
-- ⚠⚠⚠ LA MONEDA — LO MÁS IMPORTANTE DE ESTE ARCHIVO ⚠⚠⚠
-- `subtotal` y `total_con_impuesto` vienen de Odoo EN LA MONEDA DE LA FACTURA, no en pesos.
-- `debito`/`credito`/`saldo`/`venta_neta` sí están en COP (moneda de la compañía). Medido:
--
--   · razón `subtotal / venta_neta` = EXACTAMENTE 1,0000 (min = max) en GRAVADO y EXCLUIDO
--   · razón `subtotal / venta_neta` = 0,0003 en EXPORTACION  ⇒ otra moneda
--   · las exportaciones son 100 % USD (40 de 40 líneas muestreadas): `price_subtotal` = 500,00
--     USD mientras el `balance` de la misma línea es −1.851.640 COP
--
-- ⇒ **NUNCA SUMAR `subtotal` NI `total_con_impuesto`**: mezclaría dólares con pesos y
--   subestimaría la exportación ~3.900× (son 1.708.605.877 COP en `v_ventas_producto` 2026).
--
-- ⇒ LO ÚNICO SEGURO ES SU RAZÓN. `total_con_impuesto / subtotal` es un FACTOR ADIMENSIONAL
--   (una razón entre dos importes de la misma moneda no tiene moneda), así que:
--
--        venta_con_iva (COP) = venta_neta (COP) × (total_con_impuesto / subtotal)
--
--   sale en pesos sin conversión ni TRM. Validado contra Odoo: el factor es exactamente
--   1,19000 en las líneas gravadas y 1,00000 en las de exportación (la exportación no lleva
--   IVA, así que su valor con IVA es igual al de sin IVA — en COP).
--   Eso es lo que hacen `v_ventas_producto.venta_subtotal_con_iva` (14_ventas.sql) y
--   `v_ventas_explotada.venta_componente_con_iva` (15b_kits.sql).
--
-- `moneda` se guarda para que esa trampa sea VERIFICABLE desde SQL y no dependa de que alguien
-- lea este comentario.
--
-- ⚠ `precio_unitario` (= `price_unit` de Odoo) INCLUYE IVA en este Odoo: 58.900 contra un
-- `subtotal` de 49.496, con `discount = 0`. Es lo que hace que `precio_unitario × cantidad`
-- NO dé `venta_neta`. No es un descuento: es el precio con impuesto.
--
-- ⚠ ALCANCE DEL RELLENO: solo se puebla en las líneas que consumen las vistas de ventas
-- (`es_venta` + clase 4) = 585.541 de las 4.414.170 del hecho. En el resto (impuesto, CxC,
-- asientos) Odoo devuelve `price_total = 0`; rellenarlas serían 3,8 M de UPDATEs para guardar
-- ceros. Quedan NULL A PROPÓSITO. Ver `backfill_total_con_impuesto` en etl_dw_marts.py.
--
-- ORDEN: este archivo solo hace ALTER. Después hay que re-crear las vistas que exponen la
-- medida (14 → 15b → 21) y, como de ellas cuelgan MV, seguir con 23 → 27 → 24 y refrescar.
-- La secuencia completa está en el comentario de cabecera de 14_ventas.sql.
-- ============================================================================

ALTER TABLE marts.fact_movimiento_contable
    ADD COLUMN IF NOT EXISTS total_con_impuesto NUMERIC,
    ADD COLUMN IF NOT EXISTS moneda             VARCHAR(8);

COMMENT ON COLUMN marts.fact_movimiento_contable.total_con_impuesto IS
  'price_total de Odoo: total de la linea CON impuestos. ⚠ EN LA MONEDA DE LA FACTURA (USD en '
  'exportacion), NO en COP: NUNCA sumarlo. Solo es seguro como razon contra `subtotal` '
  '(total_con_impuesto/subtotal = factor adimensional, 1,19 gravado / 1,00 exento). NULL fuera '
  'de las lineas de venta clase 4 (a proposito: alli Odoo devuelve 0).';

COMMENT ON COLUMN marts.fact_movimiento_contable.moneda IS
  'Moneda del documento (COP/USD), de account.move.line.currency_id. Existe para poder VERIFICAR '
  'en SQL que `subtotal` y `total_con_impuesto` no estan en pesos. Las exportaciones son USD.';

COMMENT ON COLUMN marts.fact_movimiento_contable.subtotal IS
  'price_subtotal de Odoo: subtotal de la linea SIN impuestos. ⚠ EN LA MONEDA DE LA FACTURA, NO '
  'en COP (medido: subtotal/venta_neta = 1,0000 nacional y 0,0003 en exportacion, que es USD). '
  'Para valor en pesos usar `venta_neta`. Ver el comentario de `total_con_impuesto`.';

COMMENT ON COLUMN marts.fact_movimiento_contable.precio_unitario IS
  'price_unit de Odoo. ⚠ INCLUYE IVA (medido: 58.900 con un subtotal de 49.496 y discount=0) y '
  'esta en la MONEDA DE LA FACTURA. Por eso precio_unitario*cantidad NO da venta_neta.';
