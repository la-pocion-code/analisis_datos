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
  'los demas: los markets NO se suman entre si. ⚠ El NOMBRE que da Nielsen no es fiable '
  '(NEW TOTAL COLOMBIA no es el total del pais, son supermercados): la jerarquia de esta '
  'tabla se MIDIO, no se dedujo del nombre. Ver el comentario de arriba.';

-- ⚠⚠ JERARQUÍA MEDIDA (2026-08-06), NO deducida de los nombres ⚠⚠
-- El export nuevo trae `NEW TOTAL SUPERMERCADOS + FARMACIAS COLOMBIA` y retira
-- `Total Colombia Supermercados`. Al medirlo salió que los nombres ENGAÑAN:
--
--   NEW TOTAL SUPERMERCADOS + FARMACIAS  2.501.713.761.171   ← el universo MAYOR
--     ├── NEW TOTAL COLOMBIA             2.017.542.626.003   ← ¡son SUPERMERCADOS!
--     └── TOTAL COLOMBIA FARMACIAS         484.179.801.326
--   TOTAL CO ECOMMERCE                     131.244.863.164   ← FUERA del combinado
--
-- Las dos pruebas que lo demuestran:
--   1. Aritmética: 2.017.542.626.003 + 484.179.801.326 = 2.501.722.427.329, contra el
--      combinado de 2.501.713.761.171 ⇒ diferencia de 8,7 M sobre 2,5 BILLONES (0,00035 %).
--      Y el ecommerce (131.245 M) NO cabe en esa cuenta ⇒ está fuera.
--   2. Celda a celda al grano de mv_nielsen_semana: `(combinado − farmacias)` coincide con
--      `NEW TOTAL COLOMBIA` **al peso en 74.804 de 75.381 celdas (99,23 %)**, con 0,00043 %
--      de diferencia agregada. Y la contención es limpia: de las 56.815 celdas de farmacias,
--      las 56.815 tienen par en el combinado y hay **0 valores negativos**.
--
-- ⚠⚠ ACTUALIZADO 2026-09-07 — ESTO SE INVIRTIO, Y CONVIENE SABER POR QUE ⚠⚠
-- Con el export del 2026-08-06 la conclusion fue «NO hace falta un market derivado: supermercados
-- YA existe, es NEW TOTAL COLOMBIA». Era correcto ENTONCES.
-- El export del **2026-09-07 dejo de traer `NEW TOTAL COLOMBIA`**: la matriz paso de 3 categorias x
-- 4 markets (12 archivos) a 3 x 3 (9 archivos), quedando el combinado, farmacias y ecommerce.
-- ⇒ Sin ese market, la resta pasa a ser **la UNICA forma de tener supermercados**, asi que ahora SI
--   se materializa: `SUPERMERCADOS (derivado)` en mv_nielsen_semana (ver su comentario).
-- ⚠ Leccion del origen: **el conjunto de markets NO es estable** — cambio dos veces en un mes
--   (primero se retiro `Total Colombia Supermercados`, ahora `NEW TOTAL COLOMBIA`). Por eso esta
--   semilla conserva las filas de los markets retirados con su motivo, en vez de borrarlas, y por
--   eso NO se debe fijar «4 markets» como invariante en ningun test.
INSERT INTO marts.bi_nielsen_market
    (market, etiqueta, es_universo_total, tiene_valor, orden, nota) VALUES
    ('NEW TOTAL SUPERMERCADOS + FARMACIAS COLOMBIA', 'Supermercados + Farmacias', TRUE, TRUE, 1,
     'Universo MAYOR de los cuatro (2.501.713.761.171). CONTIENE a Supermercados y a '
     'Farmacias: no sumarlo con ninguno de los dos. NO incluye e-commerce.'),
    ('SUPERMERCADOS (derivado)', 'Supermercados', FALSE, TRUE, 2,
     '⚠ CALCULADO AQUI, NO MEDIDO POR NIELSEN: combinado - farmacias, al grano de la MV. Existe '
     'porque el export del 2026-09-07 dejo de traer NEW TOTAL COLOMBIA, que era este canal. La '
     'resta se validO contra el market real mientras los dos coexistian: coincidia al peso en '
     '99,23 % de las celdas, con 0,00043 % de diferencia agregada y 0 negativos. ⚠ `items` va NULL: '
     'un COUNT(DISTINCT) no se puede restar.'),
    ('NEW TOTAL COLOMBIA', 'Supermercados (retirado)', FALSE, FALSE, 8,
     '⚠ EL NOMBRE ENGANABA: no era el total del pais, era el canal de SUPERMERCADOS. **Ya no viene '
     'en el export desde el 2026-09-07**; lo reemplaza SUPERMERCADOS (derivado). Se conserva la '
     'fila como historia y para que nadie lo vuelva a leer como «total nacional».'),
    ('TOTAL COLOMBIA FARMACIAS', 'Farmacias', FALSE, TRUE, 3,
     'Subconjunto de Supermercados + Farmacias. Su valor no cambio con el export nuevo '
     '(484.179.801.326 antes y despues), asi que el share propio de farmacias es comparable.'),
    ('TOTAL CO ECOMMERCE', 'E-commerce', FALSE, TRUE, 4,
     'FUERA del combinado (no cuadra en su aritmetica). ⚠ El export del 2026-08-06 RE-MIDIO '
     'este universo: paso de 20.355 a 48.733 filas y de 77.582 M a 131.245 M de valor, asi '
     'que su share NO es comparable con medidas anteriores a esa fecha.'),
    ('Total Colombia Supermercados', 'Supermercados (retirado)', FALSE, FALSE, 9,
     'YA NO VIENE en el export (lo reemplazo NEW TOTAL SUPERMERCADOS + FARMACIAS el '
     '2026-08-06). Se conserva la fila como historia: solo traia distribucion, sin valor.')
