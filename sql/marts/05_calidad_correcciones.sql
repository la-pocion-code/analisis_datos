-- ============================================================================
-- Reglas de ventas (reversos), control de calidad y correcciones de datos.
-- Archivo: sql/marts/05_calidad_correcciones.sql  (ejecutar DESPUÉS de 01..04)
-- Idempotente.
-- ============================================================================

-- ── Columnas de reverso en el hecho ──────────────────────────────────────────
-- estado_pago         = account.move.payment_state
-- reversed_factura_id = account.move.reversed_entry_id (factura que la NC reversa)
-- es_reverso          = TRUE si la línea NO debe contar en ventas por ser un
--                       reverso TOTAL (factura payment_state='reversed' o su NC).
ALTER TABLE marts.fact_movimiento_contable
    ADD COLUMN IF NOT EXISTS estado_pago         VARCHAR(20),
    ADD COLUMN IF NOT EXISTS reversed_factura_id BIGINT,
    ADD COLUMN IF NOT EXISTS es_reverso          BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_fmc_reverso ON marts.fact_movimiento_contable (es_reverso);

-- ── v_ventas: ingresos (clase 4) de venta, EXCLUYENDO reversos totales ────────
-- Las devoluciones PARCIALES sí restan (vía venta_neta); solo se excluyen los
-- pares de reverso total (factura anulada + su NC). Ver es_reverso.
CREATE OR REPLACE VIEW marts.v_ventas AS
SELECT f.*
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.es_venta IS TRUE
  AND c.clase_codigo = '4'
  AND f.es_reverso IS NOT TRUE;

-- ── v_dq_analitica: anomalías de distribución analítica a corregir en Odoo ────
-- 1) algún reparto con porcentaje <> 100 (cada plan debe ir siempre al 100%).
--    Nota: una línea puede tener varias entradas (una por plan), cada una al 100%;
--    la anomalía es que un VALOR individual sea distinto de 100, no que la suma lo sea.
-- 2) líneas de gasto/costo (clases 5 y 6) sin centro de costo.
CREATE OR REPLACE VIEW marts.v_dq_analitica AS
-- (1) algún porcentaje del reparto distinto de 100
SELECT
    f.linea_id, f.factura_id, f.numero, f.tipo_movimiento,
    c.codigo AS cuenta_codigo, c.clase_codigo,
    'PCT_DISTINTO_100' AS anomalia,
    (SELECT MIN(value::numeric)
       FROM jsonb_each_text(f.analytic_distribution) WHERE value::numeric <> 100) AS detalle_pct,
    f.analytic_distribution
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.analytic_distribution IS NOT NULL
  AND EXISTS (SELECT 1 FROM jsonb_each_text(f.analytic_distribution) WHERE value::numeric <> 100)
UNION ALL
-- (2) gasto/costo (clases 5/6) sin centro de costo
SELECT
    f.linea_id, f.factura_id, f.numero, f.tipo_movimiento,
    c.codigo, c.clase_codigo,
    'SIN_CENTRO_COSTO' AS anomalia,
    NULL::numeric AS detalle_pct,
    f.analytic_distribution
FROM marts.fact_movimiento_contable f
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE c.clase_codigo IN ('5', '6')
  AND f.centro_costo_id IS NULL;

-- ── correcciones: overrides de datos mal registrados en Odoo (aplicados en DW) ─
-- El ETL aplica estas correcciones tras cargar (UPDATE dirigido). No toca Odoo.
CREATE TABLE IF NOT EXISTS marts.correcciones (
    id          BIGSERIAL PRIMARY KEY,
    tabla       TEXT NOT NULL,          -- p.ej. 'fact_movimiento_contable'
    pk_col      TEXT NOT NULL,          -- p.ej. 'linea_id'
    pk_val      BIGINT NOT NULL,        -- id de la fila a corregir
    campo       TEXT NOT NULL,          -- columna a corregir
    valor_nuevo TEXT,                   -- valor corregido (texto; se castea al aplicar)
    motivo      TEXT,
    activo      BOOLEAN DEFAULT TRUE,
    creado_en   TIMESTAMPTZ DEFAULT now()
);
