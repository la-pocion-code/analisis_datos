-- ============================================================================
-- Vistas MATERIALIZADAS para los dashboards de la INTRANET. Idempotente.
-- Archivo: sql/marts/23_mv_dashboards.sql  (ejecutar DESPUÉS de 21 y 22).
--
-- ¿POR QUÉ EXISTEN? La intranet (repo `proyecto pocion/intranet`, app
-- `apps/dashboards`) sustituye los tableros de Power BI por gráficos web. Power
-- BI importa los datos y los agrega en memoria; un dashboard web consulta EN
-- VIVO en cada carga de página. Y `v_ventas_bi` es una vista sobre
-- `v_ventas_explotada` (window functions) sobre `v_ventas_producto` (7 joins):
-- se recalcula entera —910k filas— en CADA consulta.
--
-- Medido en producción (2026-07-28, ~910.423 filas):
--     ventas por mes del año en curso ......... 6.892 ms
--     top 10 clientes del año ................. 8.277 ms
-- Con 5-6 paneles por página eso son ~40 s de CPU de base de datos por usuario
-- que entra al tablero. Materializar elimina la recomputación: la consulta pasa
-- a leer una tabla plana e indexada.
--
-- ⚠ REGLA DEL PROYECTO: todo lo de base de datos vive en ESTE repo. La intranet
-- solo hace SELECT sobre estos objetos (con el rol `intranet_ro`, ver
-- 24_rol_intranet.sql). Contrato de datos en docs/dashboards_intranet.md.
--
-- ── FASE 1: solo VENTAS ──────────────────────────────────────────────────────
-- Nielsen, cuentas clave, cartera y contabilidad se añadirán en archivos/fases
-- siguientes, a medida que se construya cada hoja.
--
-- ── CONVENCIONES ─────────────────────────────────────────────────────────────
-- · `venta`    = SUM(venta_componente)     ⭐ el valor SIEMPRE se suma así.
-- · `unidades` = SUM(cantidad_componente)  (grano componente).
--   ⚠ NUNCA usar `cantidad_neta`: es de nivel KIT y se repite en cada fila de
--   componente (inflaría ~30%: 42.810 M correcto vs 55.753 M inflado).
-- · `facturas` es un COUNT(DISTINCT) al grano EXACTO de cada MV ⇒ **NO es
--   aditivo**: no lo sumes al agrupar más grueso. Para conteos por mes usa
--   `mv_ventas_kpi_mes`, que los calcula a su propio grano.
--
-- · ⚠ **DOS FECHAS, y la que usa el informe es `fecha_factura`.** Cada línea tiene
--   `fecha_venta` (la NC resta en el mes de SU factura original, ver
--   19_nc_factura.sql) y `fecha_factura` (el mes de emisión del documento).
--   `docs/guia_bi_ventas.md` dice que la relación activa con el calendario es
--   `fecha_venta`, pero **en el modelo real de Power BI la activa es
--   `fecha_factura`** (la de `fecha_venta` está marcada inactiva), así que TODO
--   lo que hoy ve el negocio está atribuido por fecha de factura. Verificado el
--   2026-07-28 contra el informe: por `fecha_factura` los meses de 2026 cuadran
--   **al peso** (ene 7.943.994.410, feb 7.939.146.002, mar 7.870.065.104,
--   abr 8.986.842.524, may 7.163.682.804, jun 7.162.634.966); por `fecha_venta`
--   difieren hasta un 7 % en un mes (marzo y abril) aunque el total anual sea
--   casi igual.
--   Por eso el grano de las MV incluye **las dos** y cada una expone su periodo:
--     `fecha` / `periodo_aaaamm` / `anio` / `mes`  → por **fecha_venta**
--     `fecha_factura` / `periodo_factura_aaaamm` / `anio_factura` / `mes_factura`
--                                                 → por **fecha_factura**
--   Cuesta casi nada: solo 952 de 910.802 líneas tienen fechas distintas (0,10 %),
--   así que el grano crece +0,04 %. La intranet usa `fecha_factura` por defecto
--   (para ser fiel al informe) y permite cambiar de base.
-- · Las dimensiones de texto van con COALESCE a un centinela y los ids a -1:
--   el índice ÚNICO que exige `REFRESH ... CONCURRENTLY` no puede depender de
--   NULLs (en un índice único los NULL se consideran distintos entre sí, así que
--   no garantizarían unicidad). Un -1 simplemente no casa en el LEFT JOIN con la
--   dimensión ⇒ la intranet lo pinta como "(sin …)".
--
-- ── IDEMPOTENCIA ─────────────────────────────────────────────────────────────
-- No existe CREATE OR REPLACE MATERIALIZED VIEW ⇒ se hace DROP + CREATE. El
-- script se puede re-ejecutar cuando se quiera, pero **reconstruye** las MV
-- (1-3 min). Para el refresco rutinario NO se usa este archivo: lo hace
-- run_dw.py con REFRESH MATERIALIZED VIEW CONCURRENTLY (no bloquea lecturas).
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- Bitácora de refresco — la intranet la lee para (a) invalidar su caché Redis y
-- (b) mostrar "datos actualizados hace X". Es una TABLA normal, no una MV.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_mv_refresh (
    mv_name      TEXT        PRIMARY KEY,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    filas        BIGINT,
    duracion_ms  INTEGER,
    ok           BOOLEAN     NOT NULL DEFAULT TRUE,
    error        TEXT
);