-- ⚠ DO UPDATE, no DO NOTHING (y es deliberado, al contrario que en bi_nielsen_marca_propia).
-- Estos metadatos NO son una preferencia editable: son la JERARQUÍA MEDIDA de los universos, y
-- si la base se queda con la version vieja el tablero marca como «universo total» uno que no lo
-- es y etiqueta supermercados como «Total Colombia». Con DO NOTHING hubo que sincronizarlo a
-- mano, que es como se llega a que el archivo y la base digan cosas distintas. Re-ejecutar este
-- DDL deja la metadata correcta por si sola.
ON CONFLICT (market) DO UPDATE SET
    etiqueta          = EXCLUDED.etiqueta,
    es_universo_total = EXCLUDED.es_universo_total,
    tiene_valor       = EXCLUDED.tiene_valor,
    orden             = EXCLUDED.orden,
    nota              = EXCLUDED.nota;


-- ════════════════════════════════════════════════════════════════════════════
-- MARCAS PROPIAS — para no cablear el literal 'POCION' en el código de la intranet.
-- Si mañana entra otra marca de la casa al panel, es una fila.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_nielsen_marca_propia (
    marca TEXT PRIMARY KEY,
    nota  TEXT
);

COMMENT ON TABLE marts.bi_nielsen_marca_propia IS
  'Marcas de la casa dentro del panel Nielsen. Aqui va UNA fila por marca; las '
  'VARIANTES de nombre de una misma marca no van aqui, se unifican en el loader '
  '(cargar_bi_datasets.ALIAS_MARCA) y marca_origen guarda la original.';

