-- ============================================================================
-- Ventas netas a grano de producto desde el hecho (reemplaza el diff de Excel).
-- Archivo: sql/marts/14_ventas.sql  (ejecutar DESPUÉS de 01..13). Idempotente.
--
-- "Ventas correctas" = líneas de INGRESO (clase 4) de facturas y notas crédito de venta, netas:
--   * es_venta (out_invoice/out_refund) y clase 4 (línea de producto/ingreso, sin impuestos).
--   * es_reverso IS NOT TRUE  → excluye reversos TOTALES (anulaciones); las devoluciones/rebates
--     PARCIALES netean vía venta_neta (crédito − débito) y cantidad_neta.
--   * producto comercial: default_code empieza por PCN/KD/TNG/B8 (incluye kits *KIT).
-- La NC ya está enlazada contablemente (no hace falta casar por ref+producto como en el Excel).
--
-- Medidas: venta_neta (subtotal, SIN impuestos) y cantidad_neta (NC en negativo).
-- Grano: línea del hecho (una por factura×producto×línea). Agregar en BI por lo que se necesite.
-- ============================================================================

-- Se recrean (no CREATE OR REPLACE): la lista de columnas cambia al exponer `equipo`.
-- v_ventas_explotada depende de esta vista y se vuelve a crear en 15b_kits.sql.
-- Orden importa: v_ventas_explotada y v_precio_componente dependen de v_ventas_producto.
-- Ambas se vuelven a crear en 15b_kits.sql (aplicar 14 y luego 15b).
-- v_ventas_bi (21_ventas_bi.sql) cuelga de v_ventas_explotada: hay que soltarla primero o el DROP
-- falla ("other objects depend on it").
-- ⚠ ORDEN OBLIGATORIO al reaplicar: primero DROPear las MV de dashboards (mv_ventas_dia/mes/kpi_mes,
-- que cuelgan de v_ventas_bi; si no, este DROP falla), luego 14 → 15b → 21 → 23 → 24 (los GRANT a
-- intranet_ro se pierden al recrear las MV) y por último `python refrescar_mv_dashboards.py`.
DROP VIEW IF EXISTS marts.v_ventas_bi;
DROP VIEW IF EXISTS marts.v_ventas_explotada;
DROP VIEW IF EXISTS marts.v_precio_componente;
DROP VIEW IF EXISTS marts.v_ventas_producto;