COMMENT ON TABLE marts.bi_mv_refresh IS
  'Última vez que se refrescó cada vista materializada de dashboards. La '
  'intranet usa MAX(refreshed_at) como versión de caché.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_dia — SERIES TEMPORALES (evolución diaria, MTD, acumulados)
-- Grano: fecha × empresa × tercero × vendedor × categoría × país × equipo
-- Cardinalidad medida: ~176.979 filas (vs 910.423 de origen).
-- Sin `componente_id` a propósito: el producto es lo que multiplica las filas y
-- para la línea de tiempo no se necesita. Para desglosar por producto está
-- mv_ventas_mes.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_dia CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_dia AS
SELECT
    -- ── atribución por fecha de VENTA ──
    v.fecha_venta                                                     AS fecha,
    EXTRACT(YEAR  FROM v.fecha_venta)::SMALLINT                       AS anio,
    EXTRACT(MONTH FROM v.fecha_venta)::SMALLINT                       AS mes,
    (EXTRACT(YEAR FROM v.fecha_venta) * 100
     + EXTRACT(MONTH FROM v.fecha_venta))::INTEGER                    AS periodo_aaaamm,
    -- ── atribución por fecha de FACTURA (la que usa el informe) ──
    COALESCE(v.fecha_factura, v.fecha_venta)                          AS fecha_factura,
    EXTRACT(YEAR  FROM COALESCE(v.fecha_factura, v.fecha_venta))::SMALLINT AS anio_factura,
    EXTRACT(MONTH FROM COALESCE(v.fecha_factura, v.fecha_venta))::SMALLINT AS mes_factura,
    (EXTRACT(YEAR FROM COALESCE(v.fecha_factura, v.fecha_venta)) * 100
     + EXTRACT(MONTH FROM COALESCE(v.fecha_factura, v.fecha_venta)))::INTEGER AS periodo_factura_aaaamm,
    -- ── dimensiones ──
    COALESCE(v.empresa_id,  -1)                                       AS empresa_id,
    COALESCE(v.tercero_id,  -1)                                       AS tercero_id,
    COALESCE(v.vendedor_id, -1)                                       AS vendedor_id,
    COALESCE(NULLIF(btrim(v.categoria), ''), '(sin categoria)')       AS categoria,
    COALESCE(NULLIF(btrim(v.pais),      ''), '(sin pais)')            AS pais,
    COALESCE(NULLIF(btrim(v.equipo),    ''), '(sin equipo)')          AS equipo,
    -- ── medidas ──
    SUM(v.venta_componente)                                           AS venta,
    SUM(v.cantidad_componente)                                        AS unidades,
    COUNT(DISTINCT v.factura_id)                                      AS facturas  -- ⚠ NO aditivo
FROM marts.v_ventas_bi v
WHERE v.fecha_venta IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14;

-- Índice ÚNICO: obligatorio para REFRESH ... CONCURRENTLY. Incluye las DOS
-- fechas porque las dos están en el grano.
CREATE UNIQUE INDEX ux_mv_ventas_dia
    ON marts.mv_ventas_dia (fecha, fecha_factura, empresa_id, tercero_id,
                            vendedor_id, categoria, pais, equipo);

