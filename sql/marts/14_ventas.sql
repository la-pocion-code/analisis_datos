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
    f.precio_unitario
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
