-- ============================================================================
-- dim_centro_costo 100% Odoo: quitar columnas locales (Excel 'CC') y enriquecer con Odoo.
-- Archivo: sql/marts/10_centro_costo_odoo.sql  (ejecutar DESPUÉS de 01..09). Idempotente.
--
-- adm_vtas/origen/tipo venían de la hoja 'CC' de base_cuentas.xlsx → se eliminan (no hay
-- equivalente en account.analytic.account). Se añaden atributos reales de Odoo: plan, activo,
-- empresa_id. Ninguna vista depende de las columnas eliminadas (solo el hecho referencia
-- centro_costo_id por FK). Tras aplicar, correr `python etl_dw_marts.py --dims` para poblarlas.
-- ============================================================================

ALTER TABLE marts.dim_centro_costo
    DROP COLUMN IF EXISTS adm_vtas,
    DROP COLUMN IF EXISTS origen,
    DROP COLUMN IF EXISTS tipo,
    ADD COLUMN IF NOT EXISTS plan       TEXT,
    ADD COLUMN IF NOT EXISTS activo     BOOLEAN,
    ADD COLUMN IF NOT EXISTS empresa_id BIGINT;
