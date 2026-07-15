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
DROP VIEW IF EXISTS marts.v_ventas_explotada;
DROP VIEW IF EXISTS marts.v_ventas_producto;

CREATE VIEW marts.v_ventas_producto AS
SELECT
    f.linea_id,
    f.factura_id,
    f.numero                AS numero_factura,
    f.referencia,
    f.tipo_movimiento,                                   -- out_invoice / out_refund
    f.empresa_id,
    e.nombre                AS empresa_nombre,
    f.fecha,
    f.fecha_factura,
    d.anio, d.mes, d.mes_nombre, d.periodo_aaaamm,
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
    -- producto
    f.producto_id,
    p.codigo                AS producto_codigo,
    p.nombre                AS producto,
    p.categoria             AS producto_categoria,
    -- medidas (netas: NC restan)
    (CASE WHEN f.tipo_movimiento = 'out_refund' THEN -f.cantidad ELSE f.cantidad END) AS cantidad_neta,
    f.venta_neta            AS venta_subtotal,           -- crédito − débito (sin impuestos)
    f.precio_unitario
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta   c ON c.cuenta_id  = f.cuenta_id
JOIN marts.dim_fecha    d ON d.fecha_key  = f.fecha_key
LEFT JOIN marts.dim_tercero  t ON t.tercero_id  = f.tercero_id
LEFT JOIN marts.dim_vendedor v ON v.vendedor_id = f.vendedor_id
LEFT JOIN marts.dim_producto p ON p.producto_id = f.producto_id
LEFT JOIN marts.dim_empresa  e ON e.empresa_id  = f.empresa_id
WHERE f.es_venta IS TRUE
  AND c.clase_codigo = '4'
  AND f.es_reverso IS NOT TRUE
  AND p.codigo IS NOT NULL
  AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%' OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%');
