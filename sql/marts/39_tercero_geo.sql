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
-- Medido tras el backfill (2026-10-10): 2.152 listables entre los 130.029 clientes con venta, y
-- 0 terceros no listables con nombre o calle en `v_lk_tercero_geo` (el control negativo).
-- Código DANE: 127.146 de 130.029 clientes con venta (97,78 %); los 2.863 restantes no tienen
-- `city_id` y su ciudad es texto libre — el mapa los publica como «sin ubicar», no los esconde.
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


-- ── La geocodificación: caché por DIRECCIÓN y asignación por TERCERO (fase 3) ─
-- La escribe `geocodificar_terceros.py` (Geoapify, cupo gratis). Va en este archivo y no en uno
-- aparte porque las vistas de abajo la leen: si viviera en un `40`, re-ejecutar este la pisaría.
-- ⛔ NO Google: sus términos prohíben guardar la coordenada más de 30 días (también en un CSV).
-- Geoapify (OpenStreetMap) lo permite por escrito, con la atribución junto al dato.
CREATE TABLE IF NOT EXISTS marts.bi_geo_direccion (
    llave             TEXT PRIMARY KEY,          -- md5 de la consulta normalizada
    consulta          TEXT NOT NULL,             -- 'Calle 14 # 7-85, IPIALES, Nariño, Colombia'
    proveedor         TEXT NOT NULL,             -- 'geoapify'
    estado            TEXT NOT NULL,             -- ok | impreciso | fuera_de_municipio | baja_confianza | sin_resultado
    latitud           NUMERIC(9, 6),
    longitud          NUMERIC(9, 6),
    tipo_resultado    TEXT,                      -- building | street | suburb | city …
    confianza         NUMERIC(5, 4),
    tipo_coincidencia TEXT,                      -- full_match | match_by_street | …
    barrio            TEXT,                      -- suburb/neighbourhood de OSM
    comuna_localidad  TEXT,                      -- district/city_district de OSM
    ciudad            TEXT,
    departamento      TEXT,
    codigo_postal     TEXT,
    place_id          TEXT,
    consultado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- La incertidumbre en metros (mitad de la diagonal del `bbox` de lo que coincidió). Entra el
-- 2026-10-13 tras la consulta de prueba: la placa colombiana casi nunca está en OSM y lo normal
-- es que coincida la CALLE, cuyo punto medio no ubica el barrio si la calle es larga.
ALTER TABLE marts.bi_geo_direccion ADD COLUMN IF NOT EXISTS incertidumbre_m INTEGER;

COMMENT ON TABLE marts.bi_geo_direccion IS
  'Cache de geocodificacion por direccion (Geoapify, datos (c) OpenStreetMap, ODbL). Solo '
  'estado=ok se usa. NO se concede: la coordenada de un consumidor es dato personal.';

CREATE TABLE IF NOT EXISTS marts.bi_geo_tercero (
    tercero_id     BIGINT PRIMARY KEY,
    llave          TEXT,                         -- NULL: la dirección no es geocodificable
    hash_direccion TEXT NOT NULL,                -- si cambia la dirección, se vuelve a consultar
    barrio_escrito TEXT,                         -- el que el cliente escribió («… BRR CENTRO»)
    asignado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bi_geo_tercero_llave ON marts.bi_geo_tercero (llave);


-- ── v_tercero_geo_base: la regla, en UN sitio (NO se concede a nadie) ───────
-- La leen la vista de lookup y la MV. Si la regla de `es_listable` viviera en las dos, divergirían.
-- ⚠ Las columnas de la geocodificación van AL FINAL (CREATE OR REPLACE solo añade al final).
--   Solo cuenta un resultado con `estado = 'ok'`: uno fuera del municipio del cliente se guarda
--   en la caché para no volver a pagarlo, pero no ubica a nadie.
-- El BARRIO, por fuentes: el de OSM si hay resultado bueno, si no el que el cliente escribió; y
--   `fuente_barrio` lo dice. (El polígono oficial de Bogotá/Medellín/Cali entrará por delante.)
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
                                                                          AS es_listable,
       d.latitud,
       d.longitud,
       d.tipo_resultado                                                   AS precision_geo,
       d.comuna_localidad                                                 AS comuna,
       COALESCE(d.barrio, g.barrio_escrito)                               AS barrio,
       CASE WHEN d.barrio IS NOT NULL THEN 'osm'
            WHEN g.barrio_escrito IS NOT NULL THEN 'escrito' END          AS fuente_barrio,
       CASE WHEN d.llave IS NOT NULL THEN d.proveedor END                 AS fuente_geo
FROM marts.dim_tercero t
LEFT JOIN marts.bi_geo_tercero g ON g.tercero_id = t.tercero_id
LEFT JOIN marts.bi_geo_direccion d ON d.llave = g.llave AND d.estado = 'ok';

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
       -- ⚠⚠ Coordenada, comuna y barrio: el MISMO `CASE WHEN es_listable` que el nombre. Un
       --    consumidor con su barrio al lado del `tercero_id` (que casa con el nombre de
       --    `v_lk_tercero`) dejaría de estar «solo agregado». Su barrio cuenta en la MV, no aquí.
       -- El cast a NUMERIC(9,6) no es cosmético: CREATE OR REPLACE exige el tipo EXACTO, y un
       -- CASE devuelve `numeric` a secas.
       (CASE WHEN b.es_listable THEN b.latitud  END)::NUMERIC(9, 6)       AS latitud,
       (CASE WHEN b.es_listable THEN b.longitud END)::NUMERIC(9, 6)       AS longitud,
       CASE WHEN b.es_listable THEN b.precision_geo END                   AS precision_geo,
       CASE WHEN b.es_listable THEN b.comuna END                          AS comuna,
       CASE WHEN b.es_listable THEN b.barrio END                          AS barrio,
       CASE WHEN b.es_listable THEN b.fuente_geo END                      AS fuente_geo
FROM marts.v_tercero_geo_base b;

COMMENT ON VIEW marts.v_lk_tercero_geo IS
  'Geografia por tercero. Nombre, direccion, coordenada, comuna y barrio SOLO de los '
  'listables (NIT o canal mayorista/distribuidor); las personas naturales salen con ciudad y '
  'departamento y nada mas. Sin NIT, telefono ni correo. Coordenadas (c) OpenStreetMap.';


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
       b.comuna,
       b.barrio,
       b.fuente_barrio,
       -- La CELDA del mapa de densidad: ~1 km (2 decimales). Es lo más fino que la intranet puede
       -- pintar de un consumidor, y solo con un mínimo de clientes por celda.
       round(b.latitud, 2)                                                AS celda_lat,
       round(b.longitud, 2)                                               AS celda_lng,
       SUM(m.venta)                                                       AS venta,
       SUM(m.unidades)                                                    AS unidades
FROM marts.mv_ventas_mes m
JOIN marts.v_tercero_geo_base b ON b.tercero_id = m.tercero_id
WHERE m.tercero_id > 0
GROUP BY m.periodo_aaaamm, m.anio, m.mes, m.tercero_id, m.categoria,
         b.es_listable, b.pais_codigo, b.pais, b.departamento, b.codigo_dane_departamento,
         b.codigo_dane_municipio, b.ciudad, b.comuna, b.barrio, b.fuente_barrio,
         round(b.latitud, 2), round(b.longitud, 2);

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
