-- ============================================================================
-- Multi-empresa (dim_empresa + empresa_id en el hecho).
-- Archivo: sql/marts/04_empresa_cartera.sql  (ejecutar DESPUÉS de 01/02/03)
-- Idempotente.
-- NOTA: la cartera (CxC) NO es un hecho aparte; sale del hecho único filtrando
--       es_cxc y sumando saldo_pendiente. Ver 06_cartera_en_hecho.sql.
-- ============================================================================

-- ── dim_empresa (res.company) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_empresa (
    empresa_id BIGINT PRIMARY KEY,       -- res.company id (PCN Poción, Hector Fabio, ...)
    nombre     TEXT
);

-- ── empresa_id en el hecho principal ─────────────────────────────────────────
ALTER TABLE marts.fact_movimiento_contable
    ADD COLUMN IF NOT EXISTS empresa_id BIGINT REFERENCES marts.dim_empresa(empresa_id);
CREATE INDEX IF NOT EXISTS ix_fmc_empresa ON marts.fact_movimiento_contable (empresa_id);
