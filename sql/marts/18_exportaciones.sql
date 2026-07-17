-- ============================================================================
-- EXPORTACIONES: cliente analítico (plan 22) + país por línea + vista de auditoría.
-- Archivo: sql/marts/18_exportaciones.sql  (ejecutar DESPUÉS de 14..17). Idempotente.
--
-- Objetivo: proyectar el PyG por PAÍS y por CLIENTE, incluyendo los gastos de exportación.
--   · cliente_analitico  → plan analítico 22 "Cliente" ([CLI-ZAR-EC]...). Atribuye tanto ventas
--     como gastos (logística cargada a proveedores) al cliente correcto. Lo captura el ETL
--     (construir_hecho, rol 'cliente_analitico') y se backfillea desde account.analytic.line.
--   · pais               → denormalizado de dim_tercero.pais (país del tercero de la línea).
--     Decisión: país ESTRICTO por línea; el gasto logístico queda en Colombia (país del proveedor),
--     y el cliente_analitico es el que agrupa ventas+gastos por cliente exportador.
--   · categoria='EXPORTACION' (ver 17 + consolidar_categoria): clientes EXTERIOR + centros [EXPO].
-- ============================================================================

ALTER TABLE marts.fact_movimiento_contable ADD COLUMN IF NOT EXISTS cliente_analitico TEXT; -- plan 22
ALTER TABLE marts.fact_movimiento_contable ADD COLUMN IF NOT EXISTS pais              TEXT; -- = dim_tercero.pais

CREATE INDEX IF NOT EXISTS ix_fact_cliente_analitico ON marts.fact_movimiento_contable (cliente_analitico);

-- ── v_exportaciones: auditoría + insumo para el PyG por país×cliente ─────────
-- Todo lo marcado como EXPORTACION (clientes EXTERIOR o gastos en centros [EXPO]).
CREATE OR REPLACE VIEW marts.v_exportaciones AS
SELECT
    f.linea_id,
    f.empresa_id,
    e.nombre                AS empresa_nombre,
    f.fecha,
    d.anio, d.mes, d.mes_nombre, d.periodo_aaaamm,
    cta.clase_codigo,
    cta.nivel_movimiento,                            -- concepto de PyG (Ingresos/Costo/Gastos...)
    cta.codigo              AS cuenta_codigo,
    cta.nombre              AS cuenta_nombre,
    f.tercero_id,
    t.nombre                AS tercero,              -- quien factura/recibe el gasto (proveedor logístico)
    f.cliente_analitico,                             -- cliente exportador atribuido (plan 22)
    f.pais,                                          -- país del tercero de la línea (estricto)
    f.categoria,
    cc.codigo               AS centro_codigo,
    cc.nombre               AS centro_nombre,
    f.debito, f.credito,
    f.venta_neta,                                    -- crédito − débito (ventas)
    (f.debito - f.credito)  AS gasto_costo           -- débito − crédito (gastos/costos)
FROM marts.fact_movimiento_contable f
JOIN marts.dim_fecha        d   ON d.fecha_key      = f.fecha_key
JOIN marts.dim_cuenta       cta ON cta.cuenta_id    = f.cuenta_id
LEFT JOIN marts.dim_tercero t   ON t.tercero_id     = f.tercero_id
LEFT JOIN marts.dim_empresa e   ON e.empresa_id     = f.empresa_id
LEFT JOIN marts.dim_centro_costo cc ON cc.centro_costo_id = f.centro_costo_id
WHERE f.categoria = 'EXPORTACION'
   OR f.cliente_analitico IS NOT NULL;              -- incluye clientes clave (plan 22) para auditar