-- ============================================================================
-- v_impuestos_asiento — base, IVA, retenciones y TOTAL A COBRAR de cada documento de venta.
-- Es lo que permite dar las tres lecturas del mismo dinero:
--     venta_subtotal        = base                                (sin impuestos)
--     venta_subtotal_con_iva= base + IVA                          (lo que dice la factura)
--     venta_total_factura   = base + IVA − retenciones            (lo que el cliente paga = CxC)
--
-- ⚠ EL IVA NO ESTÁ EN LA LÍNEA DE VENTA. Vive en OTRA línea del MISMO asiento (grupo 2408).
-- Anatomía real de una factura (linea_id 14746722-27, jul-2026):
--     clase 4  41353801 VENTA DE COSMETICOS GRAVADO 19%   166.890   ← base (venta_neta)
--     clase 2  24080101 IVA GENERADO EN VENTAS 19%         31.709   ← el IVA
--     clase 1  13050501 CLIENTES NACIONALES               198.600   ← la CxC
--
-- ⚠⚠ SOLO se toman las cuentas **2408** (IVA generado / IVA descontable de devoluciones).
-- NO se usa `price_total` de Odoo, que parecería el camino obvio: ese campo es
-- base + IVA − RETENCIONES, o sea el valor A COBRAR, no la venta con IVA. Medido en el
-- asiento 82410 (NCR69): base 466.891 + IVA 88.709 (19,0 %) − retefuente 11.672 (2,5 %) =
-- 543.928, que es exactamente su `price_total` y su CxC. Usarlo daba un factor de 1,1650 en
-- 5.555 líneas por 6.024 millones (y 1,1550 donde la retención es del 3,5 %), subestimando la
-- venta: la retención es un anticipo de NUESTRO impuesto de renta que el cliente consigna por
-- nosotros — no reduce la venta. `fact.total_con_impuesto` se conserva porque responde bien a
-- otra pregunta ("cuánto va a pagar el cliente"), pero NO es esta.
--
-- ⚠ Todo aquí está en COP (`venta_neta`, `debito`, `credito` son moneda de la compañía), así que
-- el cálculo NUNCA toca `subtotal`/`total_con_impuesto`/`precio_unitario`, que vienen en la
-- moneda de la FACTURA (las exportaciones se facturan en USD). Ver sql/marts/32_iva_ventas.sql.
--
-- ⚠ El IVA se reparte sobre TODA la base clase 4 del asiento. Medido 2026: 58.128 asientos al
-- 19 %, 750 al 0 % (exportación/excluido/exento) y **1 solo** con dos tarifas mezcladas (base
-- 7.072.460) ⇒ el reparto es exacto en 58.878 de 58.879 y el error del caso mixto queda en el
-- orden del 0,001 % del total. Separar por tarifa exigiría deducirla del NOMBRE de la cuenta,
-- que es justo el antipatrón que el repo ya evitó con la línea de producto.
--
-- ⚠ El TOTAL A COBRAR se toma de las líneas de **CxC** del propio asiento (`es_cxc`), no de
-- "base + IVA − retenciones": la CxC es lo que de verdad se factura y no exige suponer qué
-- cuentas son retención. Medido 2026: **0 de 58.915** asientos de venta carecen de línea de CxC,
-- y las dos formas coinciden en 58.878 (difieren en 37 asientos por 2,8 M sobre 63.470 M, o sea
-- 0,0045 %: la CxC recoge además conceptos que no están en el grupo 1355). Reproduce `price_total`
-- de Odoo al peso: 198.600 en la factura de ejemplo y 543.928 en NCR69.
-- ⚠ Signo: `debito − credito` en la CxC ⇒ POSITIVO en factura y NEGATIVO en nota crédito, igual
-- que la convención de `venta_neta`. Una factura con varios vencimientos tiene varias líneas de
-- CxC: se suman.
-- ============================================================================
DROP VIEW IF EXISTS marts.v_impuestos_asiento;

CREATE VIEW marts.v_impuestos_asiento AS
SELECT f.factura_id,
       SUM(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)          AS base_asiento,
       SUM(CASE WHEN c.codigo LIKE '2408%' THEN f.credito - f.debito ELSE 0 END) AS iva_asiento,
       SUM(CASE WHEN c.codigo LIKE '1355%' THEN f.debito - f.credito ELSE 0 END) AS retencion_asiento,
       SUM(CASE WHEN f.es_cxc THEN f.debito - f.credito ELSE 0 END)              AS total_asiento
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.es_venta IS TRUE
  AND (c.clase_codigo = '4' OR c.codigo LIKE '2408%' OR c.codigo LIKE '1355%' OR f.es_cxc)
GROUP BY 1;

COMMENT ON VIEW marts.v_impuestos_asiento IS
  'Por documento de venta y en COP: base (clase 4), IVA (2408), retenciones (1355) y total a '
  'cobrar (lineas de CxC). Da los factores de v_ventas_producto.venta_subtotal_con_iva y '
  '.venta_total_factura. NO usa price_total de Odoo (viene en moneda de la factura y ya neto de '
  'retenciones).';


