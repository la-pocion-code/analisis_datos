-- ============================================================================
-- Puente NOTA CRÉDITO → FACTURA original. Idempotente.
-- Archivo: sql/marts/19_nc_factura.sql  (ejecutar DESPUÉS de 14). 100% desde Odoo.
--
-- Problema que resuelve: una NC restaba en SU propio mes, no en el de la factura que corrige.
-- Ej.: NCR1858 (04-mar-2026) corrige FEVY80693 (06-nov-2025) → deprimía marzo-2026 e inflaba
-- noviembre-2025. Medido en 2025-2026: 777 NC caen en un mes distinto al de su factura,
-- por ~6.584 millones mal atribuidos.
--
-- El enlace NC→factura vive SOLO en la CONCILIACIÓN (`account.partial.reconcile`): NCR1858 tiene
-- `ref` y `reversed_entry_id` en NULL, pero concilia 49.944.031 contra FEVY80693. Por eso lo puebla
-- `enlazar_notas_credito()` en el ETL leyendo las conciliaciones (no basta con `ref`).
--
-- Una NC puede repartirse entre varias facturas: se guarda una fila por factura con su `proporcion`
-- (suma 1 por NC). Solo se considera lo conciliado contra FACTURAS de venta (`out_invoice`); lo que
-- concilia contra notas débito o pagos se ignora (ej. NDY21 en NCR1858).
-- ============================================================================

CREATE TABLE IF NOT EXISTS marts.map_nc_factura (
    nc_factura_id BIGINT  NOT NULL,   -- account.move de la NOTA CRÉDITO (= fact.factura_id)
    factura_id    BIGINT  NOT NULL,   -- account.move de la FACTURA original
    proporcion    NUMERIC NOT NULL,   -- share de lo conciliado con esa factura (suma 1 por NC)
    fecha_venta   DATE    NOT NULL,   -- invoice_date de la FACTURA original
    PRIMARY KEY (nc_factura_id, factura_id)
);

CREATE INDEX IF NOT EXISTS ix_map_nc_factura_nc ON marts.map_nc_factura (nc_factura_id);
