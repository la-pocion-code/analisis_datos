-- ============================================================================
-- Columnas DATE (filtrado fácil en BD) + balance de comprobación por empresa.
-- Archivo: sql/marts/08_fechas_balance.sql  (ejecutar DESPUÉS de 01..07). Idempotente.
-- ============================================================================

-- ── Fechas reales (DATE) además de las *_key numéricas ───────────────────────
ALTER TABLE marts.fact_movimiento_contable
    ADD COLUMN IF NOT EXISTS fecha             DATE,
    ADD COLUMN IF NOT EXISTS fecha_factura     DATE,
    ADD COLUMN IF NOT EXISTS fecha_vencimiento DATE;

-- Backfill de filas ya cargadas desde las keys (sin recargar).
UPDATE marts.fact_movimiento_contable
   SET fecha = to_date(fecha_key::text, 'YYYYMMDD')
 WHERE fecha IS NULL AND fecha_key IS NOT NULL;
UPDATE marts.fact_movimiento_contable
   SET fecha_factura = to_date(fecha_factura_key::text, 'YYYYMMDD')
 WHERE fecha_factura IS NULL AND fecha_factura_key IS NOT NULL;
UPDATE marts.fact_movimiento_contable
   SET fecha_vencimiento = to_date(fecha_vencimiento_key::text, 'YYYYMMDD')
 WHERE fecha_vencimiento IS NULL AND fecha_vencimiento_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_fmc_fecha_date ON marts.fact_movimiento_contable (fecha);

-- ── v_balance_comprobacion: por empresa (para filtrar cids=8) ────────────────
-- Incluye TODOS los move types y NO excluye reversos (son movimientos reales).
DROP VIEW IF EXISTS marts.v_balance_comprobacion;
CREATE VIEW marts.v_balance_comprobacion AS
SELECT
    f.empresa_id,
    e.nombre            AS empresa_nombre,
    f.cuenta_id,
    c.codigo            AS cuenta_codigo,
    c.nombre            AS cuenta_nombre,
    c.clase_codigo,
    c.grupo_codigo,
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
         c.grupo_codigo, c.nivel_movimiento, d.periodo_aaaamm, d.anio, d.mes;
