-- ============================================================================
-- Hoja de MARKETING — meta del mes, ROAS por plataforma y embudo digital
-- Archivo: sql/marts/31_marketing_dashboards.sql
--
-- Alimenta la hoja `/dashboards/marketing` de la intranet: Resumen · Plataformas
-- · Embudo · Diario. Contrato completo en el repo de la intranet,
-- `docs/dashboards/marketing-contrato.md`.
--
-- ⚠ Tras ejecutarlo hay que RE-EJECUTAR 24_rol_intranet.sql (los GRANT se pierden
-- al recrear una MV).
--
-- ── DE DONDE SALE ESTO ───────────────────────────────────────────────────────
--
-- El area de marketing construyo en Cowork un artefacto HTML
-- (`tracker-la-pocion.html`, 1.645 lineas) que seguia el cumplimiento de la meta
-- mensual y el ROAS por pais. Guardaba TODO en el `localStorage` de un navegador
-- y se refrescaba llamando a Supermetrics desde una sesion abierta: cada persona
-- veia sus propios numeros —o ninguno— y las metas las tecleaba quien tuviera el
-- archivo. La hoja de la intranet ya esta construida; esto es la mitad de datos
-- que le faltaba.
--
-- Los identificadores de cuenta de `bi_marketing_cuenta` salen de ese artefacto
-- (lineas 405-438) y producen las cifras que marketing usa hoy, asi que se
-- reutilizan tal cual.
--
-- ── LAS SEIS TRAMPAS DE ESTE DATASET ────────────────────────────────────────
--
-- 1. ⚠⚠ **LA TRM NO SE CONGELA EN LA CARGA.** El artefacto tenia la tasa en una
--    casilla de texto con `4000` por defecto, global y sin fecha. Las cuentas de
--    Meta y Google de ECUADOR facturan en COP mientras Ecuador reporta en USD, o
--    sea que TODO su gasto y su ROAS colgaban de ese numero — y cambiarlo
--    reconvertia el historico entero sin dejar rastro. Aqui el loader guarda
--    SOLO la moneda nativa y la conversion la hace la MV contra `bi_trm_dia`,
--    con la tasa vigente de cada dia. Corregir una TRM re-convierte el historico
--    correctamente en el siguiente refresco, en vez de dejar un numero malo
--    petrificado. La MV publica `trm_usada` para que la cifra sea auditable.
--
-- 2. ⚠⚠ **`NULL` NO ES CERO, y en esta hoja es la diferencia entre «no hubo
--    visitas» y «no tenemos el dato».** `sesiones`, `usuarios`, `impresiones`,
--    `clics` y `posicion_media` son ANULABLES a proposito: GA4 y Search Console
--    solo entregan desde que se les concedio acceso al service account, y no hay
--    forma de reconstruir lo anterior. El artefacto mostraba «0 sesiones sobre
--    una meta de 18.000» —semaforo rojo permanente sobre un dato inexistente—
--    porque Supermetrics no las traia. Si el loader escribe 0 en vez de NULL, la
--    hoja vuelve a mentir. NO poner centinelas en esas cinco columnas.
--
-- 3. ⚠ **QUE PLATAFORMAS TIENE UN PAIS SALE DE `bi_marketing_cuenta`**, no de
--    las filas con gasto. Republica Dominicana no tiene TikTok: en el artefacto
--    eso estaba cableado en TRES sitios distintos del JavaScript. Aqui RD
--    simplemente no tiene fila, y una plataforma que este mes no invirtio sigue
--    existiendo (si desapareciera del panel se leeria como «no la tenemos»).
--
-- 4. ⚠ **SHOPIFY, GA4 Y SEARCH CONSOLE NO SON «PLATAFORMAS».** Sus
--    identificadores viven en columnas de `bi_marketing_pais`, no como filas de
--    `bi_marketing_cuenta`. Si estuvieran ahi, la intranet los pintaria como una
--    cuarta tarjeta de publicidad con ROAS: `plataformas_de()` lee esa tabla tal
--    cual.
--
-- 5. ⚠ **EL DIA EN CURSO NO SE CARGA.** Las cuatro fuentes lo entregan
--    incompleto y un dia a medias hunde el promedio. El artefacto ya lo hacia
--    (`endDate = min(fin_de_mes, ayer)`) y la intranet cuenta con ello: su
--    calculo del ritmo divide entre DIAS CON DATO, no entre dias de calendario.
--
-- 6. ⚠ **LAS COMPRAS AUTO-REPORTADAS SE SOLAPAN Y NO SE SUMAN.** `compras_auto`
--    es lo que cada plataforma se atribuye con su propio modelo: el mismo pedido
--    lo reclaman Meta y Google a la vez, y su suma es normalmente MAYOR que los
--    pedidos reales de Shopify. Se guardan tal cual —la intranet las publica con
--    su aviso— pero no se reconcilian aqui ni se prorratean. El artefacto tenia
--    un «ROAS prorrateado» que repartia el 100 % de la venta entre plataformas
--    usando esas conversiones, e inflaba a las tres a la vez.
--
-- ── ARQUITECTURA: TRES CAPAS ────────────────────────────────────────────────
--
--   bi_marketing_pais / bi_marketing_cuenta   config, se teclea aqui
--   bi_trm_dia                                 la llena el loader (datos.gov.co)
--   bi_marketing_*_dia                         aterrizaje: lo que dijo cada API
--   mv_marketing_*_dia                         lo que lee la intranet
--
-- La separacion aterrizaje/MV no es ceremonia: es lo que permite recargar la TRM
-- y que el gasto convertido se corrija solo (trampa 1), y lo que deja tipar de
-- verdad en vez de heredar el `VARCHAR(512)` que genera `DBLoader.cargar`.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- bi_marketing_pais — CATALOGO de paises del tracker.
--
-- Anadir Mexico o Peru tiene que ser un INSERT, no un despliegue: en el
-- artefacto los tres paises estaban cableados en el JavaScript.
--
-- ⚠ `pais` es el codigo del tracker y NO es necesariamente ISO-3166: la
-- Republica Dominicana es `RD` aqui y `DO` en ISO. Manda este catalogo, porque
-- es la llave con la que cruzan los hechos Y con la que la intranet guarda las
-- metas mensuales (`MarketingMeta.pais`).
--
-- Las tres columnas de identificadores (shopify/ga4/gsc) viven aqui y no en
-- `bi_marketing_cuenta` por la trampa 4.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_marketing_pais (
    pais            TEXT PRIMARY KEY,
    nombre          TEXT    NOT NULL,
    moneda_reporte  TEXT    NOT NULL,
    locale          TEXT,
    timezone        TEXT    NOT NULL,
    orden           SMALLINT NOT NULL DEFAULT 0,
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    -- Identificadores de las fuentes que NO son plataformas de publicidad.
    shopify_shop    TEXT,
    ga4_property_id TEXT,
    gsc_site_url    TEXT,
    nota            TEXT
);

