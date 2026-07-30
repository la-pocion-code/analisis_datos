-- ============================================================================
-- Hoja de NIELSEN — panel de mercado (share, competencia y distribución)
-- Archivo: sql/marts/28_nielsen_dashboards.sql
--
-- Tipa y expone `bi_nielsen` (573.013 filas, todo VARCHAR) para las tres
-- sub-páginas del informe: Mercado · Share · Comparar.
--
-- ⚠ Tras ejecutarlo hay que RE-EJECUTAR 24_rol_intranet.sql (los GRANT se pierden
-- al recrear una MV).
--
-- ── LO QUE SE MIDIÓ ANTES DE ESCRIBIR ESTO (2026-07-30, solo lectura) ────────
--
--   filas ............................ 573.013
--   semanas .......................... 164  (2023-05-14 → 2026-06-28)
--   markets .......................... 4    categorías ... 3
--   marcas ........................... 248  fabricantes .. 195
--   ítems ............................ 3.601
--   casts de vtas_valor/unds/dist_num  0 filas mal formadas de 573.013
--   `periods` parseable .............. 100 %
--
-- Cotejo contra el informe (TOTAL COLOMBIA FARMACIAS) — cuadra al segundo decimal:
--
--   total ventas ..... 474.124.569.959   (el informe dice «474 mil M»)
--   total unidades ... 18.586.406        («18,59 mill.»)
--   precio medio ..... 25.509,21         («$25.510,16»)
--   marcas/productos . 207 / 2.589       (idéntico)
--   categorías ....... SHAMPOO 280.703.031.368 (59,20 %)
--                      TRATAMIENTOS   112.474.208.195 (23,72 %)
--                      BALSAMOS        80.947.330.396 (17,07 %)   ← las tres exactas
--   share histórico .. ELVIVE 9,70 · OTRAS 7,10 · DOVE 5,81 · TIO NACHO 5,20
--   share de ELVIVE por mes: 10,10 / 10,23 / 10,23 / 10,25 / 9,94 / 9,15 / 8,97 /
--                      9,13 / 9,75 / 8,60 / 9,68 / 10,17   ← columna por columna
--
-- ── LAS SEIS TRAMPAS DEL DATASET ────────────────────────────────────────────
--
-- 1. ⚠ **LOS 4 MARKETS NO SE SUMAN.** `NEW TOTAL COLOMBIA` (1.998.446.266.413) ya
--    contiene a los otros. El KPI «2.549 mil M» de la hoja *Comparar* del informe es
--    exactamente 1.998.446.266.413 + 474.124.569.959 + 76.507.844.825, o sea el
--    mercado **inflado ~27 %** por sumar universos solapados. La intranet obliga a
--    elegir UN market (`bi_nielsen_market.es_universo_total` marca cuál es el total).
--
-- 2. ⚠ **`Total Colombia Supermercados` no trae valor NI unidades**: 96.675 filas, el
--    100 % de ese market. Solo sirve para distribución (`dist_num`).
--
-- 3. ⚠ **La marca propia solo está medida en 2 de los 4 markets**: FARMACIAS (desde
--    2024-12-15) y ECOMMERCE (desde 2024-12-22). En `NEW TOTAL COLOMBIA` no aparece.
--    Los 4 se exponen igual, porque la hoja también sirve para estudiar mercados
--    donde todavía no se entra — pero un 0 % ahí significa «aquí no nos miden», no
--    «aquí no vendemos», y la intranet tiene que distinguirlo.
--
-- 4. ⚠ **`dist_num` es un PORCENTAJE POR ÍTEM** (0,016 a 69,47), no una fracción ni
--    un share: la suma por categoría/semana/market da **1.814 %**. NO es agregable,
--    así que vive solo en `mv_nielsen_item_semana` y no en la agregada.
--
-- 5. ⚠ **El UPC de Nielsen no casa con ningún código propio** (0 de 18 ítems de la
--    marca). Nielsen es *sell-out* de mercado; las ventas propias son *sell-in*. La
--    hoja es AUTÓNOMA: no se cruza con `mv_ventas_*` ni se intenta.
--
-- 6. La fecha real está DENTRO de `periods`, con el formato
--    «1 sem 26-26 fin 28/06/26» → se toma lo que va tras «fin » como `DD/MM/YY`.
--    El número de semana del texto se ignora a propósito: la fecha de cierre es la
--    que permite ordenar y agrupar por mes/año sin ambigüedad.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- CATÁLOGO DE MARKETS — qué se puede leer en cada universo.
--
-- Va en tabla y no en un CASE del código de la intranet porque son metadatos del
-- contrato con Nielsen: si mañana se contrata el universo total, es una fila.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_nielsen_market (
    market            TEXT PRIMARY KEY,
    etiqueta          TEXT    NOT NULL,
    -- ¿Es el universo que engloba a los demás? Los markets NO se suman entre sí.
    es_universo_total BOOLEAN NOT NULL DEFAULT FALSE,
    -- ¿Trae valor y unidades, o solo distribución?
    tiene_valor       BOOLEAN NOT NULL DEFAULT TRUE,
    orden             SMALLINT NOT NULL DEFAULT 99,
    nota              TEXT
);