CREATE INDEX ix_mv_ventas_dia_fecha      ON marts.mv_ventas_dia (fecha);
CREATE INDEX ix_mv_ventas_dia_fecha_fact ON marts.mv_ventas_dia (fecha_factura);
CREATE INDEX ix_mv_ventas_dia_periodo    ON marts.mv_ventas_dia (periodo_aaaamm);
CREATE INDEX ix_mv_ventas_dia_per_fact   ON marts.mv_ventas_dia (periodo_factura_aaaamm);
CREATE INDEX ix_mv_ventas_dia_tercero    ON marts.mv_ventas_dia (tercero_id);
CREATE INDEX ix_mv_ventas_dia_vendedor   ON marts.mv_ventas_dia (vendedor_id);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_dia IS
  'Ventas al grano día × empresa × cliente × vendedor × categoría × país × '
  'equipo. Para series temporales de la hoja Ventas de la intranet. '
  'venta=SUM(venta_componente), unidades=SUM(cantidad_componente). '
  'facturas NO es aditivo (ver mv_ventas_kpi_mes).';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_mes — DESGLOSES (por cliente, producto, vendedor, categoría, país)
-- Grano: mes × empresa × tercero × vendedor × componente × categoría × país × equipo
-- Cardinalidad medida: ~851.515 filas. Casi las mismas que el origen (910k)
-- porque `tercero_id` tiene 120.789 valores distintos: agrupar por cliente y
-- producto NO colapsa mucho. La ganancia NO está en reducir filas, está en
-- **dejar de recalcular la cadena de 3 vistas anidadas** en cada consulta: se
-- pasa de reconstruir 910k filas con 7 joins + window functions a leer una
-- tabla plana con índices.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_mes AS
SELECT
    -- ── atribución por fecha de VENTA ──
    date_trunc('month', v.fecha_venta)::DATE                          AS fecha_mes,
    (EXTRACT(YEAR FROM v.fecha_venta) * 100
     + EXTRACT(MONTH FROM v.fecha_venta))::INTEGER                    AS periodo_aaaamm,
    EXTRACT(YEAR  FROM v.fecha_venta)::SMALLINT                       AS anio,
    EXTRACT(MONTH FROM v.fecha_venta)::SMALLINT                       AS mes,
    -- ── atribución por fecha de FACTURA (la que usa el informe) ──
    date_trunc('month', COALESCE(v.fecha_factura, v.fecha_venta))::DATE AS fecha_factura_mes,
    (EXTRACT(YEAR FROM COALESCE(v.fecha_factura, v.fecha_venta)) * 100
     + EXTRACT(MONTH FROM COALESCE(v.fecha_factura, v.fecha_venta)))::INTEGER AS periodo_factura_aaaamm,
    EXTRACT(YEAR  FROM COALESCE(v.fecha_factura, v.fecha_venta))::SMALLINT AS anio_factura,
    EXTRACT(MONTH FROM COALESCE(v.fecha_factura, v.fecha_venta))::SMALLINT AS mes_factura,
    -- ── dimensiones ──
    COALESCE(v.empresa_id,    -1)                                     AS empresa_id,
    COALESCE(v.tercero_id,    -1)                                     AS tercero_id,
    COALESCE(v.vendedor_id,   -1)                                     AS vendedor_id,
    COALESCE(v.componente_id, -1)                                     AS componente_id,
    COALESCE(NULLIF(btrim(v.categoria), ''), '(sin categoria)')       AS categoria,
    COALESCE(NULLIF(btrim(v.pais),      ''), '(sin pais)')            AS pais,
    COALESCE(NULLIF(btrim(v.equipo),    ''), '(sin equipo)')          AS equipo,
    -- ── medidas ──
    SUM(v.venta_componente)                                           AS venta,
    SUM(v.cantidad_componente)                                        AS unidades,
    COUNT(DISTINCT v.factura_id)                                      AS facturas  -- ⚠ NO aditivo
FROM marts.v_ventas_bi v
WHERE v.fecha_venta IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15;

CREATE UNIQUE INDEX ux_mv_ventas_mes
    ON marts.mv_ventas_mes (periodo_aaaamm, periodo_factura_aaaamm, empresa_id,
                            tercero_id, vendedor_id, componente_id,
                            categoria, pais, equipo);

