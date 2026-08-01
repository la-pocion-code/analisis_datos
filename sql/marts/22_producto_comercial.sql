-- ============================================================================
-- Nombre COMERCIAL del producto en dim_producto. Idempotente.
-- Archivo: sql/marts/22_producto_comercial.sql  (ejecutar cuando se agregue la columna).
--
-- `dim_producto.nombre` = product.product.name en el idioma BASE (p. ej. PCN19 = "Kit anticaída y
-- crecimiento capilar"). El nombre por el que se reconoce comercialmente el producto (PCN19 =
-- "DUTONIC (TONICO CAPILAR)") es la TRADUCCIÓN es_CO del `name` de la PLANTILLA product.template
-- (ese campo es traducible; el usuario del BI ve es_CO). La puebla enriquecer_nombre_comercial() en
-- el ETL leyendo con context lang='es_CO' (100% Odoo). En Power BI se proyecta esta columna.
-- ============================================================================

ALTER TABLE marts.dim_producto ADD COLUMN IF NOT EXISTS nombre_comercial TEXT;
