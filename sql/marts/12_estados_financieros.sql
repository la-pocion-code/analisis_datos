-- ============================================================================
-- Columnas para estados financieros completos en dim_cuenta.
-- Archivo: sql/marts/12_estados_financieros.sql  (ejecutar DESPUÉS de 01..11). Idempotente.
--
-- nivel_movimiento / seccion / subseccion se DERIVAN de los reportes de Odoo (account.report,
-- es_CO): Balance/ESF (clases 1/2/3) + Estado de Resultados (4/5/6/7). Se POBLAN vía el ETL
-- (etl_dw_marts.cargar_clasificacion_reportes); aquí solo el DDL. Tras aplicar, correr `--dims`.
--
-- nivel_movimiento pasa a TEXT: los nombres de línea de Odoo pueden ser largos
-- (p.ej. "Dividendos o participaciones decretados en acciones, cuotas o partes de interés social").
-- ============================================================================

-- v_balance_comprobacion depende de nivel_movimiento → se recrea tras el ALTER.
DROP VIEW IF EXISTS marts.v_balance_comprobacion;

ALTER TABLE marts.dim_cuenta
    ALTER COLUMN nivel_movimiento TYPE TEXT,
    ADD COLUMN IF NOT EXISTS seccion    TEXT,   -- raíz del reporte: ACTIVOS/PASIVO/PATRIMONIO/Ingresos/Gastos/Costos…
    ADD COLUMN IF NOT EXISTS subseccion TEXT;   -- subtotal: Activos corrientes/no corrientes, Pasivos corrientes…

-- Recreación de la vista + seccion/subseccion (para armar estados financieros directo en SQL/BI).
CREATE VIEW marts.v_balance_comprobacion AS
SELECT
    f.empresa_id,
    e.nombre            AS empresa_nombre,
    f.cuenta_id,
    c.codigo            AS cuenta_codigo,
    c.nombre            AS cuenta_nombre,
    c.clase_codigo,
    c.grupo_codigo,
    c.seccion,
    c.subseccion,
    c.nivel_movimiento,
    d.periodo_aaaamm,
    d.anio,
    d.mes,
    SUM(f.debito)             AS total_debito,
    SUM(f.credito)            AS total_credito,
    SUM(f.debito - f.credito) AS saldo,
    COUNT(*)                  AS n_movimientos
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
JOIN marts.dim_fecha  d ON d.fecha_key = f.fecha_key
LEFT JOIN marts.dim_empresa e ON e.empresa_id = f.empresa_id
GROUP BY f.empresa_id, e.nombre, f.cuenta_id, c.codigo, c.nombre, c.clase_codigo,
         c.grupo_codigo, c.seccion, c.subseccion, c.nivel_movimiento, d.periodo_aaaamm, d.anio, d.mes;