COMMENT ON TABLE marts.bi_marketing_pais IS
  'Paises del tracker de marketing con su moneda de reporte y sus identificadores '
  'de Shopify, GA4 y Search Console. El codigo NO es ISO: RD, no DO. Es la llave '
  'con la que la intranet guarda las metas mensuales.';

INSERT INTO marts.bi_marketing_pais
    (pais, nombre, moneda_reporte, locale, timezone, orden, activo,
     shopify_shop, ga4_property_id, gsc_site_url, nota)
VALUES
    ('CO', 'Colombia',  'COP', 'es-CO', 'America/Bogota',        10, TRUE,
     'gid://shopify/Shop/76335644950', NULL, NULL, NULL),
    ('EC', 'Ecuador',   'USD', 'es-EC', 'America/Guayaquil',     20, TRUE,
     'gid://shopify/Shop/63174410321', NULL, NULL,
     'Sus cuentas de Meta y Google facturan en COP pero el pais reporta en USD: '
     'es el que mas depende de la TRM diaria.'),
    ('RD', 'Rep. Dom.', 'USD', 'es-DO', 'America/Santo_Domingo', 30, TRUE,
     'gid://shopify/Shop/56489836616', NULL, NULL,
     'No tiene TikTok (no hay fila en bi_marketing_cuenta).')
ON CONFLICT (pais) DO UPDATE
    SET nombre         = EXCLUDED.nombre,
        moneda_reporte = EXCLUDED.moneda_reporte,
        locale         = EXCLUDED.locale,
        timezone       = EXCLUDED.timezone,
        orden          = EXCLUDED.orden,
        shopify_shop   = EXCLUDED.shopify_shop,
        nota           = EXCLUDED.nota;
