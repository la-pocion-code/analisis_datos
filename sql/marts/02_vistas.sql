-- ============================================================================
-- Vistas de conveniencia para Power BI sobre la tabla única fact_movimiento_contable.
-- Archivo: sql/marts/02_vistas.sql  (ejecutar DESPUÉS de 01_star_schema.sql)
-- No materializan datos; son SELECT sobre el hecho + dimensiones.
-- ============================================================================

-- ── v_ventas: solo líneas de venta de producto (ingreso, clase 4) ────────────
CREATE OR REPLACE VIEW marts.v_ventas AS
SELECT f.*
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.es_venta IS TRUE
  AND c.clase_codigo = '4';          -- cuentas de ingreso (líneas de producto/ingreso)

-- ── v_balance_comprobacion: resumen contable cuenta × mes ────────────────────
CREATE OR REPLACE VIEW marts.v_balance_comprobacion AS
SELECT
    f.cuenta_id,
    c.codigo            AS cuenta_codigo,
    c.nombre            AS cuenta_nombre,
    c.nivel_movimiento,
    d.periodo_aaaamm,
    d.anio,
    d.mes,
    SUM(f.debito)               AS total_debito,
    SUM(f.credito)              AS total_credito,
    SUM(f.debito - f.credito)   AS saldo,
    COUNT(*)                    AS n_movimientos
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
JOIN marts.dim_fecha  d ON d.fecha_key = f.fecha_key
GROUP BY f.cuenta_id, c.codigo, c.nombre, c.nivel_movimiento,
         d.periodo_aaaamm, d.anio, d.mes;
