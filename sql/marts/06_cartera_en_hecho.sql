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
-- ⚠⚠ ESTA VISTA SE RECREA IDÉNTICA EN 07_widen_text.sql, que corre DESPUÉS y por
-- tanto gana. Cualquier cambio aquí hay que repetirlo allí o no existe.
--
-- ⚠ `es_nota_debito`: en Odoo una nota débito es `move_type = 'out_invoice'`,
-- exactamente igual que una factura de venta. Lo ÚNICO que las distingue es el
-- diario, y se mira por CÓDIGO y no por nombre porque es más estable — misma
-- regla que 14_ventas.sql y 25_nd_factura.sql, que ya la usan para ventas.
-- Sin esta columna, aguas abajo no hay forma de saberlo: `mv_cartera_saldo` no
-- puede leer `dim_diario` y la intranet no puede leer ninguna de las dos.
-- ⚠⚠ CASCADE: `mv_cartera_saldo` (30_cartera_dashboards.sql) cuelga de esta
-- vista. Sin el, este DROP falla en cuanto la MV existe. El 30 la recrea.
DROP VIEW IF EXISTS marts.v_cartera CASCADE;
CREATE VIEW marts.v_cartera AS
SELECT
    f.linea_id, f.factura_id, f.numero, f.tipo_movimiento, f.estado_pago,
    f.tercero_id, t.nombre AS tercero_nombre, t.identificacion, t.tipo_cliente,
    f.empresa_id, e.nombre AS empresa_nombre,
    f.fecha_key, f.fecha_vencimiento_key,
    f.saldo_pendiente,
    COALESCE(dj.codigo IN ('NDY', 'NDEXP'), FALSE)        AS es_nota_debito,
    -- El `ref` de una ND trae formato fijo "<documento>, <motivo>" (41 de 44).
    -- Se publica el documento para que quien cobre sepa de dónde sale el cargo.
    NULLIF(btrim(split_part(f.referencia, ',', 1)), '')   AS documento_origen
FROM marts.fact_movimiento_contable f
LEFT JOIN marts.dim_tercero t ON t.tercero_id = f.tercero_id
LEFT JOIN marts.dim_empresa e ON e.empresa_id = f.empresa_id
LEFT JOIN marts.dim_diario  dj ON dj.diario_id = f.diario_id
WHERE f.es_cxc IS TRUE
  AND COALESCE(f.saldo_pendiente, 0) <> 0;

-- ── Eliminar el hecho de cartera separado (ya no se usa) ─────────────────────
DROP TABLE IF EXISTS marts.fact_cartera;