-- ⚠ `activo`, `ga4_property_id` y `gsc_site_url` NO se pisan en el UPDATE: los
-- dos ultimos se rellenan cuando existan las propiedades, y re-ejecutar este
-- fichero no debe borrarlos.


-- ════════════════════════════════════════════════════════════════════════════
-- bi_marketing_cuenta — CONFIGURACION de la ingesta de publicidad.
--
-- Es ademas la respuesta a «que plataformas tiene este pais» (trampa 3): la
-- intranet lee esta tabla, no las filas de gasto.
--
-- ⚠ `filtro` y `ajustes` se pasan TAL CUAL a la consulta de Supermetrics. Sin
-- ellos las cifras NO cuadran con las que marketing usa hoy: Colombia limita
-- Google a Search y Performance Max, y Ecuador y Republica Dominicana limitan
-- Meta a las campanas de conversion.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_marketing_cuenta (
    pais          TEXT    NOT NULL REFERENCES marts.bi_marketing_pais(pais),
    plataforma    TEXT    NOT NULL,
    cuenta_id     TEXT    NOT NULL,
    -- ⚠ La moneda en la que FACTURA la cuenta, que no tiene por que ser la del
    -- pais. Es el nucleo de la trampa 1.
    moneda_nativa TEXT    NOT NULL,
    ds_id         TEXT,          -- id de la fuente en Supermetrics (FA/AW/TIK)
    filtro        TEXT,
    ajustes       JSONB,
    activo        BOOLEAN NOT NULL DEFAULT TRUE,
    nota          TEXT,
    PRIMARY KEY (pais, plataforma)
);

COMMENT ON TABLE marts.bi_marketing_cuenta IS
  'Cuentas de publicidad por pais. Solo PLATAFORMAS DE ANUNCIOS: Shopify, GA4 y '
  'Search Console van en columnas de bi_marketing_pais. La intranet lee esta '
  'tabla para saber que plataformas ofrecer, asi que una fila de mas se pinta '
  'como una tarjeta de ROAS.';

INSERT INTO marts.bi_marketing_cuenta
    (pais, plataforma, cuenta_id, moneda_nativa, ds_id, filtro, ajustes, activo, nota)
VALUES
    -- Colombia
    ('CO', 'Meta',   'act_158846641',       'COP', 'FA',  NULL, NULL, TRUE, NULL),
    ('CO', 'Google', '1675062109',          'COP', 'AW',
     'AdvertisingChannelType =~ ''Search|Performance Max''',
     '{"asset_level": "ASSET_LEVEL_CAMPAIGN"}'::jsonb, TRUE, NULL),
    ('CO', 'TikTok', '7276455376147496961', 'COP', 'TIK', NULL,
     '{"report_type": "4"}'::jsonb, TRUE, NULL),
    -- Ecuador. ⚠ Meta y Google facturan en COP aunque el pais reporte en USD.
    ('EC', 'Meta',   'act_925685692133976', 'COP', 'FA',
     'campaignobjective == ''OUTCOME_SALES''', NULL, TRUE,
     'Factura en COP y el pais reporta en USD: depende de la TRM del dia.'),
    ('EC', 'Google', '2843290494',          'COP', 'AW',  NULL, NULL, TRUE,
     'Factura en COP y el pais reporta en USD.'),
    ('EC', 'TikTok', '7481394959237955601', 'USD', 'TIK', NULL,
     '{"report_type": "4"}'::jsonb, TRUE, NULL),
    -- Republica Dominicana. NO hay fila de TikTok, y esa ausencia ES el dato
    -- (trampa 3): la intranet la lee para no ofrecer una plataforma inexistente.
    ('RD', 'Meta',   'act_501263635883432', 'USD', 'FA',
     'campaignobjective == ''OUTCOME_SALES''', NULL, TRUE, NULL),
    ('RD', 'Google', '1264483933',          'USD', 'AW',  NULL, NULL, TRUE, NULL)
ON CONFLICT (pais, plataforma) DO UPDATE
    SET cuenta_id     = EXCLUDED.cuenta_id,
        moneda_nativa = EXCLUDED.moneda_nativa,
        ds_id         = EXCLUDED.ds_id,
        filtro        = EXCLUDED.filtro,
        ajustes       = EXCLUDED.ajustes,
        nota          = EXCLUDED.nota;
