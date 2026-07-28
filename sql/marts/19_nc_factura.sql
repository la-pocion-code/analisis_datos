-- ============================================================================
-- Puente NOTA CRÉDITO → FACTURA original. Idempotente.
-- Archivo: sql/marts/19_nc_factura.sql  (ejecutar DESPUÉS de 14). 100% desde Odoo.
--
-- Problema que resuelve: una NC restaba en SU propio mes, no en el de la factura que corrige.
-- Ej.: NCR1858 (04-mar-2026) corrige FEVY80693 (06-nov-2025) → deprimía marzo-2026 e inflaba
-- noviembre-2025. Medido en 2025-2026: 777 NC caen en un mes distinto al de su factura,
-- por ~6.584 millones mal atribuidos.
--
-- Lo puebla `enlazar_notas_credito()` en el ETL con una CASCADA DE EVIDENCIA, de la más fuerte a la
-- más débil (el método usado queda en `metodo_enlace`, para poder auditar cada par):
--   1. `reversed_entry` — `account.move.reversed_entry_id`: Odoo dice qué factura reversa.
--   2. `ref`           — el número de la factura aparece en la referencia de la NC (mismo cliente y
--                        un único candidato válido; si hay varios es ambiguo y se pasa al método 3).
--   3. `conciliacion`  — `account.partial.reconcile`. Es lo único disponible para la mayoría (NCR1858
--                        tiene `ref` y `reversed_entry_id` NULL y aun así concilia 49.944.031 contra
--                        FEVY80693), pero es el más DÉBIL: conciliar significa "se aplicó contra" y no
--                        siempre "corrige a" (una NC puede abonarse a la factura abierta más antigua).
--
-- Una NC puede repartirse entre varias facturas: se guarda una fila por factura con su `proporcion`
-- (suma 1 por NC); los métodos 1 y 2 apuntan a una sola factura → proporcion 1. Solo se consideran
-- FACTURAS de venta (`out_invoice`); lo conciliado contra notas débito o pagos se ignora (ej. NDY21 en
-- NCR1858). ⚠ Las NOTAS DÉBITO también son `out_invoice`: se distinguen por el DIARIO.
-- ============================================================================

CREATE TABLE IF NOT EXISTS marts.map_nc_factura (
    nc_factura_id BIGINT  NOT NULL,   -- account.move de la NOTA CRÉDITO (= fact.factura_id)
    factura_id    BIGINT  NOT NULL,   -- account.move de la FACTURA original
    proporcion    NUMERIC NOT NULL,   -- share de lo conciliado con esa factura (suma 1 por NC)
    fecha_venta   DATE    NOT NULL,   -- invoice_date de la FACTURA original
    PRIMARY KEY (nc_factura_id, factura_id)
);

-- Aditivo para tablas ya creadas: de qué evidencia salió el par (ver cascada arriba).
ALTER TABLE marts.map_nc_factura
    ADD COLUMN IF NOT EXISTS metodo_enlace VARCHAR(32);

CREATE INDEX IF NOT EXISTS ix_map_nc_factura_nc ON marts.map_nc_factura (nc_factura_id);
