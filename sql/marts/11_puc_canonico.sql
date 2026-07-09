-- ============================================================================
-- Canonicalización de códigos PUC duplicados (NO destructivo).
-- Archivo: sql/marts/11_puc_canonico.sql  (ejecutar DESPUÉS de 01..10). Idempotente.
--
-- Problema: en Odoo coexisten DOS códigos para la MISMA cuenta (variantes 8 vs 9 díg de la
-- migración del plan de cuentas). Ej.: "CLIENTES NACIONALES" = 130505001 + 13050501;
-- "Venta de Cosmeticos gravado 19%" = 413538001 + 41353801. Esto parte los reportes por cuenta.
--
-- Duplicado GENUINO = misma subcuenta (6 díg) + mismo nombre normalizado (upper+trim); difieren
-- solo en el sufijo auxiliar. NO se colapsa por nombre solo (OTROS/COMISIONES en grupos 51 vs 52
-- son cuentas distintas) ni por subcuenta sola (110505 = Caja Cali/Yumbo/Nequi son distintas).
--
-- NO destructivo: el hecho conserva el cuenta_id real de Odoo. Se agregan a dim_cuenta columnas
-- canónicas; en Power BI se agrupa por codigo_canonico / cuenta_canonica_id.
-- Regla del canónico: variante MÁS USADA en el hecho (desempate: código más corto, luego menor id).
-- ============================================================================

ALTER TABLE marts.dim_cuenta
    ADD COLUMN IF NOT EXISTS cuenta_canonica_id BIGINT,
    ADD COLUMN IF NOT EXISTS codigo_canonico    VARCHAR(20),
    ADD COLUMN IF NOT EXISTS nombre_canonico    TEXT;

-- 1) Duplicados genuinos: elegir canónico por (subcuenta 6 díg + nombre normalizado).
WITH usos AS (
    SELECT cuenta_id, COUNT(*) AS n
    FROM marts.fact_movimiento_contable GROUP BY cuenta_id
),
base AS (
    SELECT c.cuenta_id, c.codigo,
           left(c.codigo, 6)        AS p6,
           upper(trim(c.nombre))    AS nom,
           COALESCE(u.n, 0)         AS usos
    FROM marts.dim_cuenta c
    LEFT JOIN usos u ON u.cuenta_id = c.cuenta_id
    WHERE c.codigo IS NOT NULL AND c.nombre IS NOT NULL
),
canon AS (
    SELECT p6, nom,
           (array_agg(cuenta_id ORDER BY usos DESC, length(codigo) ASC, cuenta_id ASC))[1] AS canon_id
    FROM base
    GROUP BY p6, nom
)
UPDATE marts.dim_cuenta d
   SET cuenta_canonica_id = cc.cuenta_id,
       codigo_canonico    = cc.codigo,
       nombre_canonico    = cc.nombre
FROM base b
JOIN canon k         ON k.p6 = b.p6 AND k.nom = b.nom
JOIN marts.dim_cuenta cc ON cc.cuenta_id = k.canon_id
WHERE d.cuenta_id = b.cuenta_id;

-- 2) Fallback: cuentas sin código/nombre o singletons → canónico = sí mismas (nunca NULL).
UPDATE marts.dim_cuenta
   SET cuenta_canonica_id = cuenta_id,
       codigo_canonico    = codigo,
       nombre_canonico    = nombre
 WHERE cuenta_canonica_id IS NULL;

-- Control:
--   Reducción por canonicalización (debe ser ~423):
--   SELECT COUNT(DISTINCT codigo) - COUNT(DISTINCT codigo_canonico) FROM marts.dim_cuenta;
--   Ninguna sin canónico (debe ser 0):
--   SELECT COUNT(*) FROM marts.dim_cuenta WHERE cuenta_canonica_id IS NULL;
