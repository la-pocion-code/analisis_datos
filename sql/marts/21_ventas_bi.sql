-- ============================================================================
-- v_ventas_bi — vista DELGADA de ventas para importar a Power BI. Idempotente.
-- Archivo: sql/marts/21_ventas_bi.sql  (ejecutar DESPUÉS de 14 y 15b).
--
-- El hecho contable y sus dimensiones (dim_fecha/tercero/producto/vendedor/empresa) YA están en el
-- BI para los estados financieros. Para las ventas basta importar ESTA vista: solo trae IDs de
-- relación + columnas degeneradas (que no viven en ninguna dimensión) + medidas. Todo lo descriptivo
-- (nombre de cliente, ciudad, producto, categoría de producto, vendedor, mes...) se obtiene cruzando
-- con las dims ya cargadas. Así la tabla de ~905k filas ocupa mucho menos y el modelo va más rápido.
--
-- Sale de v_ventas_explotada (grano de COMPONENTE): una fila por línea individual y una por cada
-- componente de un kit. Es la misma vista, recortada — NO se toca v_ventas_explotada (queda completa
-- para SQL/depuración). Ver docs/guia_bi_ventas.md.
--
-- RELACIONES EN EL BI:
--   fecha_venta   -> dim_fecha.fecha        (ACTIVA; con esta fecha caen las devoluciones en el mes
--                                            de su factura — ver 19_nc_factura.sql)
--   fecha_factura -> dim_fecha.fecha        (INACTIVA; fecha propia del documento, para el informe de
--                                            "NC por mes de emisión" vía USERELATIONSHIP)
--   empresa_id    -> dim_empresa
--   tercero_id    -> dim_tercero
--   vendedor_id   -> dim_vendedor
--   componente_id -> dim_producto           (ACTIVA; vista "por producto", el kit ya repartido)
--   producto_id   -> dim_producto del KIT   (vista "tal como se factura"): como dim_producto solo
--                    admite UNA relación activa, importar dim_producto una 2.ª vez ("Producto (kit)")
--                    y relacionarla por producto_id. (Alternativa: relación inactiva + USERELATIONSHIP.)
--
-- MEDIDAS (regla de oro): el valor SIEMPRE se suma con venta_componente (nunca infla). cantidad_neta
-- es de nivel KIT y se REPITE en cada fila de componente -> úsala solo para [Kits vendidos] por
-- linea_id, nunca en un SUM directo. cantidad_componente = unidades de producto (grano componente).
--
-- Degeneradas que se quedan (no hay dimensión para ellas): categoria (tipo de cliente consolidado),
-- pais, equipo, cliente_analitico, origen (INDIVIDUAL/KIT), tipo_movimiento, numero_factura.
-- ============================================================================

CREATE OR REPLACE VIEW marts.v_ventas_bi AS
SELECT
    -- grano / degeneradas de documento
    e.linea_id,
    e.factura_id,
    e.numero_factura,
    e.tipo_movimiento,          -- out_invoice / out_refund
    e.es_nota_debito,           -- ⚠ TRUE = nota débito (es out_invoice y cuenta como venta)
    e.origen,                   -- INDIVIDUAL / KIT
    -- claves de relación a las dimensiones (ya cargadas en el BI)
    e.fecha_venta,              -- -> dim_fecha.fecha  ACTIVA  (la NC resta en el mes de su factura)
    e.fecha_factura,            -- -> dim_fecha.fecha  INACTIVA (mes de emisión de la NC)
    e.empresa_id,               -- -> dim_empresa
    e.tercero_id,               -- -> dim_tercero
    e.vendedor_id,              -- -> dim_vendedor
    e.componente_id,            -- -> dim_producto (ACTIVA; vista por producto)
    e.producto_id,              -- -> dim_producto del kit (vista tal como se factura)
    -- degeneradas sin dimensión propia
    e.categoria,                -- tipo de cliente consolidado (ver 17_categoria.sql)
    e.pais,
    e.equipo,
    e.cliente_analitico,
    -- medidas
    e.cantidad_neta,            -- nivel KIT (repetida en filas KIT): solo para [Kits vendidos]
    e.cantidad_componente,      -- unidades de producto (grano componente)
    -- ⭐ LAS TRES LECTURAS DEL MISMO DINERO, todas en COP y con el mismo prorrateo por componente.
    -- ⚠⚠ NO se suman entre sí: es el mismo dinero, leído de tres formas.
    e.venta_componente,                 -- base, SIN impuestos  ← la medida de VENTAS
    e.venta_componente_con_iva,         -- base + IVA           (lo que dice la factura)
    e.venta_componente_total_factura,   -- base + IVA − retención = lo que PAGA el cliente (CxC)
    e.moneda                    -- COP/USD del documento (exportación factura en USD)
FROM marts.v_ventas_explotada e;
