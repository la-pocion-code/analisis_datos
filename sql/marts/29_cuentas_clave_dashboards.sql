-- ============================================================================
-- Hoja de CUENTAS CLAVE — sell-in contra sell-out por retailer
-- Archivo: sql/marts/29_cuentas_clave_dashboards.sql
--
-- Tipa y expone los tres volcados de cuentas clave (todo VARCHAR(512)) para la
-- hoja de la intranet: Retailers · Productos · Tiendas e inventario.
--
-- ⚠ Tras ejecutarlo hay que RE-EJECUTAR 24_rol_intranet.sql (los GRANT se pierden
-- al recrear una MV).
--
-- ── LO QUE SE MIDIÓ ANTES DE ESCRIBIR ESTO (2026-07-31, solo lectura) ────────
--
--   bi_cuentas_clave_ventas ..... 60.515 filas · 10 clientes · 35 productos
--                                 2024-12-01 → 2026-06-30
--   bi_inventario_cclave ........ 4.303 filas · 8 clientes · UNA sola foto
--   bi_tiendas_cclave ........... 1.155 tiendas · 11 clientes
--   bi_cuentas_clave (BASE) ..... 57 filas (mapeo de códigos por retailer)
--
--   casts de `unidades` ......... 0 filas mal formadas de 60.515
--   casts de `inventario`/`maximo` 0 mal formadas de 4.303
--   `maximo` > 0 ................ 2.006 de 4.287 — SOLO Farmatodo (ver trampa 7)
--   huecos en fecha/tienda/producto/id_tienda ......... 0
--
--   empalme producto → dim_producto.codigo ........... 35 de 35   (100 %)
--   empalme cliente  → tercero_id (por SEMILLA) ...... 11 de 11
--     ⚠ por NOMBRE daba «10 de 10» y era una trampa: ver bi_cclave_cliente
--   empalme id_tienda ventas → catálogo .............. 1.128 de 1.128 (normalizado)
--   empalme id_tienda inventario → catálogo .......... 315 de 315     (normalizado)
--
-- ── LAS NUEVE TRAMPAS DE ESTE DATASET ───────────────────────────────────────
--
-- 1. ⚠ **EL SELL-OUT NO TRAE PESOS.** La columna `valores` existe y viene VACÍA en
--    los 10 retailers (0 filas con dato de 60.515). Por eso esta MV **no la expone**:
--    una columna de dinero siempre en cero invita a dividir por ella y a compararla
--    con la venta propia. La hoja entera se mide en UNIDADES, y el sell-in también.
--
-- 2. ⚠ **CADA RETAILER CIERRA EN UN MES DISTINTO** — medido: NOVAVENTA 2025-10,
--    PASTEUR/LASKIN/LUCEGO/SURTI 2026-01, FARMATODO/BRECCIA/PROSALON 2026-02,
--    LEOPHARMA/LIFE 2026-06. Cualquier ratio sell-out/sell-in tiene que recortar los
--    DOS lados a la ventana con sell-out **de ese cliente**. Sin recortar, 2026 da
--    un 6 % (sell-in 792.822 uds contra sell-out 47.777) que es falso; recortando,
--    los retailers salen entre el 45 % y el 125 %.
--    Es el bug del `.pbix`, donde `CUENTAS_CLAVE ANEXO[FECHA]` no está relacionada
--    con el calendario: el sell-out ignora el filtro de fechas.
--    **La MV expone `periodo_aaaamm` para que ese recorte sea posible; no lo hace
--    ella, porque depende del periodo que el usuario pida.**
--
-- 2b. ⚠⚠ **UN RATIO > 100 % NO ES NECESARIAMENTE UN ERROR.** Es la corrección de una
--    conclusión que parecía obvia y es falsa. Medido: LUCEGO da **125,3 %** con la
--    ventana perfectamente recortada (202511-202601) porque **compró 33.694 uds entre
--    202505 y 202510**, antes de que su reporte de sell-out existiera: está vendiendo
--    inventario acumulado. Es un dato correcto y además útil — significa desacumulación.
--    Lo que sí hay que tratar aparte es el **sell-in ≤ 0** (LUCEGO 202601 = −175 uds
--    netos por devoluciones), donde el ratio no existe y hay que decirlo.
--    ⚠ Consecuencia de diseño: el ratio se lee como *sell-through de la ventana*, NO
--    como «qué porcentaje de lo que le vendimos ha vendido». Y **el sell-out empieza
--    cuando empieza el REPORTE del retailer, no cuando empieza la relación comercial.**
--
-- 3. ⚠ **`id_tienda` = CLIENTE ‖ NOMBRE_TIENDA**, y el volcado de inventario
--    conserva la caja original («Locatel Calle 100») mientras ventas y el catálogo
--    la traen en mayúsculas. En crudo el inventario solo empalma 155 de 315 (49 %);
--    normalizando a mayúsculas y colapsando espacios, **315 de 315**. Las tres MV
--    normalizan igual — si una sola no lo hiciera, su panel perdería la mitad de las
--    tiendas sin dar ningún error.
--
-- 4. ⚠ **El inventario es UNA FOTO, no una serie.** Un solo `_loaded_at` en las
--    4.303 filas. Se expone como `foto_at` para que la hoja pueda decir de cuándo
--    es: presentarlo junto a una serie mensual sin fecharlo haría creer que el
--    inventario también evoluciona.
--
-- 5. ⚠ **`vendedor` viene vacío al 100 %** (0 de 60.515) y `sucursal` casi
--    (794). No se exponen. `ciudad` (35.669) y `canal_venta` (23.662) sí, pero
--    llegan a medias y la intranet tiene que tratarlos como opcionales.
--
-- 6. **Hay 96 filas con unidades negativas** (−99 uds en total): son devoluciones y
--    se CONSERVAN. Un sell-out negativo es un dato correcto; filtrarlas infla el
--    sell-out del mes en que se devolvió.
--
-- 7. ⚠ **`maximo` (stock máximo del anaquel) SOLO lo entrega FARMATODO.** Medido:
--    2.006 filas de 4.287, todas suyas; los otros 7 retailers traen literalmente el
--    texto `'0'`, que **no es vacío** y por eso una cuenta de «celdas con dato» lo
--    daba por poblado. El `NULLIF(SUM(maximo), 0)` de `llenado_pct` es lo que
--    convierte eso en NULL en vez de en una división por cero o en un 0 % — un 0 %
--    de llenado se leería como «el anaquel está vacío», que es lo contrario de «no
--    sabemos cuánto cabe». Farmatodo sale al 78,2 %.
--
-- 8. **Dos tiendas de LASKIN venden y no están en el catálogo** (`CHIPICHAPE`,
--    `CIUDAD JARDIN`), de 1.128. La cobertura se calcula partiendo del catálogo, así
--    que no puede pasar del 100 % — pero conviene saber que el catálogo no es
--    exhaustivo. LASKIN es además el de peor cobertura: 15 de 27 tiendas.
--
-- 9. ⚠⚠ **EL CLIENTE NO SE EMPALMA POR NOMBRE.** Hay 4 «FARMATODO» en `dim_tercero` y
--    el que casa exacto con el sell-out es un duplicado **sin ventas**. El empalme va
--    por la semilla `bi_cclave_cliente`, y el detalle completo está en su comentario.
--    Es la trampa más cara de las nueve: acierta el string, falla el negocio y no da
--    ningún error.
--
-- ── LO QUE NO ESTÁ Y NO SE INVENTA ──────────────────────────────────────────
--
-- El `.pbix` calcula un «Stock Sugerido» con una tabla `DIAS_INVENTARIO` (ciclo de
-- 8/15/80 días) **cableada con `Table.FromRows` dentro del propio archivo**: no
-- existe en ninguna base. Aquí nace como semilla `bi_cclave_ciclo` **vacía**, y
-- hasta que el negocio la llene la intranet devuelve «no calculable» con la razón.
-- Un ciclo inventado daría un stock sugerido con aspecto de dato.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- bi_cclave_cliente — SEMILLA: el retailer del sell-out → `tercero_id` de Odoo.
--
-- ⚠⚠ **NO se empalma por nombre, y este es el motivo.** `dim_tercero` tiene CUATRO
-- «FARMATODO»: el que factura es «FARMATODO COLOMBIA S.A» (id 268476, 383.515 uds,
-- tipo_cliente FARMACIAS) y el que casa por nombre EXACTO con el sell-out es
-- «FARMATODO COLOMBIA SA» — sin puntos — que es un duplicado **con cero ventas**
-- (id 388191). Un `JOIN ... ON UPPER(nombre) = UPPER(cliente)` acierta el string y
-- falla el negocio: dejaba al mayor retailer del panel (212.665 uds de sell-out) con
-- sell-in 0 y sin ratio, sin que nada lo delatara. Con el tercero correcto el ratio
-- da 78,5 %, en línea con el resto.
--
-- Verificado uno a uno contra `mv_ventas_mes` (2026-07-31): los otros 9 sí coincidían
-- con su nombre exacto, pero eso es suerte, no un contrato. Aquí queda explícito.
--
-- Si un retailer nuevo entra al ETL y no se añade aquí, su `tercero_id` queda NULL y
-- la hoja dice «sin empalme con las ventas propias» en vez de inventar un ratio.
-- `check_marts` lo vigila.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_cclave_cliente (
    cliente    TEXT   PRIMARY KEY,
    tercero_id BIGINT NOT NULL,
    nota       TEXT
);

