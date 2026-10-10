-- ============================================================================
-- 39_tercero_geo.sql — GEOGRAFÍA DE LOS CLIENTES, para los mapas de calor (2026-10-10)
--
-- Idempotente. ⚠ ORDEN AL APLICAR: este archivo ANTES que el código del ETL que lo puebla
-- (`upsert` escribe todas las columnas del DataFrame: si el ETL nuevo corre contra la tabla vieja,
-- falla por columna inexistente), y DESPUÉS re-ejecutar `24_rol_intranet.sql`, que es donde viven
-- los GRANT a `intranet_ro` — y que a su vez FALLA si este archivo no se ha aplicado, porque
-- concede sobre objetos que crea este.
-- Luego: `python etl_dw_marts.py --backfill-terceros` (~5 min; `--dims` NO puebla un campo nuevo) y
-- `python refrescar_mv_dashboards.py --mv mv_clientes_geo_mes`.
--
-- POR QUÉ EXISTE
-- --------------
-- Se quiere un mapa de calor de los clientes que compran, por país, departamento, ciudad y
-- comuna. País, departamento y ciudad salen de Odoo SIN geocodificar: basta una llave estable por
-- zona (ISO del país, código DIVIPOLA del municipio) con la que casar los polígonos oficiales. La
-- comuna y un mapa de puntos SÍ necesitan la coordenada de cada cliente: esas columnas quedan
-- reservadas en NULL (fase 3, que se paga por dirección y espera el OK al coste).
--
-- MEDIDO el 2026-10-10 con `diagnosticar_direcciones_terceros.py` (130.028 clientes con venta):
--   · 99,99 % en Colombia (130.016); EC, PE, DO y US tienen UN cliente cada uno.
--   · street poblado al 99,95 %; con al menos un dígito (geocodificable) 122.438 = 94,16 %.
--   · city_id al 97,79 % → código DANE (`res.city.zipcode`) para casi todos.
--   · partner_latitude/longitude EXISTEN en Odoo pero están en 0 salvo en 4: nadie los geocodificó.
--   · 115.801 direcciones distintas: lo que costaría la fase 3 (US$4/1.000 en Esri ≈ US$463).
--
-- ⚠⚠ DATO PERSONAL — QUIÉN SE LISTA CON DIRECCIÓN Y QUIÉN SOLO AGREGADO
-- --------------------------------------------------------------------
-- La dirección de un CONSUMIDOR (persona natural) es dato personal (Ley 1581 de 2012). Decisión de
-- William del 2026-10-10: las personas naturales solo aparecen AGREGADAS (conteos por zona), sin
-- calle ni nombre; los negocios sí con su dirección. El criterio se resuelve AQUÍ, en el SQL, y no
-- en quien consume: si cada consumidor lo reimplementara, el primero que se equivocara filtraría.
--
-- `es_listable` = NIT (tipo_documento '31' nacional o '50' de otro país)
--              OR canal MAYORISTA NV / DISTRIBUIDOR (tenderos que facturan con cédula).
-- ⚠ NO es `is_company`: medido, el 83 % de los MAYORISTA NV está en False. Ni `person_type` solo:
--   829 clientes del canal CLIENTE con ticket de consumidor están marcados '1' (jurídica) con
--   CÉDULA, una contradicción de captura que los listaría por error.
-- ⚠ Es una LISTA CERRADA a propósito (fail-closed): un canal B2B nuevo con clientes de cédula
--   queda SIN listar —una fila de menos— en vez de filtrarse. Con NIT se lista igual.
-- Medido: ~2.200 clientes listables, que concentran la venta B2B.
--
-- ⛔ `dim_tercero` sigue NEGADA a `intranet_ro`: la vista accede con los privilegios de su dueño.
-- ⛔ Ni NIT, ni teléfono, ni correo en ninguno de los dos objetos.
-- ============================================================================

-- ── dim_tercero: geografía y naturaleza del tercero ─────────────────────────
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS direccion             TEXT;    -- street
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS direccion2            TEXT;    -- street2
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS es_compania           BOOLEAN; -- is_company (NO decide)
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS tipo_persona          TEXT;    -- person_type: '1' jurídica / '2' natural
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS tipo_documento        TEXT;    -- vat_type, código DIAN ('31' NIT, '13' CC…)
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS codigo_dane_municipio TEXT;    -- res.city.zipcode, 5 dígitos DIVIPOLA
ALTER TABLE marts.dim_tercero ADD COLUMN IF NOT EXISTS pais_codigo           TEXT;    -- res.country.code, ISO-2


-- ── v_tercero_geo_base: la regla, en UN sitio (NO se concede a nadie) ───────
-- La leen la vista de lookup y la MV. Si la regla de `es_listable` viviera en las dos, divergirían.
CREATE OR REPLACE VIEW marts.v_tercero_geo_base AS
SELECT t.tercero_id,
       t.nombre,
       t.direccion,
       t.direccion2,
       t.ciudad,
       t.departamento,
       t.pais,
       t.pais_codigo,
       t.codigo_dane_municipio,
       left(t.codigo_dane_municipio, 2)                                   AS codigo_dane_departamento,
       t.tipo_cliente,
       (COALESCE(t.tipo_documento, '') IN ('31', '50')
        OR upper(btrim(COALESCE(t.tipo_cliente, ''))) IN ('MAYORISTA NV', 'DISTRIBUIDOR'))
                                                                          AS es_listable
