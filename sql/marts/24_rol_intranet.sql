-- ============================================================================
-- Rol de SOLO LECTURA para los dashboards de la INTRANET + vistas de consulta.
-- Archivo: sql/marts/24_rol_intranet.sql  (ejecutar DESPUÉS de 23). Idempotente.
--
-- Espejo de 20_agente.sql: el consumidor externo NO recibe acceso al hecho
-- (`fact_movimiento_contable`) ni a las dimensiones crudas; solo a una whitelist
-- de objetos pensados para él. Defensa en profundidad: si mañana alguien inyecta
-- SQL en un endpoint de la intranet, lo máximo que puede leer es esto.
--
-- ── ¿POR QUÉ VISTAS DE LOOKUP EN VEZ DE LAS DIMENSIONES? ─────────────────────
-- Los desgloses de los tableros necesitan el NOMBRE del cliente/producto/
-- vendedor, no toda la dimensión. `dim_tercero` tiene ~207k filas con
-- identificación (NIT), teléfono y email: datos personales que un tablero de
-- ventas no necesita. Se exponen vistas recortadas con solo lo descriptivo.
--
-- ── CONTRASEÑA (NO va en el repo) ────────────────────────────────────────────
-- Este script crea el rol SIN contraseña. Asignarla fuera de aquí:
--     ALTER ROLE intranet_ro PASSWORD '<generada>';
-- y luego ponerla en la variable MARTS_DATABASE_URL del servicio de la intranet
-- en Railway:
--     postgresql://intranet_ro:<pass>@<host>:<puerto>/railway
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- Vistas de lookup: solo columnas descriptivas, sin datos personales.
-- ════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW marts.v_lk_tercero AS
SELECT t.tercero_id,
       t.nombre,
       t.tipo_cliente,
       t.ciudad,
       t.departamento,
       t.pais,
       t.cliente_padre
FROM marts.dim_tercero t;

COMMENT ON VIEW marts.v_lk_tercero IS
  'Lookup de clientes para los tableros: solo lo descriptivo. Excluye a '
  'propósito identificacion (NIT), telefono, email y etiqueta.';

CREATE OR REPLACE VIEW marts.v_lk_producto AS
SELECT p.producto_id,
       p.codigo,
       p.nombre,
       p.nombre_comercial,
       COALESCE(NULLIF(btrim(p.nombre_comercial), ''), p.nombre) AS etiqueta,
       p.categoria,
       p.es_kit
FROM marts.dim_producto p;

COMMENT ON VIEW marts.v_lk_producto IS
  'Lookup de productos para los tableros. `etiqueta` = nombre_comercial si '
  'existe, si no el nombre técnico (es lo que se muestra en los gráficos).';

CREATE OR REPLACE VIEW marts.v_lk_vendedor AS
SELECT v.vendedor_id, v.nombre
FROM marts.dim_vendedor v;

CREATE OR REPLACE VIEW marts.v_lk_empresa AS
SELECT e.empresa_id, e.nombre
FROM marts.dim_empresa e;


-- ════════════════════════════════════════════════════════════════════════════
-- Rol de solo lectura.
-- ════════════════════════════════════════════════════════════════════════════
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'intranet_ro') THEN
        CREATE ROLE intranet_ro LOGIN;
    END IF;
END $$;

-- Sin acceso por defecto a nada; se concede SOLO lo necesario.
GRANT USAGE ON SCHEMA marts TO intranet_ro;

-- Vistas materializadas de dashboards (hoja Ventas — fase 1).
GRANT SELECT ON
    marts.mv_ventas_dia,
    marts.mv_ventas_mes,
    marts.mv_ventas_kpi_mes,
    marts.mv_presupuesto_mes,
    marts.mv_ventas_presupuesto_mes   -- ventas vs presupuesto por mes × categoría
TO intranet_ro;

-- Bitácora de refresco: la intranet la lee para invalidar su caché y mostrar
-- "datos actualizados hace X". Solo SELECT (la escribe el ETL con su rol).
GRANT SELECT ON marts.bi_mv_refresh TO intranet_ro;

-- Lookups descriptivos.
GRANT SELECT ON
    marts.v_lk_tercero,
    marts.v_lk_producto,
    marts.v_lk_vendedor,
    marts.v_lk_empresa
TO intranet_ro;

-- En PostgreSQL una VISTA accede a sus tablas base con los privilegios de su
-- DUEÑO, no de quien consulta (salvo que se cree con `security_invoker = true`,
-- PG15+). Por eso basta el GRANT sobre la vista: NO hace falta —ni se debe—
-- conceder SELECT sobre marts.dim_tercero, y el rol sigue sin poder leer las
-- columnas que la vista no expone.
-- ⚠ Las MATERIALIZED VIEWS son tablas físicas: el GRANT de arriba es directo y
-- suficiente, y no arrastra acceso a v_ventas_bi ni al hecho contable.

-- Cinturón de seguridad: revocar cualquier privilegio de escritura heredado de
-- PUBLIC y asegurar que no puede crear objetos en el esquema.
REVOKE CREATE ON SCHEMA marts FROM intranet_ro;

-- NOTA: NO se concede SELECT sobre marts.fact_movimiento_contable, dim_cuenta,
-- v_ventas_bi, v_ventas_explotada ni las bi_* crudas. Cuando se construyan las
-- hojas de Nielsen / cuentas clave / cartera / contabilidad, se añadirán aquí
-- sus MV correspondientes (nunca las tablas base).