CREATE INDEX ix_mv_ventas_mes_periodo    ON marts.mv_ventas_mes (periodo_aaaamm);
CREATE INDEX ix_mv_ventas_mes_per_fact   ON marts.mv_ventas_mes (periodo_factura_aaaamm);
CREATE INDEX ix_mv_ventas_mes_anio       ON marts.mv_ventas_mes (anio);
CREATE INDEX ix_mv_ventas_mes_anio_fact  ON marts.mv_ventas_mes (anio_factura);
CREATE INDEX ix_mv_ventas_mes_fecha_mes  ON marts.mv_ventas_mes (fecha_mes);
CREATE INDEX ix_mv_ventas_mes_fmes_fact  ON marts.mv_ventas_mes (fecha_factura_mes);
CREATE INDEX ix_mv_ventas_mes_tercero    ON marts.mv_ventas_mes (tercero_id);
CREATE INDEX ix_mv_ventas_mes_componente ON marts.mv_ventas_mes (componente_id);
CREATE INDEX ix_mv_ventas_mes_vendedor   ON marts.mv_ventas_mes (vendedor_id);
CREATE INDEX ix_mv_ventas_mes_categoria  ON marts.mv_ventas_mes (categoria);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_mes IS
  'Ventas al grano mes × empresa × cliente × vendedor × producto(componente) × '
  'categoría × país × equipo. Base de los desgloses y top-N de la hoja Ventas.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_kpi_mes — CONTEOS DISTINTOS (no se pueden derivar de las anteriores)
-- Grano: mes × empresa × categoría. Pequeña. Existe porque COUNT(DISTINCT …) no
-- es aditivo: sumar `facturas` de mv_ventas_mes daría un número inflado.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_kpi_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_kpi_mes AS
SELECT
    (EXTRACT(YEAR FROM v.fecha_venta) * 100
     + EXTRACT(MONTH FROM v.fecha_venta))::INTEGER                    AS periodo_aaaamm,
    EXTRACT(YEAR  FROM v.fecha_venta)::SMALLINT                       AS anio,
    EXTRACT(MONTH FROM v.fecha_venta)::SMALLINT                       AS mes,
    (EXTRACT(YEAR FROM COALESCE(v.fecha_factura, v.fecha_venta)) * 100
     + EXTRACT(MONTH FROM COALESCE(v.fecha_factura, v.fecha_venta)))::INTEGER AS periodo_factura_aaaamm,
    EXTRACT(YEAR  FROM COALESCE(v.fecha_factura, v.fecha_venta))::SMALLINT AS anio_factura,
    EXTRACT(MONTH FROM COALESCE(v.fecha_factura, v.fecha_venta))::SMALLINT AS mes_factura,
    COALESCE(v.empresa_id, -1)                                        AS empresa_id,
    COALESCE(NULLIF(btrim(v.categoria), ''), '(sin categoria)')       AS categoria,
    SUM(v.venta_componente)                                           AS venta,
    SUM(v.cantidad_componente)                                        AS unidades,
    COUNT(DISTINCT v.factura_id)                                      AS facturas,
    COUNT(DISTINCT v.tercero_id)                                      AS clientes,
    -- linea_id se repite al explotar kits ⇒ DISTINCT devuelve las líneas reales
    -- de la factura (para "promedio de ítems por factura").
    COUNT(DISTINCT v.linea_id)                                        AS lineas
FROM marts.v_ventas_bi v
WHERE v.fecha_venta IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8;

CREATE UNIQUE INDEX ux_mv_ventas_kpi_mes
    ON marts.mv_ventas_kpi_mes (periodo_aaaamm, periodo_factura_aaaamm,
                                empresa_id, categoria);

CREATE INDEX ix_mv_ventas_kpi_mes_anio      ON marts.mv_ventas_kpi_mes (anio);
CREATE INDEX ix_mv_ventas_kpi_mes_anio_fact ON marts.mv_ventas_kpi_mes (anio_factura);
CREATE INDEX ix_mv_ventas_kpi_mes_per_fact  ON marts.mv_ventas_kpi_mes (periodo_factura_aaaamm);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_kpi_mes IS
  'KPIs de ventas con conteos DISTINTOS (facturas, clientes, líneas) al grano '
  'mes × empresa × categoría. Úsala para ticket promedio y clientes únicos: '
  'esos conteos NO se pueden sumar desde mv_ventas_mes.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_presupuesto_mes — presupuesto comercial, tipado y normalizado a mes