CREATE VIEW marts.v_ventas_producto AS
SELECT
    f.linea_id,
    f.factura_id,
    f.numero                AS numero_factura,
    f.referencia,
    f.tipo_movimiento,                                   -- out_invoice / out_refund
    -- ⚠ NOTA DÉBITO (diario NDY/NDEXP): también es `out_invoice`. Una ND NO es venta, SALVO cuando
    -- ANULA una NOTA CRÉDITO (la devolución se anuló → hay que reponer la venta); esas son las únicas
    -- que llegan hasta aquí (ver el filtro al final) y quedan marcadas para poder aislarlas en el BI.
    (dj.codigo IN ('NDY', 'NDEXP'))                      AS es_nota_debito,
    f.empresa_id,
    e.nombre                AS empresa_nombre,
    f.fecha,
    f.fecha_factura,                                     -- fecha propia del documento (la NC, la suya)
    d.anio, d.mes, d.mes_nombre, d.periodo_aaaamm,       -- por fecha CONTABLE
    -- ⭐ fecha_venta: fecha con la que se miden las VENTAS. Para una NC es la fecha de la FACTURA que
    -- corrige (así la NC resta en el mes de la venta original, no en el suyo); para una NOTA DÉBITO que
    -- anula una NC, la de la factura que revive; para una factura es la suya.
    -- Ej.: NCR1858 (mar-2026) corrige FEVY80693 (nov-2025) → resta en nov-2025.
    -- Ej.: NDY1 (24-abr-2026) anula RINV/2026/0062, que anuló FE7281 (09-mar) → suma en MARZO.
    COALESCE(nd.fecha_venta, m.fecha_venta, f.fecha_factura)             AS fecha_venta,
    EXTRACT(YEAR  FROM COALESCE(nd.fecha_venta, m.fecha_venta, f.fecha_factura))::int AS anio_venta,
    EXTRACT(MONTH FROM COALESCE(nd.fecha_venta, m.fecha_venta, f.fecha_factura))::int AS mes_venta,
    -- cliente
    f.tercero_id,
    t.nombre                AS cliente,
    t.identificacion        AS identificacion_cliente,
    t.tipo_cliente,                                       -- partner_type_id crudo de Odoo
    f.categoria,                                          -- categoría de CLIENTE consolidada (ver 17)
    t.ciudad, t.departamento, t.pais,
    -- vendedor / asesor / equipo (equipo viene del asiento: account.move.team_id)
    f.vendedor_id,
    v.nombre                AS vendedor,
    f.equipo,
    f.cliente_analitico,                                 -- cliente atribuido por analítico (plan 22)
    -- producto
    f.producto_id,
    p.codigo                AS producto_codigo,
    p.nombre                AS producto,
    p.categoria             AS producto_categoria,
    -- medidas (netas: NC restan). Prorrateadas si la NC corrige varias facturas.
    (CASE WHEN f.tipo_movimiento = 'out_refund' THEN -f.cantidad ELSE f.cantidad END)
        * COALESCE(m.proporcion, 1)                      AS cantidad_neta,
    f.venta_neta * COALESCE(m.proporcion, 1) AS venta_subtotal,  -- crédito − débito (sin impuestos)
    -- ⚠ precio_unitario INCLUYE IVA y está en la moneda de la factura (ver 32_iva_ventas.sql).
    f.precio_unitario,
    -- ⭐ VALOR CON IVA, EN COP. El IVA no está en esta línea: se toma del asiento (cuentas 2408)
    -- y se aplica como FACTOR = 1 + iva_asiento / base_asiento. Ver v_iva_asiento arriba para
    -- por qué NO se usa `price_total` (es base+IVA−RETENCIONES ⇒ subestimaba la venta) y por qué
    -- todo el cálculo se queda en COP (nunca toca los campos en moneda de la factura).
    -- Medido: factor 1,19 en lo gravado y 1,00 en exportación/excluido/exento.
    -- ⚠ Lleva el mismo `proporcion` que `venta_subtotal`: si no, una NC que corrige varias
    -- facturas contaría el IVA completo en cada una.
    -- ⚠ COALESCE(iva,0) sí es correcto aquí: un asiento sin línea 2408 es una venta SIN IVA
    -- (exportación, excluido, exento) y su factor es 1,00 — no es un dato ausente. Lo que sí
    -- queda NULL es un asiento sin base (base_asiento = 0), que no debería existir.
    f.venta_neta * COALESCE(m.proporcion, 1)
                 * (1 + COALESCE(iv.iva_asiento, 0) / NULLIF(iv.base_asiento, 0))
                                                         AS venta_subtotal_con_iva,
    -- ⭐ TOTAL FACTURA, EN COP: base + IVA − RETENCIONES, o sea lo que el cliente PAGA (la CxC).
    -- Se prorratea con el factor `total_asiento / base_asiento` (ver v_impuestos_asiento).
    -- ⚠ La diferencia contra `venta_subtotal_con_iva` es exactamente la RETENCIÓN (2,5 % o 3,5 %
    -- de la base, medido). No son dos versiones de lo mismo: la retención no reduce la venta, es
    -- un anticipo de nuestro impuesto de renta que el cliente consigna por nosotros. Para
    -- REPORTAR VENTAS se usa `venta_subtotal` o `venta_subtotal_con_iva`; esta columna sirve para
    -- conciliar contra cartera o contra el valor del documento.
    -- ⚠ NO se suma con las otras dos: es el MISMO dinero leído de otra forma.
    f.venta_neta * COALESCE(m.proporcion, 1)
                 * (iv.total_asiento / NULLIF(iv.base_asiento, 0)) AS venta_total_factura,
    f.moneda                    -- COP/USD del documento. ⚠ Solo poblado en las líneas cargadas
                                -- desde 2026-08-05 (ver 32_iva_ventas.sql): NULL en el histórico
                                -- mientras no se corra `--backfill-iva`.
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta   c ON c.cuenta_id  = f.cuenta_id
JOIN marts.dim_fecha    d ON d.fecha_key  = f.fecha_key
LEFT JOIN marts.dim_diario   dj ON dj.diario_id  = f.diario_id
LEFT JOIN marts.dim_tercero  t ON t.tercero_id  = f.tercero_id
LEFT JOIN marts.dim_vendedor v ON v.vendedor_id = f.vendedor_id
LEFT JOIN marts.dim_producto p ON p.producto_id = f.producto_id
LEFT JOIN marts.dim_empresa  e ON e.empresa_id  = f.empresa_id
-- Puente NC→factura: solo matchea NOTAS CRÉDITO (las facturas no están en el puente → 1 fila,
-- proporcion 1). ⚠ Una NC que corrige VARIAS facturas genera VARIAS filas (76 de ~2.000 NC),
-- así que `linea_id` deja de ser único en esta vista.
LEFT JOIN marts.map_nc_factura m ON m.nc_factura_id = f.factura_id
-- Puente ND→factura: solo matchea las NOTAS DÉBITO que anulan una NC (= venta revivida). Ver 25.
LEFT JOIN marts.map_nd_factura nd ON nd.nd_factura_id = f.factura_id
-- Base, IVA y total a cobrar del asiento (ver v_impuestos_asiento arriba).
LEFT JOIN marts.v_impuestos_asiento iv ON iv.factura_id = f.factura_id
WHERE f.es_venta IS TRUE
  AND c.clase_codigo = '4'
  AND f.es_reverso IS NOT TRUE
  AND p.codigo IS NOT NULL
  AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%' OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%')
  -- Excluir NC SIN factura asignada en el puente: no sabemos a qué venta pertenecen, así que no
  -- restan de ventas (se revisan aparte en v_nc_sin_asignar). Las facturas y las NC enlazadas se
  -- conservan. Una NC que corrige varias facturas mantiene m.nc_factura_id NOT NULL → se conserva.
  AND NOT (f.tipo_movimiento = 'out_refund' AND m.nc_factura_id IS NULL)
  -- Una NOTA DÉBITO solo es venta si ANULA una NOTA CRÉDITO (está en el puente ND). Las demás (cargos
  -- extra tipo "Ajuste por precio", o sin `ref`) NO son venta → v_notas_debito_excluidas.
  -- ⚠ `dj` es LEFT JOIN: `dj.codigo IN (...)` da NULL si no hay diario y NO excluye la línea.
  AND NOT (dj.codigo IN ('NDY', 'NDEXP') AND nd.nd_factura_id IS NULL);