COMMENT ON TABLE marts.bi_cclave_cliente IS
  'Mapeo retailer del sell-out -> tercero_id de Odoo. ⚠ NO empalmar por nombre: hay 4 '
  '"FARMATODO" en dim_tercero y el que casa exacto con el sell-out es un duplicado sin '
  'ventas. Verificado contra mv_ventas_mes el 2026-07-31.';

INSERT INTO marts.bi_cclave_cliente (cliente, tercero_id, nota) VALUES
    ('FARMATODO COLOMBIA SA',            268476,
     'OJO: el nombre exacto (388191, "SA" sin puntos) es un duplicado SIN ventas. '
     'El que factura es "FARMATODO COLOMBIA S.A".'),
    ('BRECCIA SALUD S.AS.',              289686, 'Locatel.'),
    ('DISTRIBUIDORA PASTEUR S.A',        320770, NULL),
    ('PROSALON DISTRIBUCIONES SAS',      270065, NULL),
    ('NOVAVENTA S.A.S',                  273073, NULL),
    ('SURTICOSMETICOS HF EU',              8874, NULL),
    ('LASKIN S.A',                       326549, NULL),
    ('LUCEGO SAS',                       306473, 'Krika.'),
    ('DISTRIBUIDORA LEOPHARMA S.R.L.',      878, 'Republica Dominicana.'),
    ('DROGUERIA CORPORACION LIFE S.A.C.', 274174, 'Peru.'),
    ('ZAR IMPORT ZARIMPORT S.A.',        270949,
     'Ecuador. Tiene inventario y tiendas pero NINGUN sell-out cargado: el ETL lo deja '
     'fuera esperando un acceso directo roto en Drive.')
