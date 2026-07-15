-- ============================================================================
-- Enriquecimiento de dimensiones para VENTAS (Fase 2). 100% desde Odoo.
-- Archivo: sql/marts/15_dims_ventas.sql  (ejecutar DESPUÉS de 01..14). Idempotente.
--
-- Añade a dim_tercero los datos de contacto/segmentación que hoy da el Excel pero que ya
-- viven en Odoo (res.partner), y a dim_producto la marca de kit (product.product.bom_count).
-- Poblado: `python etl_dw_marts.py --dims` (refrescar_dimensiones / cargar_terceros).
-- ============================================================================

-- ── dim_tercero: contacto + segmentación de ventas ──────────────────────────
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS telefono         TEXT;   -- phone (o mobile)
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS email            TEXT;   -- email
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS etiqueta         TEXT;   -- category_id (m2m, nombres "; ")
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS cliente_padre_id BIGINT; -- commercial_partner_id (id)
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS cliente_padre    TEXT;   -- commercial_partner_id (nombre)

-- ── dim_producto: marca de kit ──────────────────────────────────────────────
ALTER TABLE marts.dim_producto ADD COLUMN IF NOT EXISTS es_kit BOOLEAN;         -- bom_count > 0

-- ── Equipo de ventas: va en el HECHO, no en el tercero ──────────────────────
-- OJO: `res.partner.team_id` está VACÍO en este Odoo (0 de ~206k). El equipo de ventas vive en el
-- ASIENTO (`account.move.team_id`, 99,99% de las facturas de venta lo tienen) — igual que el
-- pipeline de Excel, que lo mapea por número de factura. Se guarda como columna degenerada del
-- hecho, mismo patrón que `vendedor_id` (invoice_user_id del move).
ALTER TABLE marts.fact_movimiento_contable ADD COLUMN IF NOT EXISTS equipo TEXT;
ALTER TABLE marts.dim_tercero DROP COLUMN IF EXISTS equipo;                     -- era 100% NULL