-- ============================================================================
-- v_nc_sin_asignar: notas crédito de venta (out_refund) comerciales que NO se pudieron enlazar a
-- una factura en map_nc_factura. Quedan FUERA de v_ventas_producto (no restan de ventas); esta vista
-- las aísla para conciliarlas a mano. Mismos filtros base que v_ventas_producto.
-- ============================================================================
DROP VIEW IF EXISTS marts.v_nc_sin_asignar;
CREATE VIEW marts.v_nc_sin_asignar AS
SELECT
    f.linea_id,
    f.factura_id,
    f.numero                AS numero_factura,
    f.referencia,
    f.empresa_id,
    e.nombre                AS empresa_nombre,
    f.fecha,
    f.fecha_factura,
    f.tercero_id,
    t.nombre                AS cliente,
    t.identificacion        AS identificacion_cliente,
    f.categoria,
    f.producto_id,
    p.codigo                AS producto_codigo,
    p.nombre                AS producto,
    (-f.cantidad)           AS cantidad_neta,
    f.venta_neta            AS venta_subtotal
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta   c ON c.cuenta_id  = f.cuenta_id
LEFT JOIN marts.dim_tercero  t ON t.tercero_id  = f.tercero_id
LEFT JOIN marts.dim_producto p ON p.producto_id = f.producto_id
LEFT JOIN marts.dim_empresa  e ON e.empresa_id  = f.empresa_id
LEFT JOIN marts.map_nc_factura m ON m.nc_factura_id = f.factura_id
WHERE f.es_venta IS TRUE
  AND c.clase_codigo = '4'
  AND f.es_reverso IS NOT TRUE
  AND p.codigo IS NOT NULL
  AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%' OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%')
  AND f.tipo_movimiento = 'out_refund'
  AND m.nc_factura_id IS NULL;