FROM marts.dim_tercero t;

COMMENT ON VIEW marts.v_tercero_geo_base IS
  'Base de la geografia del cliente con la regla es_listable. NO se concede: la leen '
  'v_lk_tercero_geo y mv_clientes_geo_mes (39_tercero_geo.sql).';


-- ── v_lk_tercero_geo: una fila por tercero, la dirección SOLO si es listable ─
-- ⚠ Con `CREATE OR REPLACE VIEW` solo se AÑADEN columnas al final: por eso las de la fase 3
--   (coordenada, precisión, comuna, barrio, fuente) ya van aquí, en NULL. Cuando se pueblen tienen
--   que seguir el MISMO `CASE WHEN es_listable`, o la coordenada de un consumidor saldría por aquí.
CREATE OR REPLACE VIEW marts.v_lk_tercero_geo AS
SELECT b.tercero_id,
       b.es_listable,
       CASE WHEN b.es_listable THEN b.nombre     END                      AS nombre,
       CASE WHEN b.es_listable THEN b.direccion  END                      AS direccion,
       CASE WHEN b.es_listable THEN b.direccion2 END                      AS direccion2,
       b.ciudad,
       b.departamento,
       b.pais,
       b.pais_codigo,
       b.codigo_dane_municipio,
       b.codigo_dane_departamento,
       b.tipo_cliente,
       NULL::NUMERIC(9, 6)                                                AS latitud,
       NULL::NUMERIC(9, 6)                                                AS longitud,
       NULL::TEXT                                                         AS precision_geo,
       NULL::TEXT                                                         AS comuna,
       NULL::TEXT                                                         AS barrio,
       NULL::TEXT                                                         AS fuente_geo
FROM marts.v_tercero_geo_base b;

COMMENT ON VIEW marts.v_lk_tercero_geo IS
  'Geografia por tercero. Nombre y direccion SOLO de los listables (NIT o canal mayorista/'
  'distribuidor); las personas naturales salen con ciudad y departamento y nada mas. '
  'Sin NIT, telefono ni correo. Latitud/longitud/comuna/barrio reservadas (fase 3).';


-- ── mv_clientes_geo_mes: lo que pinta el MAPA DE CALOR ──────────────────────
-- Grano: mes (fecha de VENTA) × tercero × canal, con la geografía del tercero al lado. Se guarda a
-- grano de tercero y no ya agregado por zona porque «clientes que compraron» es un COUNT DISTINCT:
-- sumar los distintos de cada mes contaría dos veces al que repite, así que la intranet cuenta
-- distintos sobre el rango que le pidan. Medido al crearla (2026-10-10): 185.466 filas para
-- 130.028 clientes con venta, ~1,4 por cliente; se creó en 3,4 s junto con el resto del archivo.
-- ⚠ Sale de `mv_ventas_mes` (la cifra oficial de venta, fecha de venta, neta de devoluciones): por
--   eso se refresca DESPUÉS de ella (va al final de MVS en refrescar_mv_dashboards.py).
-- ⚠ Lleva `tercero_id` de personas naturales, pero ni nombre ni calle: la intranet NUNCA publica
--   filas de esta MV, solo agregados por zona.
DROP MATERIALIZED VIEW IF EXISTS marts.mv_clientes_geo_mes;

CREATE MATERIALIZED VIEW marts.mv_clientes_geo_mes AS
SELECT m.periodo_aaaamm,
       m.anio,
       m.mes,
       m.tercero_id,
       m.categoria,
       b.es_listable,
       b.pais_codigo,
       b.pais,
       b.departamento,
       b.codigo_dane_departamento,
       b.codigo_dane_municipio,
       b.ciudad,
       NULL::TEXT                                                         AS comuna,   -- fase 3
       SUM(m.venta)                                                       AS venta,
       SUM(m.unidades)                                                    AS unidades
FROM marts.mv_ventas_mes m
JOIN marts.v_tercero_geo_base b ON b.tercero_id = m.tercero_id
WHERE m.tercero_id > 0
GROUP BY m.periodo_aaaamm, m.anio, m.mes, m.tercero_id, m.categoria,
         b.es_listable, b.pais_codigo, b.pais, b.departamento, b.codigo_dane_departamento,
         b.codigo_dane_municipio, b.ciudad;

-- Índice único: lo exige REFRESH ... CONCURRENTLY. La geografía depende del tercero, así que no
-- entra en la llave.
CREATE UNIQUE INDEX ux_mv_clientes_geo_mes
    ON marts.mv_clientes_geo_mes (periodo_aaaamm, tercero_id, categoria);
CREATE INDEX ix_mv_clientes_geo_mes_anio      ON marts.mv_clientes_geo_mes (anio);
CREATE INDEX ix_mv_clientes_geo_mes_municipio ON marts.mv_clientes_geo_mes (codigo_dane_municipio);
CREATE INDEX ix_mv_clientes_geo_mes_depto     ON marts.mv_clientes_geo_mes (departamento);

COMMENT ON MATERIALIZED VIEW marts.mv_clientes_geo_mes IS
  'Venta y clientes por mes x tercero x canal con la geografia del tercero, para mapas de '
  'calor. Contar clientes con COUNT(DISTINCT tercero_id): no es aditivo entre meses. '
  'Nunca publicar sus filas: solo agregados por zona (39_tercero_geo.sql).';
