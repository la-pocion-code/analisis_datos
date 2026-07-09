-- ============================================================================
-- Modelo estrella — La Poción DW
-- Archivo: sql/marts/01_star_schema.sql
-- Esquema afectado: marts (NUEVO). NO toca raw.odoo_apuntes ni el cron.
--
-- Diseño de TABLA ÚNICA de hechos a grano de línea (account.move.line, todos los
-- move_type, state='posted'). En Power BI se filtra "ventas" con es_venta / cuenta
-- de ingreso (clase 4) y el resumen contable se obtiene agregando por cuenta × mes.
--
-- Convenciones:
--   * Dimensiones: PK = id natural de Odoo (coherente con el UPSERT por id existente).
--   * dim_fecha: PK = fecha_key (AAAAMMDD).
--   * Hecho: PK = id de la línea => recargas idempotentes vía UPSERT.
--   * Jerarquía PUC por longitud de código: N1=clase(1), N2=grupo(2), N4=cuenta(4), N6=subcuenta(6).
--   * Centro de costo = dimensión; planes comerciales (canal, línea producto, tipo producto,
--     país) = columnas degeneradas en el hecho. analytic_distribution crudo se conserva (JSONB).
--
-- NO ejecutar sin OK explícito. Ejecutar una vez con: psql ... -f este_archivo.sql
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS marts;

-- ── dim_fecha ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_fecha (
    fecha_key      INTEGER PRIMARY KEY,      -- AAAAMMDD
    fecha          DATE NOT NULL,
    anio           SMALLINT NOT NULL,
    trimestre      SMALLINT NOT NULL,
    mes            SMALLINT NOT NULL,
    mes_nombre     VARCHAR(12) NOT NULL,
    dia            SMALLINT NOT NULL,
    dia_semana     SMALLINT NOT NULL,        -- 1=lunes ... 7=domingo
    dia_nombre     VARCHAR(12) NOT NULL,
    semana_anio    SMALLINT NOT NULL,
    es_fin_semana  BOOLEAN NOT NULL,
    periodo_aaaamm INTEGER NOT NULL          -- AAAAMM
);

-- ── dim_cuenta (PUC) ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_cuenta (
    cuenta_id        BIGINT PRIMARY KEY,     -- account.account id
    codigo           VARCHAR(20),
    nombre           VARCHAR(512),
    clase_codigo     CHAR(1),                -- N1 (1 díg)
    grupo_codigo     CHAR(2),                -- N2 (2 díg)
    cuenta_codigo    CHAR(4),                -- N4 (4 díg)
    subcuenta_codigo CHAR(6),                -- N6 (6 díg)
    nivel_movimiento TEXT,                   -- línea del reporte de Odoo (es_CO); todas las clases
    seccion          TEXT,                   -- raíz del reporte: ACTIVOS/PASIVO/PATRIMONIO/Ingresos/Gastos/Costos…
    subseccion       TEXT,                   -- subtotal: Activos corrientes/no corrientes, Pasivos corrientes…
                                             -- Los tres se derivan de account.report (Balance+Estado de Resultados,
                                             -- es_CO) vía etl_dw_marts.cargar_clasificacion_reportes. Ver 12_estados_financieros.sql
    naturaleza       VARCHAR(10),            -- Débito / Crédito según clase
    tipo_cuenta      VARCHAR(64),            -- account_type (NULL hasta extender)
    -- Canonicalización PUC (no destructivo; ver sql/marts/11_puc_canonico.sql). El hecho conserva
    -- el cuenta_id real de Odoo; en BI se agrupa por codigo_canonico. Canónico = variante MÁS usada
    -- de misma subcuenta (6 díg) + mismo nombre normalizado (unifica los códigos 8 vs 9 díg).
    cuenta_canonica_id BIGINT,               -- cuenta_id de la variante canónica (= self si no hay duplicado)
    codigo_canonico    VARCHAR(20),
    nombre_canonico    TEXT
);

-- ── dim_tercero ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_tercero (
    tercero_id     BIGINT PRIMARY KEY,       -- res.partner id
    nombre         TEXT,
    identificacion TEXT,                     -- x_studio_related_field_9er_1ipkj4lvp (NIT)
    tipo_cliente   TEXT,                     -- move_id/partner_type_id
    ciudad         TEXT,
    departamento   TEXT,
    pais           TEXT
);

-- ── dim_diario ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_diario (
    diario_id BIGINT PRIMARY KEY,            -- account.journal id
    codigo    VARCHAR(20),
    nombre    TEXT,
    tipo      VARCHAR(32)                    -- sale, purchase, bank, general, ...
);

-- ── dim_producto ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_producto (
    producto_id BIGINT PRIMARY KEY,          -- product.product id
    codigo      TEXT,                        -- default_code
    nombre      TEXT,
    categoria   TEXT                         -- categ_id (nombre)
);

-- ── dim_vendedor (de move.invoice_user_id) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_vendedor (
    vendedor_id BIGINT PRIMARY KEY,          -- res.users id
    nombre      TEXT
);

-- ── dim_empresa (res.company) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marts.dim_empresa (
    empresa_id BIGINT PRIMARY KEY,           -- res.company id (PCN Poción, Hector Fabio, ...)
    nombre     TEXT
);

-- ── dim_centro_costo ─────────────────────────────────────────────────────────
-- Fuente: 100% Odoo (account.analytic.account). SIN insumos locales.
CREATE TABLE IF NOT EXISTS marts.dim_centro_costo (
    centro_costo_id BIGINT PRIMARY KEY,      -- account.analytic.account id
    codigo   VARCHAR(20),                    -- code (puede venir vacío en Odoo)
    nombre   TEXT,                           -- name
    plan     TEXT,                           -- plan_id (nombre del plan analítico)
    activo   BOOLEAN,                         -- active
    empresa_id BIGINT                         -- company_id (NULL = compartido)
);