COMMENT ON TABLE marts.bi_nielsen_market IS
  'Metadatos de los universos de Nielsen. es_universo_total marca el que engloba a '
  'los demas: los markets NO se suman entre si.';

INSERT INTO marts.bi_nielsen_market
    (market, etiqueta, es_universo_total, tiene_valor, orden, nota) VALUES
    ('NEW TOTAL COLOMBIA', 'Total Colombia', TRUE, TRUE, 1,
     'Universo total: ya incluye a los demas. No sumarlo con ellos.'),
    ('TOTAL COLOMBIA FARMACIAS', 'Farmacias', FALSE, TRUE, 2,
     'Subconjunto del total.'),
    ('TOTAL CO ECOMMERCE', 'E-commerce', FALSE, TRUE, 3,
     'Subconjunto del total.'),
    ('Total Colombia Supermercados', 'Supermercados', FALSE, FALSE, 4,
     'SOLO DISTRIBUCION: Nielsen no entrega valor ni unidades para este universo.')
ON CONFLICT (market) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- MARCAS PROPIAS — para no cablear el literal 'POCION' en el código de la intranet.
-- Si mañana entra otra marca de la casa al panel, es una fila.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_nielsen_marca_propia (
    marca TEXT PRIMARY KEY,
    nota  TEXT
);

COMMENT ON TABLE marts.bi_nielsen_marca_propia IS
  'Marcas de la casa dentro del panel Nielsen. El loader ya unifica TONGOLE en '
  'POCION (ver marca_origen).';

INSERT INTO marts.bi_nielsen_marca_propia (marca, nota) VALUES
    ('POCION', 'El loader unifica TONGOLE dentro de POCION; marca_origen guarda el original.')
ON CONFLICT (marca) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- mv_nielsen_semana — AGREGADA. Es la que alimenta share, ranking y series.
-- Grano: market × semana × categoría × fabricante × marca × presentación × tipo ×
--        promoción.
--
-- Sin `item` en el grano a propósito: con 3.601 ítems × 164 semanas × 4 markets la
-- MV se acercaría al tamaño del origen y no ganaría nada. El detalle por ítem vive
-- en `mv_nielsen_item_semana`.
--
-- ⚠ Sin `dist_num`: es un porcentaje POR ÍTEM y no se puede agregar (ver trampa 4).
-- ⚠ `items` es un COUNT(DISTINCT) al grano de esta vista: **NO es aditivo**.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_nielsen_semana CASCADE;

CREATE MATERIALIZED VIEW marts.mv_nielsen_semana AS
WITH base AS (
    SELECT
        btrim(n.markets)                                              AS market,
        -- La fecha de cierre de la semana, extraida de `periods`.
        to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY')         AS semana,
        COALESCE(NULLIF(btrim(n.categoria),   ''), '(sin categoria)') AS categoria,
        COALESCE(NULLIF(btrim(n.fabricantes), ''), '(sin fabricante)') AS fabricante,
        COALESCE(NULLIF(btrim(n.marcas),      ''), '(sin marca)')     AS marca,
        COALESCE(NULLIF(btrim(n.presentacion_unif), ''), '(sin presentacion)') AS presentacion,
        COALESCE(NULLIF(btrim(n.tipo_unif),   ''), '(sin tipo)')      AS tipo,
        COALESCE(NULLIF(btrim(n.promocionno_promocion_unif), ''), '(sin dato)') AS promocion,
        n.item,
        -- Los tres casts estan verificados: 0 filas mal formadas de 573.013. El
        -- NULLIF es por las 96.675 celdas VACIAS de Supermercados, que son un hueco
        -- real y no un cero: sumarlas como 0 diria que ese universo no vende.
        NULLIF(btrim(n.vtas_valor), '')::NUMERIC                      AS valor,
        NULLIF(btrim(n.vtas_unds),  '')::NUMERIC                      AS unidades
    FROM marts.bi_nielsen n
    WHERE n.periods IS NOT NULL
)
SELECT
    market,
    semana,
    EXTRACT(YEAR  FROM semana)::SMALLINT                              AS anio,
    EXTRACT(MONTH FROM semana)::SMALLINT                              AS mes,
    (EXTRACT(YEAR FROM semana) * 100
     + EXTRACT(MONTH FROM semana))::INTEGER                           AS periodo_aaaamm,
    categoria, fabricante, marca, presentacion, tipo, promocion,
    SUM(valor)                                                        AS valor,
    SUM(unidades)                                                     AS unidades,
    COUNT(DISTINCT item)                                              AS items  -- ⚠ NO aditivo
FROM base
WHERE semana IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11;

CREATE UNIQUE INDEX ux_mv_nielsen_semana
    ON marts.mv_nielsen_semana (market, semana, categoria, fabricante, marca,
                                presentacion, tipo, promocion);