--
-- `marts.bi_presupuesto` viene de un Excel de Drive y el auto-DDL de DBLoader
-- crea TODAS las columnas como VARCHAR(512) (incluidas fecha e importes). Aquí
-- se tipan una sola vez para que la intranet no tenga que castear en cada
-- consulta. Casts verificados contra los datos reales (2026-07-28): 0 valores
-- que no parseen como numeric y 0 fechas no ISO.
--
-- ⚠ LIMITACIONES DE LOS DATOS DE ORIGEN (a tener en cuenta en la hoja Ventas):
--   · Solo hay presupuesto de **2026** (2026-01-01 … 2026-12-02) ⇒ no se puede
--     comparar presupuesto de años anteriores.
--   · **No hay columna de empresa** ⇒ el presupuesto NO se puede separar por
--     empresa (HFA / PCN Poción). Al comparar contra ventas hay que sumar las
--     dos empresas o documentar el supuesto.
--   · 10 de 348 filas no traen importe y 1 no trae fecha (se excluye).
--   · `unnamed_8` está 100% vacía y `unnamed_9` no se usa (igual que en el
--     modelo de Power BI) ⇒ no se exponen.
--
-- NOTA DE ALCANCE: el cruce con `bi_cliente_credito` (días de CxC →
-- `meses_desplazamiento`, `anticipo`) que hoy hace Power Query NO se porta aquí:
-- alimenta la proyección de flujo de caja, que pertenece a la hoja de
-- Contabilidad. Se hará en la fase de esa hoja, donde se pueda validar contra
-- el informe real.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_presupuesto_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_presupuesto_mes AS
SELECT
    date_trunc('month', p.fecha::TIMESTAMP)::DATE                     AS fecha_mes,
    (EXTRACT(YEAR FROM p.fecha::TIMESTAMP) * 100
     + EXTRACT(MONTH FROM p.fecha::TIMESTAMP))::INTEGER               AS periodo_aaaamm,
    EXTRACT(YEAR  FROM p.fecha::TIMESTAMP)::SMALLINT                  AS anio,
    EXTRACT(MONTH FROM p.fecha::TIMESTAMP)::SMALLINT                  AS mes,
    COALESCE(NULLIF(btrim(p.cliente),           ''), '(sin cliente)')   AS cliente,
    COALESCE(NULLIF(btrim(p.canal),             ''), '(sin canal)')     AS canal,
    -- ⭐ categoria: el canal del Excel NORMALIZADO al mismo vocabulario que `fact.categoria` de Odoo
    -- (marts.map_categoria). Es la columna con la que se une contra mv_ventas_*.categoria y la que
    -- permite filtrar los tableros por categoría. Único desajuste real: INTERNACIONAL → EXPORTACION.
    COALESCE(mc.categoria_bi, NULLIF(btrim(p.canal), ''), '(sin categoria)') AS categoria,
    COALESCE(NULLIF(btrim(p.zona),              ''), '(sin zona)')      AS zona,
    COALESCE(NULLIF(btrim(p.ejecutiva),         ''), '(sin ejecutiva)') AS ejecutiva,
    -- ⚠ NO es una categoría: es el NIVEL del cliente (DIAMOND/SILVER/GOLD) y viene vacío en 302 de
    -- las 347 filas. Se conserva con su nombre para no romper a la intranet. La categoría es `categoria`.
    COALESCE(NULLIF(btrim(p.categoria_cliente), ''), '(sin nivel)')     AS categoria_cliente,
    SUM(COALESCE(NULLIF(btrim(p.presupuesto),         '')::NUMERIC, 0)) AS presupuesto,
    SUM(COALESCE(NULLIF(btrim(p.presupuesto_con_iva), '')::NUMERIC, 0)) AS presupuesto_con_iva
FROM marts.bi_presupuesto p
LEFT JOIN marts.map_categoria mc ON mc.categoria_origen = btrim(p.canal)
WHERE p.fecha IS NOT NULL
  AND btrim(p.fecha) <> ''
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10;

CREATE UNIQUE INDEX ux_mv_presupuesto_mes
    ON marts.mv_presupuesto_mes (periodo_aaaamm, cliente, canal, categoria, zona, ejecutiva, categoria_cliente);

CREATE INDEX ix_mv_presupuesto_mes_anio      ON marts.mv_presupuesto_mes (anio);
CREATE INDEX ix_mv_presupuesto_mes_cliente   ON marts.mv_presupuesto_mes (cliente);
CREATE INDEX ix_mv_presupuesto_mes_categoria ON marts.mv_presupuesto_mes (categoria);

COMMENT ON MATERIALIZED VIEW marts.mv_presupuesto_mes IS
  'Presupuesto comercial tipado (el origen bi_presupuesto es todo VARCHAR) al '
  'grano mes × cliente × canal × zona × ejecutiva × nivel. `categoria` = el canal '
  'normalizado con map_categoria, para unir con mv_ventas_*.categoria. OJO: '
  '`categoria_cliente` es el NIVEL (DIAMOND/SILVER/GOLD), no una categoría. '
  'Solo 2026 y SIN desglose por empresa.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_ventas_presupuesto_mes — VENTAS vs PRESUPUESTO al grano mes × categoría.
