-- ============================================================================
-- Ampliar columnas de texto de dimensiones a TEXT (evita "value too long").
-- Archivo: sql/marts/07_widen_text.sql  (ejecutar DESPUÉS de 01..06). Idempotente.
-- ============================================================================

-- v_cartera depende de columnas de dim_tercero → recrear tras el ALTER.
DROP VIEW IF EXISTS marts.v_cartera;

ALTER TABLE marts.dim_tercero
    ALTER COLUMN nombre         TYPE TEXT,
    ALTER COLUMN identificacion TYPE TEXT,
    ALTER COLUMN tipo_cliente   TYPE TEXT,
    ALTER COLUMN ciudad         TYPE TEXT,
    ALTER COLUMN departamento   TYPE TEXT,
    ALTER COLUMN pais           TYPE TEXT;

ALTER TABLE marts.dim_producto
    ALTER COLUMN codigo    TYPE TEXT,
    ALTER COLUMN nombre    TYPE TEXT,
    ALTER COLUMN categoria TYPE TEXT;

ALTER TABLE marts.dim_diario        ALTER COLUMN nombre TYPE TEXT;
ALTER TABLE marts.dim_centro_costo  ALTER COLUMN nombre TYPE TEXT;
ALTER TABLE marts.dim_vendedor      ALTER COLUMN nombre TYPE TEXT;

-- Recrear v_cartera (igual que en 06_cartera_en_hecho.sql).
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