ON CONFLICT (cliente) DO UPDATE
    SET tercero_id = EXCLUDED.tercero_id, nota = EXCLUDED.nota;


-- ════════════════════════════════════════════════════════════════════════════
-- bi_cclave_ciclo — SEMILLA: días de reposición por retailer.
--
-- Nace VACÍA a propósito (ver la nota de arriba). Es el equivalente de
-- `bi_producto_lanzamiento`: un catálogo pequeño que el negocio llena y que no
-- tendría sentido cablear en el código de la intranet.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_cclave_ciclo (
    cliente          TEXT PRIMARY KEY,
    dias_reposicion  SMALLINT NOT NULL CHECK (dias_reposicion > 0),
    nota             TEXT
);

COMMENT ON TABLE marts.bi_cclave_ciclo IS
  'Dias de reposicion por retailer, para el stock sugerido. Reemplaza la tabla '
  'DIAS_INVENTARIO que estaba cableada dentro del .pbix (ciclo 8/15/80) y no existia '
  'en ninguna base. Nace VACIA: sin fila, la intranet dice "no calculable" con la '
  'razon en vez de inventar un ciclo.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_cclave_venta_mes — SELL-OUT tipado y agregado.
-- Grano: cliente × mes × producto × tienda.
--
-- Se resuelven aquí, UNA vez, los dos empalmes que la intranet necesitaría repetir
-- en cada panel: `tercero_id` (para cruzar con el sell-in de `mv_ventas_mes`) y
-- `producto_id`/`codigo` (extraído de «[PCN02] SHAMPOO LA POCION»). Hacerlos en la
-- intranet significaría un join por nombre de texto en cada consulta.
--
-- ⚠ Sin `valores`: viene vacío (trampa 1). ⚠ Sin `vendedor`: idem (trampa 5).
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_cclave_venta_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_cclave_venta_mes AS
WITH base AS (
    SELECT
        UPPER(btrim(v.cliente))                                       AS cliente,
        -- La fecha llega como texto ISO con hora; el grano de la hoja es mensual.
        (v.fecha)::DATE                                               AS fecha,
        -- El producto viene como «[COD] NOMBRE». El código propio va entre
        -- corchetes y casa 35/35 contra dim_producto.codigo.
        UPPER(btrim(substring(v.producto FROM '\[([^\]]+)\]')))       AS codigo,
        btrim(v.producto)                                             AS producto_etiqueta,
        -- Normalización de la trampa 3: sin esto el inventario pierde media tienda.
        regexp_replace(UPPER(btrim(v.id_tienda)), '\s+', ' ', 'g')    AS id_tienda,
        regexp_replace(UPPER(btrim(v.nombre_tienda)), '\s+', ' ', 'g') AS nombre_tienda,
        NULLIF(btrim(COALESCE(v.ciudad, '')), '')                     AS ciudad,
        NULLIF(btrim(COALESCE(v.canal_venta, '')), '')                AS canal_venta,
        -- Cast verificado: 0 filas mal formadas de 60.515. Las negativas se
        -- conservan (trampa 6).
        (btrim(v.unidades))::NUMERIC                                  AS unidades
    FROM marts.bi_cuentas_clave_ventas v
    WHERE COALESCE(btrim(v.fecha), '') <> ''
      AND COALESCE(btrim(v.unidades), '') <> ''
)
SELECT
    b.cliente,
    t.tercero_id,
    t.pais,
    date_trunc('month', b.fecha)::DATE                                AS fecha_mes,
    (EXTRACT(YEAR FROM b.fecha) * 100
     + EXTRACT(MONTH FROM b.fecha))::INTEGER                          AS periodo_aaaamm,
    EXTRACT(YEAR  FROM b.fecha)::SMALLINT                             AS anio,
    EXTRACT(MONTH FROM b.fecha)::SMALLINT                             AS mes,
    b.codigo,
    d.producto_id,
    COALESCE(d.nombre_comercial, d.nombre, b.producto_etiqueta)       AS producto,
    d.categoria,
    b.id_tienda,
    b.nombre_tienda,
    -- Una tienda puede traer varias ciudades/canales entre filas; se toma el más
    -- frecuente por MIN estable en vez de dejar el grano suelto.
    MIN(b.ciudad)                                                     AS ciudad,
    MIN(b.canal_venta)                                                AS canal_venta,
    SUM(b.unidades)                                                   AS unidades,
    COUNT(*)                                                          AS filas
