-- ============================================================================
-- Esquema `agente` + rol de solo lectura para el agente financiero conversacional.
-- Archivo: sql/marts/20_agente.sql  (ejecutar DESPUÉS de 01..18). Idempotente.
--
-- Soporta el microservicio `reportes-api` y n8n:
--   * agente.usuarios_autorizados : whitelist identidad→rol→empresas (gerencia/contabilidad).
--   * agente.log_consultas        : auditoría de cada consulta del agente.
--   * rol agente_ro               : SELECT-only sobre las vistas de reporte de `marts`.
--
-- Confidencialidad (política La Poción): el agente solo responde a identidades en la
-- whitelist; el rol RO no tiene acceso a DML/DDL ni a tablas fuera de las vistas expuestas.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS agente;

-- ── Whitelist de usuarios autorizados ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS agente.usuarios_autorizados (
    id           BIGSERIAL PRIMARY KEY,
    canal        TEXT        NOT NULL,              -- 'google_chat' | 'whatsapp' | 'gmail'
    identidad    TEXT        NOT NULL,              -- email (Chat/Gmail) o teléfono E.164 (WhatsApp)
    nombre       TEXT,
    rol          TEXT        NOT NULL,              -- 'gerencia' | 'contabilidad'
    empresas     INTEGER[]   NOT NULL DEFAULT '{1,8}',  -- empresa_id permitidas (1=HFA, 8=PCN)
    activo       BOOLEAN     NOT NULL DEFAULT TRUE,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (canal, identidad)
);

COMMENT ON TABLE agente.usuarios_autorizados IS
  'Whitelist de identidades autorizadas a consultar el agente financiero.';

-- ── Auditoría de consultas ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agente.log_consultas (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    canal        TEXT,
    identidad    TEXT,
    autorizado   BOOLEAN,
    pregunta     TEXT,
    herramienta  TEXT,                              -- tool/endpoint invocado
    params       JSONB,
    ok           BOOLEAN,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS ix_log_consultas_ts ON agente.log_consultas (ts);
CREATE INDEX IF NOT EXISTS ix_log_consultas_ident ON agente.log_consultas (identidad);

-- ── Rol de SOLO LECTURA para el microservicio / tool SQL ─────────────────────
-- La contraseña se define fuera de este script (ALTER ROLE ... PASSWORD) o en Railway.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agente_ro') THEN
        CREATE ROLE agente_ro LOGIN;
    END IF;
END $$;

-- Sin acceso por defecto a nada; concedemos SOLO lo necesario.
GRANT USAGE ON SCHEMA marts TO agente_ro;

-- Vistas de reporte expuestas al agente (whitelist). Ajustar si se agregan vistas.
GRANT SELECT ON
    marts.v_balance_comprobacion,
    marts.v_ventas,
    marts.v_ventas_producto
TO agente_ro;

-- El microservicio escribe la auditoría con su propio rol (no agente_ro).
-- Si se desea que agente_ro registre, conceder INSERT explícito:
GRANT USAGE ON SCHEMA agente TO agente_ro;
GRANT SELECT ON agente.usuarios_autorizados TO agente_ro;
GRANT INSERT ON agente.log_consultas TO agente_ro;
GRANT USAGE, SELECT ON SEQUENCE agente.log_consultas_id_seq TO agente_ro;

-- NOTA: NO se hace GRANT sobre marts.fact_movimiento_contable ni dim_* crudas → el tool
-- SQL SELECT-only solo puede leer las vistas de arriba (defensa en profundidad).
