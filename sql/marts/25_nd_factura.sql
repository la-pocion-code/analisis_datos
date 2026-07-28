-- ============================================================================
-- Puente NOTA DÉBITO → FACTURA que revive. Idempotente.
-- Archivo: sql/marts/25_nd_factura.sql  (ejecutar DESPUÉS de 19). 100% desde Odoo.
--
-- REGLA DE NEGOCIO: una nota débito NO es venta ("ventas menos devoluciones"), SALVO cuando ANULA una
-- NOTA CRÉDITO: si la devolución se anuló, no hubo devolución, y hay que reponer ese valor **en el mes
-- de la factura original**, no en el de la nota débito.
--
-- La cadena es ND → NC → FACTURA. Ejemplo real (los tres por 696.586.553,17):
--   FE7281          09-mar-2026  out_invoice  ref 'OC 4503324138'
--   RINV/2026/0062  09-mar-2026  out_refund   ref 'Reversión de: FE7281, CORRECCION DE FACTURA'
--   NDY1            24-abr-2026  out_invoice  ref 'RINV/2026/0062, anulación nota crédito'  (diario NDY)
-- Sin este puente NDY1 sumaba +612,9M en ABRIL; con él suma en MARZO (fecha de FE7281).
--
-- El enlace sale del `ref` de la ND, con formato fijo "<numero_documento>, <motivo>" (41 de 44 ND de
-- 2025-2026). Lo puebla `enlazar_notas_debito()` en el ETL: resuelve el número a un account.move del
-- MISMO cliente y empresa; si es `out_refund` busca su factura en `marts.map_nc_factura` (la de mayor
-- `proporcion`) o, si no está, por el `reversed_entry_id` de la NC.
--
-- Las ND que NO entran aquí quedan FUERA de ventas (ver `marts.v_notas_debito_excluidas`):
--   * las que apuntan a una FACTURA en vez de a una NC = cargo extra real
--     (ej. NDY4 49,2M "FE7281, Ajuste por precio");
--   * las que no traen `ref` (ej. NDEXP1/NDEXP2 de ene-2025).
-- Diarios de nota débito: `dim_diario.codigo IN ('NDY','NDEXP')` (4 diarios, `tipo='sale'`). Se usa el
-- CÓDIGO y no el nombre porque es más estable.
-- ============================================================================

CREATE TABLE IF NOT EXISTS marts.map_nd_factura (
    nd_factura_id BIGINT  NOT NULL,   -- account.move de la NOTA DÉBITO (= fact.factura_id)
    nc_factura_id BIGINT  NOT NULL,   -- account.move de la NOTA CRÉDITO que anula
    factura_id    BIGINT  NOT NULL,   -- account.move de la FACTURA que revive
    fecha_venta   DATE    NOT NULL,   -- invoice_date de esa FACTURA
    metodo_enlace VARCHAR(32),        -- 'puente_nc' | 'reversed_entry'
    PRIMARY KEY (nd_factura_id)
);

CREATE INDEX IF NOT EXISTS ix_map_nd_factura_nc ON marts.map_nd_factura (nc_factura_id);
