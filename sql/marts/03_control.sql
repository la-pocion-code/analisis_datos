-- ============================================================================
-- Control de marcas de agua para la carga incremental del marts.
-- Archivo: sql/marts/03_control.sql  (ejecutar DESPUÉS de 01_star_schema.sql)
-- ============================================================================

CREATE TABLE IF NOT EXISTS marts.etl_control (
    modelo       TEXT PRIMARY KEY,        -- 'account.move.line', 'res.partner', 'product.product', ...
    ultimo_write TIMESTAMPTZ,             -- MAX(write_date) ya procesado para ese modelo
    filas        BIGINT,                  -- filas acumuladas en la última corrida
    actualizado  TIMESTAMPTZ DEFAULT now()
);
