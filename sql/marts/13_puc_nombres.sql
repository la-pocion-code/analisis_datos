-- ============================================================================
-- Nombres de la jerarquía PUC por cuenta (clase/grupo/cuenta/subcuenta) en dim_cuenta.
-- Archivo: sql/marts/13_puc_nombres.sql  (ejecutar DESPUÉS de 01..12). Idempotente.
--
-- Fuente: account.group de Odoo (es_CO), por prefijo puntual de 1/2/4/6 díg (nombre más frecuente).
-- Complementa (NO reemplaza) seccion/concepto/nivel_movimiento y *_codigo. Se POBLAN vía el ETL
-- (etl_dw_marts.cargar_puc_nombres); aquí solo el DDL. Tras aplicar, correr `--dims`.
--
-- Ej.: 510506 → clase_nombre=GASTOS, grupo_nombre=OPERACIONALES DE ADMINISTRACION,
--      cuenta_nombre=GASTOS DE PERSONAL, subcuenta_nombre=GASTOS DE PERSONAL SALARIOS.
-- ============================================================================

ALTER TABLE marts.dim_cuenta
    ADD COLUMN IF NOT EXISTS clase_nombre     TEXT,   -- N1 (1 díg): INGRESO/GASTOS/ACTIVO…
    ADD COLUMN IF NOT EXISTS grupo_nombre     TEXT,   -- N2 (2 díg): OPERACIONALES, CUENTAS POR COBRAR…
    ADD COLUMN IF NOT EXISTS cuenta_nombre    TEXT,   -- N4 (4 díg): CLIENTES, GASTOS DE PERSONAL…
    ADD COLUMN IF NOT EXISTS subcuenta_nombre TEXT;   -- N6 (6 díg): NACIONALES, … SALARIOS…