-- ── fact_movimiento_contable ─────────────────────────────────────────────────
-- Grano = línea de account.move.line (todos los move_type, state='posted').
CREATE TABLE IF NOT EXISTS marts.fact_movimiento_contable (
    linea_id          BIGINT PRIMARY KEY,                                 -- account.move.line id
    factura_id        BIGINT,                                             -- move_id
    numero            TEXT,                                               -- move_name
    referencia        TEXT,                                               -- ref
    estado            VARCHAR(20),                                        -- move state (posted)
    tipo_movimiento   VARCHAR(20),                                        -- move_type
    es_venta          BOOLEAN,                                            -- out_invoice / out_refund
    -- claves dimensionales
    fecha_key         INTEGER REFERENCES marts.dim_fecha(fecha_key),      -- date (contable)
    fecha_factura_key INTEGER REFERENCES marts.dim_fecha(fecha_key),      -- invoice_date
    cuenta_id         BIGINT  REFERENCES marts.dim_cuenta(cuenta_id),
    tercero_id        BIGINT  REFERENCES marts.dim_tercero(tercero_id),
    producto_id       BIGINT  REFERENCES marts.dim_producto(producto_id),
    vendedor_id       BIGINT  REFERENCES marts.dim_vendedor(vendedor_id), -- NULL por ahora
    diario_id         BIGINT  REFERENCES marts.dim_diario(diario_id),
    empresa_id        BIGINT  REFERENCES marts.dim_empresa(empresa_id),
    centro_costo_id   BIGINT  REFERENCES marts.dim_centro_costo(centro_costo_id),
    -- planes analíticos comerciales (columnas degeneradas)
    canal             TEXT,
    linea_producto    TEXT,
    tipo_producto     TEXT,
    pais_analitico    TEXT,
    -- medidas
    cantidad          NUMERIC,
    precio_unitario   NUMERIC,
    subtotal          NUMERIC,                                            -- price_subtotal
    debito            NUMERIC,
    credito           NUMERIC,
    saldo             NUMERIC,                                            -- balance
    venta_neta        NUMERIC,                                            -- credit - debit
    -- ventas: exclusión de reversos totales (ver 05_calidad_correcciones.sql)
    estado_pago           VARCHAR(20),                                    -- move.payment_state
    reversed_factura_id   BIGINT,                                         -- move.reversed_entry_id
    es_reverso            BOOLEAN DEFAULT FALSE,                          -- excluida de ventas
    -- cartera (CxC) a nivel de línea (ver 06_cartera_en_hecho.sql)
    saldo_pendiente       NUMERIC,                                        -- amount_residual
    es_cxc                BOOLEAN DEFAULT FALSE,                          -- account_type='asset_receivable'
    fecha_vencimiento_key INTEGER REFERENCES marts.dim_fecha(fecha_key),  -- date_maturity
    -- crudo para repartos pendientes
    analytic_distribution JSONB,
    _loaded_at        TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota')  -- hora Colombia
);

CREATE INDEX IF NOT EXISTS ix_fmc_fecha   ON marts.fact_movimiento_contable (fecha_key);
CREATE INDEX IF NOT EXISTS ix_fmc_cuenta  ON marts.fact_movimiento_contable (cuenta_id);
CREATE INDEX IF NOT EXISTS ix_fmc_tercero ON marts.fact_movimiento_contable (tercero_id);
CREATE INDEX IF NOT EXISTS ix_fmc_venta   ON marts.fact_movimiento_contable (es_venta);
CREATE INDEX IF NOT EXISTS ix_fmc_empresa ON marts.fact_movimiento_contable (empresa_id);
CREATE INDEX IF NOT EXISTS ix_fmc_cxc     ON marts.fact_movimiento_contable (es_cxc);
CREATE INDEX IF NOT EXISTS ix_fmc_reverso ON marts.fact_movimiento_contable (es_reverso);

-- Nota: la cartera (CxC) NO tiene tabla propia; sale del mismo hecho filtrando es_cxc
-- y sumando saldo_pendiente. Ver vista v_cartera en 06_cartera_en_hecho.sql.

-- ============================================================================
-- Poblado de dim_fecha (referencia; ejecutar tras crear las tablas).
-- ============================================================================
-- INSERT INTO marts.dim_fecha
-- SELECT
--     (EXTRACT(YEAR FROM d)*10000 + EXTRACT(MONTH FROM d)*100 + EXTRACT(DAY FROM d))::int,
--     d::date,
--     EXTRACT(YEAR FROM d)::smallint, EXTRACT(QUARTER FROM d)::smallint,
--     EXTRACT(MONTH FROM d)::smallint, INITCAP(TO_CHAR(d,'TMMonth')),
--     EXTRACT(DAY FROM d)::smallint, EXTRACT(ISODOW FROM d)::smallint,
--     INITCAP(TO_CHAR(d,'TMDay')), EXTRACT(WEEK FROM d)::smallint,
--     (EXTRACT(ISODOW FROM d) >= 6),
--     (EXTRACT(YEAR FROM d)*100 + EXTRACT(MONTH FROM d))::int
-- FROM generate_series(DATE '2024-01-01', DATE '2034-12-31', INTERVAL '1 day') AS g(d)
-- ON CONFLICT (fecha_key) DO NOTHING;