-- ⚠ Nielsen no usa un nombre estable para nuestra marca: ademas de TONGOLE aparecio
-- 'PCN POCION' (un producto, el anticaspa, desde la semana que cierra el 08/03/26) con ese
-- nombre en marca Y fabricante. Cada variante que no se unifique se cae de este listado y
-- deja de contar como marca propia, subestimando el share. Las variantes se resuelven en
-- ALIAS_MARCA del loader, no con una fila aqui: una fila mas aqui haria que la variante
-- contara como propia, pero seguiria apareciendo como una MARCA APARTE en los rankings.
INSERT INTO marts.bi_nielsen_marca_propia (marca, nota) VALUES
    ('POCION', 'El loader unifica TONGOLE y PCN POCION dentro de POCION (ALIAS_MARCA), en marca y fabricante; marca_origen guarda el original.')
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
),
agg AS (
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
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
),
-- ════════════════════════════════════════════════════════════════════════════
-- MARKET DERIVADO: `SUPERMERCADOS (derivado)` = combinado − farmacias.
--
-- ⚠⚠ ES UN DATO CALCULADO AQUI, NO MEDIDO POR NIELSEN. Existe porque el export del 2026-09-07
-- **dejo de traer** `NEW TOTAL COLOMBIA` (que era el canal de supermercados): la matriz paso de
-- 3 categorias x 4 markets a 3 x 3, y sin esta resta el tablero se queda sin la vista de
-- supermercados solo.
--
-- Validado antes de publicarlo, sobre el export del 2026-08-06 que SI traia los dos: la resta daba
-- `NEW TOTAL COLOMBIA` **al peso en 74.804 de 75.381 celdas (99,23 %)**, con 0,00043 % de diferencia
-- agregada, y de las 56.815 celdas de farmacias las 56.815 tenian par en el combinado con **0
-- negativos**.
--
-- ⚠ `items` va en NULL: es un COUNT(DISTINCT item) y **no se puede restar** (no se sabe que items
-- del combinado estan en supermercados sin bajar al grano de item). Poner el del combinado seria
-- mentir. La columna ya esta documentada como NO aditiva.
-- ⚠ `dist_num` no aparece aqui: es un porcentaje por item y vive solo en mv_nielsen_item_semana.
-- ⚠ Si un export futuro rompe la contencion, la resta daria NEGATIVOS. No se tapan con GREATEST:
-- se publican y se aislan en `v_nielsen_derivado_negativo`, que hay que revisar tras cada carga.
-- ════════════════════════════════════════════════════════════════════════════
derivado AS (
    SELECT
        'SUPERMERCADOS (derivado)'::text AS market,
        semana, anio, mes, periodo_aaaamm,
        categoria, fabricante, marca, presentacion, tipo, promocion,
        SUM(CASE WHEN market = 'NEW TOTAL SUPERMERCADOS + FARMACIAS COLOMBIA'
                 THEN valor ELSE -valor END)                          AS valor,
        SUM(CASE WHEN market = 'NEW TOTAL SUPERMERCADOS + FARMACIAS COLOMBIA'
                 THEN unidades ELSE -unidades END)                    AS unidades,
        NULL::bigint                                                  AS items
    FROM agg
    WHERE market IN ('NEW TOTAL SUPERMERCADOS + FARMACIAS COLOMBIA', 'TOTAL COLOMBIA FARMACIAS')
    GROUP BY 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
)
SELECT * FROM agg
UNION ALL
SELECT * FROM derivado;

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


-- ════════════════════════════════════════════════════════════════════════════
-- v_nielsen_derivado_negativo — la red de seguridad del market derivado.
--
-- `SUPERMERCADOS (derivado)` = combinado − farmacias solo es correcto si el combinado CONTIENE a
-- farmacias. Se validó al construirlo (0 negativos en las 56.815 celdas de farmacias), pero **el
-- export de Nielsen cambia cada mes y ya cambió de forma dos veces**: si en una carga futura la
-- contención se rompe, la resta publicaría supermercados con ventas NEGATIVAS.
--
-- No se tapan con GREATEST(x,0): eso convertiría un problema del origen en un dato plausible y
-- falso. Se publican y se aíslan aquí. **Revisar esta vista tras cada carga de Nielsen**: si
-- devuelve filas, el derivado no es de fiar para esas celdas y hay que hablar con Nielsen.
-- Mismo patrón que `v_nc_sin_asignar` (ventas) y `v_compras_descuadre` (compras).
-- ════════════════════════════════════════════════════════════════════════════
DROP VIEW IF EXISTS marts.v_nielsen_derivado_negativo;

CREATE VIEW marts.v_nielsen_derivado_negativo AS
SELECT semana, categoria, fabricante, marca, presentacion, tipo, promocion, valor, unidades
FROM marts.mv_nielsen_semana
WHERE market = 'SUPERMERCADOS (derivado)'
  AND (valor < 0 OR unidades < 0);

COMMENT ON VIEW marts.v_nielsen_derivado_negativo IS
  'Celdas donde (combinado - farmacias) sale NEGATIVO, o sea donde el combinado no contiene a '
  'farmacias. Debe estar VACIA: si trae filas, el market SUPERMERCADOS (derivado) no es de fiar '
  'ahi. Revisar tras cada carga de Nielsen.';