CREATE INDEX ix_mv_nielsen_semana_market  ON marts.mv_nielsen_semana (market, semana);
CREATE INDEX ix_mv_nielsen_semana_marca   ON marts.mv_nielsen_semana (marca);
CREATE INDEX ix_mv_nielsen_semana_periodo ON marts.mv_nielsen_semana (periodo_aaaamm);
CREATE INDEX ix_mv_nielsen_semana_anio    ON marts.mv_nielsen_semana (anio);

COMMENT ON MATERIALIZED VIEW marts.mv_nielsen_semana IS
  'Nielsen agregado al grano market x semana x categoria x fabricante x marca x '
  'presentacion x tipo x promocion. ⚠ Los markets NO se suman entre si (ver '
  'bi_nielsen_market.es_universo_total). `items` NO es aditivo. Sin dist_num: es un '
  'porcentaje por item y no se puede agregar.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_nielsen_item_semana — DETALLE por ítem. Ranking de productos y distribución.
-- Grano: el de arriba + `item` + `upc`.
--
-- Es prácticamente 1:1 con el origen (573.013 filas), así que la ganancia no está en
-- reducir filas sino en **tipar una vez** y **parsear la fecha una vez** en vez de
-- hacerlo en cada consulta sobre 573k VARCHAR.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_nielsen_item_semana CASCADE;

CREATE MATERIALIZED VIEW marts.mv_nielsen_item_semana AS
SELECT
    btrim(n.markets)                                                  AS market,
    to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY')             AS semana,
    EXTRACT(YEAR  FROM to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY'))::SMALLINT AS anio,
    EXTRACT(MONTH FROM to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY'))::SMALLINT AS mes,
    (EXTRACT(YEAR  FROM to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY')) * 100
     + EXTRACT(MONTH FROM to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY')))::INTEGER
                                                                      AS periodo_aaaamm,
    COALESCE(NULLIF(btrim(n.categoria),   ''), '(sin categoria)')      AS categoria,
    COALESCE(NULLIF(btrim(n.fabricantes), ''), '(sin fabricante)')     AS fabricante,
    COALESCE(NULLIF(btrim(n.marcas),      ''), '(sin marca)')          AS marca,
    COALESCE(NULLIF(btrim(n.marca_origen), ''), '(sin marca)')         AS marca_origen,
    COALESCE(NULLIF(btrim(n.item),         ''), '(sin item)')          AS item,
    COALESCE(NULLIF(btrim(n.upc),          ''), '(sin upc)')           AS upc,
    COALESCE(NULLIF(btrim(n.presentacion_unif), ''), '(sin presentacion)') AS presentacion,
    COALESCE(NULLIF(btrim(n.tipo_unif),    ''), '(sin tipo)')          AS tipo,
    COALESCE(NULLIF(btrim(n.promocionno_promocion_unif), ''), '(sin dato)') AS promocion,
    NULLIF(btrim(n.peso_vol_unitario_unif), '')                        AS peso_vol,
    NULLIF(btrim(n.vtas_valor), '')::NUMERIC                           AS valor,
    NULLIF(btrim(n.vtas_unds),  '')::NUMERIC                           AS unidades,
    -- ⚠ PORCENTAJE POR ÍTEM (0,016 a 69,47), no una fracción ni un share. Sumarlo o
    -- promediarlo entre ítems no significa nada: la suma por categoría/semana da
    -- 1.814 %. Se lee por ítem, o se pondera explícitamente por valor.
    NULLIF(btrim(n.dist_num),   '')::NUMERIC                           AS dist_num
FROM marts.bi_nielsen n
WHERE n.periods IS NOT NULL
  AND to_date(split_part(n.periods, 'fin ', 2), 'DD/MM/YY') IS NOT NULL;

-- El origen no tiene clave natural única (un mismo ítem puede venir repetido con y
-- sin UPC), así que el índice único incluye `id` del origen… que no está en la vista.
-- Se usa en su lugar el grano completo + upc, y si hubiera duplicados el CREATE
-- fallaría de forma ruidosa en vez de servir filas dobladas en silencio.
CREATE UNIQUE INDEX ux_mv_nielsen_item_semana
    ON marts.mv_nielsen_item_semana (market, semana, categoria, fabricante, marca,
                                     item, upc, presentacion, tipo, promocion);

CREATE INDEX ix_mv_nielsen_item_market ON marts.mv_nielsen_item_semana (market, semana);
CREATE INDEX ix_mv_nielsen_item_marca  ON marts.mv_nielsen_item_semana (marca);
CREATE INDEX ix_mv_nielsen_item_item   ON marts.mv_nielsen_item_semana (item);

COMMENT ON MATERIALIZED VIEW marts.mv_nielsen_item_semana IS
  'Nielsen al grano de ITEM. Para el ranking de productos y la distribucion. '
  '⚠ dist_num es un PORCENTAJE POR ITEM: no se suma ni se promedia sin ponderar.';


-- ============================================================================
-- SIGUIENTE PASO OBLIGATORIO: re-ejecutar 24_rol_intranet.sql
--   · concede SELECT sobre las 2 MV y las 2 semillas de este archivo,
--   · y recrea v_lk_producto con la linea sacada del arbol de Odoo.
-- ============================================================================