--
-- Es el cruce que la intranet hacía a mano. La unión se puede hacer porque
-- `mv_presupuesto_mes.categoria` ya viene normalizada con `map_categoria` al
-- mismo vocabulario que `mv_ventas_mes.categoria` (ver arriba).
--
-- FULL OUTER JOIN a propósito: una categoría con presupuesto y sin ventas (o al
-- revés) TIENE que aparecer — es justo lo que el negocio necesita ver.
--
-- ⚠ TRES ASIMETRÍAS que hay que respetar al leerla:
--   · El presupuesto es solo de **2026** ⇒ en 2024-2025 `presupuesto` es NULL.
--   · El presupuesto **no tiene empresa** ⇒ `venta` suma las DOS empresas
--     (HFA + PCN). No se puede filtrar por empresa en esta MV.
--   · `venta` está atribuida por **fecha_venta** (la NC resta en el mes de su
--     factura). No admite la base `fecha_factura` de los otros tableros.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_ventas_presupuesto_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_ventas_presupuesto_mes AS
WITH v AS (
    SELECT periodo_aaaamm, categoria, SUM(venta) AS venta
    FROM marts.mv_ventas_mes
    GROUP BY 1, 2
),
p AS (
    SELECT periodo_aaaamm, categoria,
           SUM(presupuesto)         AS presupuesto,
           SUM(presupuesto_con_iva) AS presupuesto_con_iva
    FROM marts.mv_presupuesto_mes
    GROUP BY 1, 2
)
SELECT
    COALESCE(v.periodo_aaaamm, p.periodo_aaaamm)              AS periodo_aaaamm,
    (COALESCE(v.periodo_aaaamm, p.periodo_aaaamm) / 100)::SMALLINT AS anio,
    (COALESCE(v.periodo_aaaamm, p.periodo_aaaamm) % 100)::SMALLINT AS mes,
    COALESCE(v.categoria, p.categoria)                        AS categoria,
    COALESCE(v.venta, 0)                                      AS venta,
    p.presupuesto,
    p.presupuesto_con_iva,
    -- cumplimiento y faltante solo tienen sentido si hay presupuesto (>0)
    CASE WHEN COALESCE(p.presupuesto, 0) > 0
         THEN ROUND(COALESCE(v.venta, 0) * 100.0 / p.presupuesto, 1) END AS cumplimiento_pct,
    CASE WHEN COALESCE(p.presupuesto, 0) > 0
         THEN p.presupuesto - COALESCE(v.venta, 0) END                   AS falta
FROM v
FULL OUTER JOIN p ON p.periodo_aaaamm = v.periodo_aaaamm
                 AND p.categoria      = v.categoria;

CREATE UNIQUE INDEX ux_mv_ventas_presupuesto_mes
    ON marts.mv_ventas_presupuesto_mes (periodo_aaaamm, categoria);

CREATE INDEX ix_mv_ventas_presupuesto_mes_anio      ON marts.mv_ventas_presupuesto_mes (anio);
CREATE INDEX ix_mv_ventas_presupuesto_mes_categoria ON marts.mv_ventas_presupuesto_mes (categoria);

COMMENT ON MATERIALIZED VIEW marts.mv_ventas_presupuesto_mes IS
  'Ventas vs presupuesto por mes × categoría (FULL OUTER: aparecen las '
  'categorías que solo tienen uno de los dos lados). `venta` por fecha_venta y '
  'sumando las DOS empresas; el presupuesto solo existe en 2026 y no tiene '
  'empresa. cumplimiento_pct/falta son NULL si no hay presupuesto.';


-- ════════════════════════════════════════════════════════════════════════════
-- Siembra de la bitácora con la creación inicial.
-- ════════════════════════════════════════════════════════════════════════════
INSERT INTO marts.bi_mv_refresh (mv_name, refreshed_at, filas, ok)
SELECT m.mv, now(), NULL, TRUE
FROM (VALUES ('mv_ventas_dia'), ('mv_ventas_mes'),
             ('mv_ventas_kpi_mes'), ('mv_presupuesto_mes'),
             ('mv_ventas_presupuesto_mes')) AS m(mv)
ON CONFLICT (mv_name) DO UPDATE SET refreshed_at = now(), ok = TRUE, error = NULL;
