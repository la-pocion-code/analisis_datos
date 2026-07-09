-- ============================================================================
-- Ajuste de nivel_movimiento (etiqueta canónica del grupo PUC de 2 díg).
-- Archivo: sql/marts/09_nivel_movimiento.sql  (ejecutar DESPUÉS de 01..08). Idempotente.
--
-- La CLAVE de agrupación es c.grupo_codigo (2 díg del code de Odoo = fiel a Odoo). Odoo NO tiene
-- una etiqueta única (difiere por empresa) → aquí se fija la etiqueta CANÓNICA, igual para ambas
-- empresas. Debe coincidir EXACTAMENTE con NIVEL_N2 de etl_dw_marts.py.
--
-- Aplica al DW ya cargado sin releer Odoo: rellena los grupos que estaban NULL (47/54/57/59) y
-- reafirma el resto. Solo toca clases P&L (4/5/6/7).
-- ============================================================================

UPDATE marts.dim_cuenta
   SET nivel_movimiento = CASE grupo_codigo
        WHEN '41' THEN 'Ingresos operacionales'
        WHEN '42' THEN 'Ingresos no operacionales'
        WHEN '47' THEN 'Impuesto diferido (ingreso)'
        WHEN '51' THEN 'Gastos operacionales de administración'
        WHEN '52' THEN 'Gastos operacionales de ventas'
        WHEN '53' THEN 'Gastos no operacionales'
        WHEN '54' THEN 'Impuesto de renta y complementarios'
        WHEN '57' THEN 'Impuesto diferido (gasto)'
        WHEN '59' THEN 'Ganancias y pérdidas (cierre)'
        WHEN '61' THEN 'Costo de ventas'
        WHEN '62' THEN 'Compras'
        WHEN '71' THEN 'Costos de producción'
        WHEN '72' THEN 'Costos de producción'
        WHEN '73' THEN 'Costos de producción'
        WHEN '74' THEN 'Costos de producción'
        ELSE nivel_movimiento
       END
 WHERE clase_codigo IN ('4', '5', '6', '7');

-- Control: cuentas P&L que quedaron SIN etiqueta (debe devolver 0 filas).
-- SELECT grupo_codigo, COUNT(*) FROM marts.dim_cuenta
--  WHERE clase_codigo IN ('4','5','6','7') AND nivel_movimiento IS NULL
--  GROUP BY grupo_codigo ORDER BY grupo_codigo;