-- `activo` no se pisa: apagar una cuenta es una decision operativa.


-- ════════════════════════════════════════════════════════════════════════════
-- bi_trm_dia — la tasa de cambio, UN REGISTRO POR DIA.
--
-- La llena `cargar_marketing.py` desde la serie oficial de datos.gov.co
-- (`32sa-8pi3`), que publica VIGENCIAS (desde/hasta) y no dias: la TRM no cambia
-- los fines de semana ni los festivos. El loader expande cada vigencia a un
-- registro por dia, que es lo que necesita el join de la MV.
--
-- ⚠ Sentido de `tasa`: cuantas unidades de `moneda_destino` vale UNA de
-- `moneda_origen`. Para la fila USD→COP con tasa 4.000, un dolar son 4.000
-- pesos. La MV divide o multiplica segun el caso; ver `mv_marketing_gasto_dia`.
--
-- No se concede a la intranet: es insumo del calculo, no dato de la hoja.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_trm_dia (
    fecha           DATE    NOT NULL,
    moneda_origen   TEXT    NOT NULL,
    moneda_destino  TEXT    NOT NULL,
    tasa            NUMERIC(18,6) NOT NULL,
    fuente          TEXT,
    PRIMARY KEY (fecha, moneda_origen, moneda_destino)
);

CREATE INDEX IF NOT EXISTS ix_bi_trm_dia_par
    ON marts.bi_trm_dia (moneda_origen, moneda_destino, fecha DESC);

COMMENT ON TABLE marts.bi_trm_dia IS
  'TRM diaria, un registro por dia (las vigencias de datos.gov.co ya vienen '
  'expandidas). tasa = cuantas unidades de moneda_destino vale una de '
  'moneda_origen. Reemplaza la casilla de texto con 4000 del artefacto.';


-- ════════════════════════════════════════════════════════════════════════════
-- TABLAS DE ATERRIZAJE — lo que dijo cada API, sin transformar.
--
-- Se tipan a mano y NO se crean con `DBLoader.cargar`, que genera todo
-- `VARCHAR(512)`: es el problema que las hojas 28 y 29 tuvieron que arreglar a
-- posteriori con casts en la MV.
--
-- El loader escribe aqui con `upsert()` de `etl_dw_marts` (clave compuesta), asi
-- que la ventana movil de reproceso de 7 dias corrige sin duplicar.
-- ════════════════════════════════════════════════════════════════════════════

-- Gasto publicitario. Grano: fecha x pais x plataforma.
CREATE TABLE IF NOT EXISTS marts.bi_marketing_gasto_dia (
    fecha              DATE    NOT NULL,
    pais               TEXT    NOT NULL,
    plataforma         TEXT    NOT NULL,
    -- ⚠ En la moneda de la CUENTA, no la del pais (trampa 1). La conversion la
    -- hace la MV; aqui no se toca.
    gasto_nativo       NUMERIC(18,4),
    moneda_nativa      TEXT    NOT NULL,
    compras_auto       NUMERIC(14,2),
    valor_compras_auto NUMERIC(18,4),
    roas_auto          NUMERIC(12,4),
    cargado_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, pais, plataforma)
);

-- Shopify + GA4 + Search Console. Grano: fecha x pais.
CREATE TABLE IF NOT EXISTS marts.bi_marketing_web_dia (
    fecha          DATE    NOT NULL,
    pais           TEXT    NOT NULL,
    -- Shopify. La venta esta en la moneda de la tienda, que ES la del pais.
    venta_neta     NUMERIC(18,4),
    impuestos      NUMERIC(18,4),
    pedidos        INTEGER,
    -- ⚠ GA4 y Search Console: ANULABLES, y NULL no es cero (trampa 2).
    sesiones       INTEGER,
    usuarios       INTEGER,
    impresiones    BIGINT,
    clics          INTEGER,
    posicion_media NUMERIC(8,3),
    cargado_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, pais)
);