-- ============================================================================
-- v_notas_debito_excluidas: notas débito (diario NDY/NDEXP) que NO son venta, es decir las que NO
-- anulan una nota crédito: cargos extra reales (ej. NDY4 "FE7281, Ajuste por precio") o sin `ref`.
-- Quedan FUERA de v_ventas_producto; esta vista las aísla para conciliar a mano.
-- ============================================================================
DROP VIEW IF EXISTS marts.v_notas_debito_excluidas;
CREATE VIEW marts.v_notas_debito_excluidas AS
SELECT
    f.linea_id,
    f.factura_id,
    f.numero                AS numero_nota_debito,
    f.referencia,                                        -- "<documento>, <motivo>"
    trim(split_part(f.referencia, ',', 1)) AS documento_referenciado,
    dj.codigo               AS diario_codigo,
    f.empresa_id,
    e.nombre                AS empresa_nombre,
    f.fecha,
    f.fecha_factura,
    f.tercero_id,
    t.nombre                AS cliente,
    f.producto_id,
    p.codigo                AS producto_codigo,
    p.nombre                AS producto,
    f.cantidad,
    f.venta_neta            AS venta_subtotal
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta   c ON c.cuenta_id  = f.cuenta_id
JOIN marts.dim_diario  dj ON dj.diario_id = f.diario_id
LEFT JOIN marts.dim_tercero  t ON t.tercero_id  = f.tercero_id
LEFT JOIN marts.dim_producto p ON p.producto_id = f.producto_id
LEFT JOIN marts.dim_empresa  e ON e.empresa_id  = f.empresa_id
LEFT JOIN marts.map_nd_factura nd ON nd.nd_factura_id = f.factura_id
WHERE f.es_venta IS TRUE
  AND c.clase_codigo = '4'
  AND f.es_reverso IS NOT TRUE
  AND dj.codigo IN ('NDY', 'NDEXP')
  AND nd.nd_factura_id IS NULL;
