-- ============================================================================
-- Mapeos de negocio para VENTAS que NO viven en Odoo. Fase 4. Idempotente.
-- Archivo: sql/marts/16_mapeos_ventas.sql  (ejecutar DESPUÉS de 14/15).
--
-- Único insumo NO-Odoo del DW: tres reglas comerciales que hoy están en Excel (Drive).
-- Poblado a demanda con `python cargar_mapeos.py` (lee de Drive vía DriveLoader → UPSERT).
-- Replican el enriquecimiento de ReportClassNew.transformar_base():
--   ZONA  = f(departamento, categoria)   [+ Cundinamarca por depto+ciudad, + Bogotá por NIT]
--   CLIENTE PADRE = f(cliente)           (consolidar clientes bajo su matriz)
--   CATEGORÍA normalizada  = f(categoria de Odoo)   (dict de renombrado)
--
-- Orden de resolución de la ZONA (de menor a mayor prioridad):
--   1) map_zona (depto+categoria)  →  2) map_zona_cundinamarca (depto+ciudad+categoria, solo
--   MAYORISTA NV sin zona)  →  3) map_zona_bogota (NIT+categoria, pisa las anteriores).
-- ============================================================================

-- ── ZONA general: departamento + categoría ─────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.map_zona (
    departamento TEXT NOT NULL,
    categoria    TEXT NOT NULL,
    zona         TEXT,
    PRIMARY KEY (departamento, categoria)
);

-- ── ZONA Cundinamarca: departamento + ciudad + categoría ───────────────────
CREATE TABLE IF NOT EXISTS marts.map_zona_cundinamarca (
    departamento TEXT NOT NULL,
    ciudad       TEXT NOT NULL,
    categoria    TEXT NOT NULL,
    zona         TEXT,
    PRIMARY KEY (departamento, ciudad, categoria)
);

-- ── ZONA Bogotá: por NIT/documento + categoría (override por cliente) ───────
-- ⚠ DEPRECADA: `Base_bogota.xlsx` ya no se usa (está vacío en Drive) y `cargar_mapeos.py` ya no la
-- carga → queda creada pero VACÍA. Se conserva por si el negocio retoma el override por NIT.
CREATE TABLE IF NOT EXISTS marts.map_zona_bogota (
    documento TEXT NOT NULL,        -- identificación (vat) del cliente
    categoria TEXT NOT NULL,
    zona      TEXT,
    PRIMARY KEY (documento, categoria)
);

-- ── CLIENTE PADRE: consolidar cada cliente bajo su matriz ───────────────────
CREATE TABLE IF NOT EXISTS marts.map_cliente_padre (
    cliente       TEXT PRIMARY KEY,
    cliente_padre TEXT
);

-- ── CATEGORÍA normalizada para BI (renombrado de la categoría de Odoo) ──────
CREATE TABLE IF NOT EXISTS marts.map_categoria (
    categoria_origen TEXT PRIMARY KEY,  -- categoría/tipo de cliente tal como viene de Odoo
    categoria_bi     TEXT               -- etiqueta normalizada para el reporte de ventas
);