-- Atribucion de la venta por canal. Grano: fecha x pais x canal x fuente.
CREATE TABLE IF NOT EXISTS marts.bi_marketing_atribucion_dia (
    fecha              DATE    NOT NULL,
    pais               TEXT    NOT NULL,
    -- ⚠ Para los canales de pago tiene que coincidir EXACTAMENTE con
    -- `bi_marketing_cuenta.plataforma` (`Meta`, `Google`, `TikTok`): la intranet
    -- cruza por igualdad de cadena, y un `facebook` aqui contra un `Meta` alli
    -- deja el ROAS last-click en null sin que nada lo delate.
    canal              TEXT    NOT NULL,
    fuente             TEXT    NOT NULL,   -- 'ga4' | 'shopify_referrer'
    venta_atribuida    NUMERIC(18,4),
    pedidos_atribuidos INTEGER,
    cargado_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, pais, canal, fuente)
);


-- ════════════════════════════════════════════════════════════════════════════
-- mv_marketing_gasto_dia — el gasto YA CONVERTIDO a la moneda del pais.
--
-- Grano: fecha x pais x plataforma.
--
-- Aqui es donde se resuelve la trampa 1. Se publica `trm_usada` para que la
-- cifra convertida sea auditable: una conversion que no dice con que tasa se
-- hizo no se puede revisar seis meses despues.
--
-- ⚠ La TRM se busca con la VIGENTE MAS RECIENTE hasta esa fecha, no con la del
-- dia exacto. Si un dia faltara en `bi_trm_dia` (un festivo que el loader no
-- expandio), con un join directo el gasto de ese dia saldria NULL y el mes
-- entero cojearia sin motivo visible.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_marketing_gasto_dia CASCADE;

CREATE MATERIALIZED VIEW marts.mv_marketing_gasto_dia AS
SELECT
    g.fecha,
    g.pais,
    g.plataforma,
    g.gasto_nativo,
    g.moneda_nativa,
    -- La tasa que se aplico. NULL cuando no hizo falta convertir.
    CASE WHEN g.moneda_nativa = p.moneda_reporte THEN NULL ELSE t.tasa END
                                                              AS trm_usada,
    CASE
        WHEN g.gasto_nativo IS NULL                    THEN NULL
        WHEN g.moneda_nativa = p.moneda_reporte        THEN g.gasto_nativo
        -- La tasa esta en USD -> COP, asi que el sentido decide la operacion.
        WHEN g.moneda_nativa = 'COP' AND p.moneda_reporte = 'USD'
             THEN CASE WHEN t.tasa > 0 THEN g.gasto_nativo / t.tasa END
        WHEN g.moneda_nativa = 'USD' AND p.moneda_reporte = 'COP'
             THEN g.gasto_nativo * t.tasa
        -- Par de monedas no contemplado: NULL, nunca el importe sin convertir.
        -- Un dolar contado como un peso no se nota y arruina el ROAS.
        ELSE NULL
    END                                                       AS gasto,
    g.compras_auto,
    -- El valor de las compras vive en la misma moneda que el gasto.
    CASE
        WHEN g.valor_compras_auto IS NULL              THEN NULL
        WHEN g.moneda_nativa = p.moneda_reporte        THEN g.valor_compras_auto
        WHEN g.moneda_nativa = 'COP' AND p.moneda_reporte = 'USD'
             THEN CASE WHEN t.tasa > 0 THEN g.valor_compras_auto / t.tasa END
        WHEN g.moneda_nativa = 'USD' AND p.moneda_reporte = 'COP'
             THEN g.valor_compras_auto * t.tasa
        ELSE NULL
    END                                                       AS valor_compras_auto,
    g.roas_auto,
    p.moneda_reporte
FROM marts.bi_marketing_gasto_dia g
JOIN marts.bi_marketing_pais p ON p.pais = g.pais
LEFT JOIN LATERAL (
    SELECT tr.tasa
    FROM marts.bi_trm_dia tr
    WHERE tr.moneda_origen = 'USD' AND tr.moneda_destino = 'COP'
      AND tr.fecha <= g.fecha
    ORDER BY tr.fecha DESC
    LIMIT 1
) t ON TRUE;

CREATE UNIQUE INDEX ux_mv_marketing_gasto_dia
    ON marts.mv_marketing_gasto_dia (fecha, pais, plataforma);
CREATE INDEX ix_mv_marketing_gasto_pais
    ON marts.mv_marketing_gasto_dia (pais, fecha);

