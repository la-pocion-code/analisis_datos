-- ============================================================================
-- Consolidar cartera en la TABLA ÚNICA de hechos (elimina fact_cartera).
-- Archivo: sql/marts/06_cartera_en_hecho.sql  (ejecutar DESPUÉS de 01..05)
-- Idempotente. En BI se usa un solo hecho: fact_movimiento_contable.
-- ============================================================================

-- ── Cartera a nivel de línea en el hecho ─────────────────────────────────────
-- saldo_pendiente        = account.move.line.amount_residual (residual por línea)
-- es_cxc                 = account_type = 'asset_receivable' (líneas de cartera)
-- fecha_vencimiento_key  = account.move.line.date_maturity (para aging)
ALTER TABLE marts.fact_movimiento_contable
    ADD COLUMN IF NOT EXISTS saldo_pendiente       NUMERIC,
    ADD COLUMN IF NOT EXISTS es_cxc                BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS fecha_vencimiento_key INTEGER;

CREATE INDEX IF NOT EXISTS ix_fmc_cxc ON marts.fact_movimiento_contable (es_cxc);

-- ── v_cartera: ahora desde el hecho único (líneas de CxC con saldo) ──────────
DROP VIEW IF EXISTS marts.v_cartera;
CREATE VIEW marts.v_cartera AS
SELECT
    f.linea_id, f.factura_id, f.numero, f.tipo_movimiento, f.estado_pago,
    f.tercero_id, t.nombre AS tercero_nombre, t.identificacion, t.tipo_cliente,
    f.empresa_id, e.nombre AS empresa_nombre,
    f.fecha_key, f.fecha_vencimiento_key,
    f.saldo_pendiente
FROM marts.fact_movimiento_contable f
LEFT JOIN marts.dim_tercero t ON t.tercero_id = f.tercero_id
LEFT JOIN marts.dim_empresa e ON e.empresa_id = f.empresa_id
WHERE f.es_cxc IS TRUE
  AND COALESCE(f.saldo_pendiente, 0) <> 0;

-- ── Eliminar el hecho de cartera separado (ya no se usa) ─────────────────────
DROP TABLE IF EXISTS marts.fact_cartera;
