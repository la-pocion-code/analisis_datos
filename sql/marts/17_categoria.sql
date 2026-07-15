-- ============================================================================
-- CATEGORÍA (tipo de cliente) consolidada en UN solo campo del hecho.
-- Archivo: sql/marts/17_categoria.sql  (ejecutar DESPUÉS de 14/15/16). Idempotente.
--
-- Sirve igual a VENTAS y a CONTABILIDAD. Se arma de DOS fuentes de Odoo (ninguna basta sola):
--   1) `partner_type_id` (cabecera del asiento) → `dim_tercero.tipo_cliente`. Fuente PRINCIPAL:
--      es con la que el negocio arma las categorías. MANDA cuando existe.
--   2) Distribución analítica del plan 21 "Canal" (`analytic_line_ids/x_plan21_id`) → `fact.canal`.
--      RELLENA cuando falta (1). Existe porque la utilidad por cliente se mira por nombre del
--      cliente, pero hay GASTOS de esos clientes que se cargan a TERCEROS y desaparecen del balance
--      del cliente; por eso se marcan en el analítico. Es lo que rescata las clases 5/6.
--
-- El valor lo calcula `consolidar_categoria()` (etl_dw_marts.py) como paso de cierre post-carga,
-- replicando la cadena de ReportClassNew.transformar_base() y normalizando con marts.map_categoria.
--
-- OJO: `fact.categoria` = categoría del CLIENTE. La categoría de PRODUCTO es `dim_producto.categoria`
-- (en v_ventas_producto se expone como `producto_categoria`). Son cosas distintas.
-- ============================================================================

ALTER TABLE marts.fact_movimiento_contable ADD COLUMN IF NOT EXISTS categoria TEXT;

-- Filtrado/agrupación habitual en BI: por categoría dentro de empresa+fecha.
CREATE INDEX IF NOT EXISTS ix_fact_categoria ON marts.fact_movimiento_contable (categoria);