COMMENT ON MATERIALIZED VIEW marts.mv_marketing_gasto_dia IS
  'Gasto publicitario diario, convertido a la moneda de reporte del pais con la '
  'TRM VIGENTE de cada dia. Publica trm_usada para que la conversion sea '
  'auditable. Reemplaza la casilla de texto global con 4000 del artefacto.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_marketing_web_dia — Shopify, GA4 y Search Console en un solo grano diario.
--
-- Grano: fecha x pais. La venta ya viene en la moneda del pais (la tienda de
-- Shopify factura en la moneda local), asi que aqui no se convierte nada.
--
-- ⚠ Las cinco columnas de GA4/GSC pasan TAL CUAL, con sus NULL (trampa 2). No
-- hay `COALESCE(...,0)` en esta vista y no debe haberlo nunca.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_marketing_web_dia CASCADE;

CREATE MATERIALIZED VIEW marts.mv_marketing_web_dia AS
SELECT
    w.fecha,
    w.pais,
    w.venta_neta,
    w.impuestos,
    -- La «facturacion real» del artefacto: venta neta + impuestos. Si falta
    -- cualquiera de las dos partes es NULL, no una suma a medias.
    CASE WHEN w.venta_neta IS NULL AND w.impuestos IS NULL THEN NULL
         ELSE COALESCE(w.venta_neta, 0) + COALESCE(w.impuestos, 0) END AS venta,
    w.pedidos,
    w.sesiones,
    w.usuarios,
    w.impresiones,
    w.clics,
    w.posicion_media,
    p.moneda_reporte
FROM marts.bi_marketing_web_dia w
JOIN marts.bi_marketing_pais p ON p.pais = w.pais;

CREATE UNIQUE INDEX ux_mv_marketing_web_dia
    ON marts.mv_marketing_web_dia (fecha, pais);
CREATE INDEX ix_mv_marketing_web_pais
    ON marts.mv_marketing_web_dia (pais, fecha);

COMMENT ON MATERIALIZED VIEW marts.mv_marketing_web_dia IS
  'Venta, pedidos, sesiones y busqueda organica por dia y pais. sesiones, '
  'usuarios, impresiones, clics y posicion_media son ANULABLES: NULL significa '
  'que la fuente no entrega ese dato, no que valga cero.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_marketing_atribucion_dia — que canal se lleva cada peso de venta.
--
-- Grano: fecha x pais x canal x fuente. Pasa casi directo; existe para que la
-- intranet lea siempre `mv_*` y para poder cambiar el aterrizaje sin tocarla.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_marketing_atribucion_dia CASCADE;

CREATE MATERIALIZED VIEW marts.mv_marketing_atribucion_dia AS
SELECT
    a.fecha,
    a.pais,
    COALESCE(NULLIF(btrim(a.canal), ''), '(sin canal)') AS canal,
    a.fuente,
    a.venta_atribuida,
    a.pedidos_atribuidos
FROM marts.bi_marketing_atribucion_dia a;

CREATE UNIQUE INDEX ux_mv_marketing_atribucion_dia
    ON marts.mv_marketing_atribucion_dia (fecha, pais, canal, fuente);
CREATE INDEX ix_mv_marketing_atribucion_pais
    ON marts.mv_marketing_atribucion_dia (pais, canal);

COMMENT ON MATERIALIZED VIEW marts.mv_marketing_atribucion_dia IS
  'Venta atribuida por canal. Para los canales de pago el nombre coincide con '
  'bi_marketing_cuenta.plataforma (Meta, Google, TikTok): la intranet cruza por '
  'igualdad de cadena para calcular el ROAS last-click.';


-- ════════════════════════════════════════════════════════════════════════════
-- Registro en la bitacora de refrescos, para que la intranet pueda invalidar su
-- cache y decir «datos actualizados hace X» desde el primer momento.
-- ════════════════════════════════════════════════════════════════════════════
INSERT INTO marts.bi_mv_refresh (mv_name, refreshed_at, filas, ok)
SELECT m.mv, now(), NULL, TRUE
FROM (VALUES ('mv_marketing_gasto_dia'),
             ('mv_marketing_web_dia'),
             ('mv_marketing_atribucion_dia')) AS m(mv)
ON CONFLICT (mv_name) DO UPDATE
    SET refreshed_at = now(), ok = TRUE, error = NULL;