FROM base b
-- ⚠ El cliente se empalma por la SEMILLA, no por el nombre: hay 4 «FARMATODO» y el
-- que casa exacto es un duplicado sin ventas (ver bi_cclave_cliente).
LEFT JOIN marts.bi_cclave_cliente c ON c.cliente = b.cliente
LEFT JOIN marts.dim_tercero  t ON t.tercero_id = c.tercero_id
LEFT JOIN marts.dim_producto d ON UPPER(btrim(d.codigo)) = b.codigo
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13;

-- El índice único es lo que permite refrescar CONCURRENTLY. Las cuatro columnas
-- están medidas sin nulos ni vacíos en el origen.
CREATE UNIQUE INDEX ux_mv_cclave_venta_mes
    ON marts.mv_cclave_venta_mes (cliente, periodo_aaaamm, codigo, id_tienda);

CREATE INDEX ix_mv_cclave_venta_periodo ON marts.mv_cclave_venta_mes (periodo_aaaamm);
CREATE INDEX ix_mv_cclave_venta_tercero ON marts.mv_cclave_venta_mes (tercero_id, periodo_aaaamm);
CREATE INDEX ix_mv_cclave_venta_prod    ON marts.mv_cclave_venta_mes (producto_id);
CREATE INDEX ix_mv_cclave_venta_tienda  ON marts.mv_cclave_venta_mes (id_tienda);

COMMENT ON MATERIALIZED VIEW marts.mv_cclave_venta_mes IS
  'Sell-out de cuentas clave al grano cliente x mes x producto x tienda, en '
  'UNIDADES. ⚠ NO trae pesos: la columna `valores` del origen viene vacia en los 10 '
  'retailers. ⚠ Cada retailer cierra en un mes distinto, asi que cualquier ratio '
  'contra el sell-in (mv_ventas_mes) tiene que recortar los dos lados al ultimo mes '
  'con sell-out DE ESE CLIENTE. Las unidades negativas son devoluciones y se conservan.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_cclave_inventario — INVENTARIO en tienda. Es UNA FOTO (trampa 4).
-- Grano: cliente × producto × tienda.
--
-- ⚠ `maximo` **solo lo entrega FARMATODO** (trampa 7): los otros 7 retailers mandan
-- `'0'`. `llenado_pct` se calcula aquí, una vez, para que ningún panel se olvide del
-- `NULLIF(..., 0)` — sin él saldría un 0 % de llenado, que se lee como «el anaquel
-- está vacío» y significa lo contrario: que no sabemos cuánto cabe.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_cclave_inventario CASCADE;

CREATE MATERIALIZED VIEW marts.mv_cclave_inventario AS
WITH base AS (
    SELECT
        UPPER(btrim(i.cliente))                                       AS cliente,
        UPPER(btrim(substring(i.producto FROM '\[([^\]]+)\]')))       AS codigo,
        btrim(i.producto)                                             AS producto_etiqueta,
        -- Misma normalización que en ventas (trampa 3). Las 32 filas sin tienda son
        -- inventario AGREGADO (LEOPHARMA y LIFE lo entregan así): se marcan en vez
        -- de descartarse, y la intranet dice que ahí no hay detalle por tienda.
        COALESCE(NULLIF(regexp_replace(UPPER(btrim(COALESCE(i.id_tienda, ''))),
                                       '\s+', ' ', 'g'), ''), '(sin tienda)') AS id_tienda,
        COALESCE(NULLIF(regexp_replace(UPPER(btrim(COALESCE(i.nombre_tienda, ''))),
                                       '\s+', ' ', 'g'), ''), '(sin tienda)') AS nombre_tienda,
        NULLIF(btrim(COALESCE(i.cod_cliente, '')), '')                AS cod_cliente,
        (btrim(i.inventario))::NUMERIC                                AS inventario,
        NULLIF(btrim(COALESCE(i.maximo, '')), '')::NUMERIC            AS maximo,
        (i._loaded_at)::TIMESTAMP                                     AS foto_at
    FROM marts.bi_inventario_cclave i
    WHERE COALESCE(btrim(i.inventario), '') <> ''
)
SELECT
    b.cliente,
    t.tercero_id,
    t.pais,
    b.codigo,
    d.producto_id,
    COALESCE(d.nombre_comercial, d.nombre, b.producto_etiqueta)       AS producto,
    d.categoria,
    b.id_tienda,
    b.nombre_tienda,
    -- ⚠ Marca de si el inventario tiene detalle por tienda. LEOPHARMA y LIFE lo
    -- entregan agregado, y un panel por tienda tiene que decirlo, no promediarlo.
    (b.id_tienda <> '(sin tienda)')                                   AS por_tienda,
    MIN(b.cod_cliente)                                                AS cod_cliente,
    SUM(b.inventario)                                                 AS inventario,
    SUM(b.maximo)                                                     AS maximo,
    -- % de llenado del anaquel. NULLIF por si algún día llega un máximo en cero:
    -- ahí la respuesta es «no se sabe», no un 0 % ni una división por cero.
    ROUND(SUM(b.inventario) * 100.0 / NULLIF(SUM(b.maximo), 0), 2)    AS llenado_pct,
    MAX(b.foto_at)                                                    AS foto_at
FROM base b
LEFT JOIN marts.bi_cclave_cliente c ON c.cliente = b.cliente
LEFT JOIN marts.dim_tercero  t ON t.tercero_id = c.tercero_id
LEFT JOIN marts.dim_producto d ON UPPER(btrim(d.codigo)) = b.codigo
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10;

CREATE UNIQUE INDEX ux_mv_cclave_inventario
    ON marts.mv_cclave_inventario (cliente, codigo, id_tienda);

CREATE INDEX ix_mv_cclave_inv_tercero ON marts.mv_cclave_inventario (tercero_id);
CREATE INDEX ix_mv_cclave_inv_tienda  ON marts.mv_cclave_inventario (id_tienda);

COMMENT ON MATERIALIZED VIEW marts.mv_cclave_inventario IS
  'Inventario en tienda de cuentas clave. ⚠ Es UNA FOTO (un solo _loaded_at), no una '
  'serie: `foto_at` dice de cuando. ⚠ `por_tienda` = false cuando el retailer entrega '
  'el inventario agregado (LEOPHARMA, LIFE): ahi no hay detalle por tienda y un panel '
  'por tienda tiene que decirlo. ⚠ llenado_pct = inventario / maximo, y `maximo` SOLO '
  'lo entrega FARMATODO: para los otros 7 retailers es NULL, que significa "no sabemos '
  'cuanto cabe" y no "el anaquel esta vacio".';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_cclave_tienda — CATÁLOGO de tiendas por retailer.
--
-- Es el DENOMINADOR de la cobertura, y por eso es una MV propia y no un
-- `SELECT DISTINCT` sobre las ventas: si el denominador fueran las tiendas que
-- vendieron, la cobertura sería siempre 100 % y el panel no diría nada.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_cclave_tienda CASCADE;

CREATE MATERIALIZED VIEW marts.mv_cclave_tienda AS
SELECT
    UPPER(btrim(c.cliente))                                           AS cliente,
    t.tercero_id,
    t.pais,
    regexp_replace(UPPER(btrim(c.id_tienda)), '\s+', ' ', 'g')        AS id_tienda,
    MIN(regexp_replace(UPPER(btrim(c.nombre_tienda)), '\s+', ' ', 'g')) AS nombre_tienda
FROM marts.bi_tiendas_cclave c
LEFT JOIN marts.bi_cclave_cliente m ON m.cliente = UPPER(btrim(c.cliente))
LEFT JOIN marts.dim_tercero t ON t.tercero_id = m.tercero_id
WHERE COALESCE(btrim(c.id_tienda), '') <> ''
GROUP BY 1, 2, 3, 4;

CREATE UNIQUE INDEX ux_mv_cclave_tienda ON marts.mv_cclave_tienda (cliente, id_tienda);
CREATE INDEX ix_mv_cclave_tienda_tercero ON marts.mv_cclave_tienda (tercero_id);

COMMENT ON MATERIALIZED VIEW marts.mv_cclave_tienda IS
  'Catalogo de tiendas por retailer: el DENOMINADOR de la cobertura. No se sustituye '
  'por un DISTINCT sobre las ventas — con las tiendas que vendieron como denominador, '
  'la cobertura seria siempre 100 %. ⚠ Incluye retailers sin sell-out cargado '
  '(ZAR IMPORT/Ecuador), a proposito: su cobertura es 0 y eso es un dato.';
