-- ============================================================================
-- 36_inventario.sql — el INVENTARIO: dimensión de almacén, la foto de existencias y
-- el hecho de snapshot diario que empieza a acumular historia.
-- Archivo: sql/marts/36_inventario.sql. Idempotente (CREATE IF NOT EXISTS / DROP+CREATE de vistas).
--
-- ⚠ Re-ejecutar `24_rol_intranet.sql` DESPUÉS: los GRANT a intranet_ro se pierden al recrear.
--
-- TODO lo de estos comentarios está MEDIDO contra Odoo el 2026-08-13 con
-- `intranet: manage.py medir_inventario_odoo`, no supuesto. El contrato completo, en
-- `proyecto pocion/intranet/docs/dashboards/inventario-contrato.md`.
--
-- ── POR QUÉ ESTO NO SALE DEL HECHO CONTABLE ──────────────────────────────────────────
-- El resto del DW cuelga de `fact_movimiento_contable`, que es un FLUJO por periodo. Una
-- existencia es un SALDO PUNTUAL: no se puede derivar de asientos, y por eso el propio
-- repo tenía escrito que «el desperdicio no es derivable: el DW no extrae movimientos de
-- inventario ni de manufactura». Esto es la Fase 5 de docs/ARQUITECTURA_DW.md, y había un
-- stub comentado con los campos correctos en archivado/etl_odoo_incremental.py:132-147.
--
-- ── LO MEDIDO (2026-08-13) ───────────────────────────────────────────────────────────
--   stock.quant ............ 4.113 filas, se descargan en 0,9 s (~4.400 filas/s)
--   stock.move.line ........ 1.047.415  ⚠ NUNCA se lee entero: se acota por fecha
--   existencia propia ...... 10.687.559 uds en 1.366 filas, 327 productos, 19 almacenes
--   quants con lote ........ 716 de 1.366 (52,4 %)
--
-- ⚠ Las dimensiones tienen DOS conteos y hay que no confundirlos, porque el ETL carga con
-- `CTX_ALL` (`active_test: False`), o sea **incluyendo archivados**:
--   stock.warehouse ........  26 activos ·  28 cargados
--   stock.location ......... 119 activas · 293 cargadas   (63 internas activas · 233 cargadas)
-- Cargar los archivados es deliberado y necesario: un quant puede seguir en una ubicación
-- archivada, y si la dimensión no la trae el join no resuelve y la fila sale «(sin almacén)»
-- sin serlo. El tablero solo muestra lo que tiene existencia — medido, **35 ubicaciones**.
--
-- Reparto medido de la primera foto (por qué importa: no es una bodega, son 19):
--   OFC   Oficina .............. 7.971.795 uds (75 %)  ← la bodega de verdad
--   GIO   MAQ Giorgio ...........  753.731
--   EURO  MAQ Eurobelleza .......  702.653
--   BIO   MAQ Biologic ..........  629.923
--   NAPRO MAQ Naprolab ..........  285.150
--   PUM   PROV Uribemold ........  216.481
--   …y el resto entre maquiladores, proveedores y operadores logísticos.
--
-- ── ⚠⚠ SOLO `usage = 'internal'` ES EXISTENCIA, Y SUMAR TODO NO INFLA: VACÍA ─────────
-- Las ubicaciones de Odoo son de DOBLE PARTIDA. Medido:
--
--   production ....  477 filas    23.798.116 uds
--   internal ...... 1.366 filas   10.687.568 uds   ← la existencia
--   customer ......  289 filas     8.037.923 uds
--   transit .......    5 filas      -214.891 uds
--   inventory ..... 1.703 filas      -812.104 uds
--   supplier ......  273 filas   -41.496.656 uds
--   ─────────────────────────────────────────────
--   SUMA DE TODAS                        -44 uds   ← no es inventario, es ruido de la partida doble
--
-- O sea que contar todos los usages no da un inventario inflado: da CASI CERO. El filtro
-- de `usage` no es una optimización, es la definición.
--
-- ── ⚠⚠ NADA DE CAMPOS COMPUTADOS ────────────────────────────────────────────────────
-- `qty_available`, `virtual_available`, `free_qty` y `available_quantity` son computados
-- `store=False`. Este repo YA se quemó con `valid_ean` (2026-08-06), que devolvía True en
-- 47 de 330 productos leyendo 1.102 de golpe y en 10 de 10 leyendo los mismos diez en un
-- lote pequeño, SIN dar ningún error. Aquí se usan solo campos ALMACENADOS:
--   quantity           store=True   ← la existencia
--   reserved_quantity  store=True   ← lo comprometido
--   lot_id             store=True
-- y «libre» se calcula como `quantity - reserved_quantity`, NO con `available_quantity`,
-- que es la versión computada de esa misma resta.
-- Medido: `qty_available` se aparta de la suma de quants internos en 19 de 327 productos
-- (5,8 %). Es diferencia de DEFINICIÓN (cuentan otro conjunto de ubicaciones), no la
-- mentira del lote — que aquí no se reprodujo. La decisión no cambia.
--
-- ── ⚠⚠ TODO EN UNIDADES, NUNCA EN PESOS ─────────────────────────────────────────────
-- Decisión de William (2026-08-13): el personal de bodega no tiene por qué ver costes. NO
-- hay columna de valor en ninguna de estas tablas, y NO se extrae `standard_price`. No es
-- que se oculte en la UI: el dato no entra. De paso desaparece un riesgo de permisos.
-- ============================================================================

-- ── dim_almacen ──────────────────────────────────────────────────────────────
-- ⚠ Los 26 almacenes NO son todos nuestros, y la distinción es de negocio:
--   propios ............. EVE (Eventos) y las existencias de cada empresa
--   maquiladores ........ MAQ Biologic, MAQ Eurobelleza, MAQ Giorgio, MAQ La Tour
--   proveedores ......... PROV Imprefarcol, PROV GlobalPack
--   operadores 3PL ...... MELONN (Bogotá/Medellín/Barranquilla), Dropi (Cali/Bogotá/Medellín)
-- En los cuatro casos el stock es NUESTRO —por eso su ubicación sigue siendo `internal`—
-- pero «cuánto tengo en bodega» y «cuánto tengo en el maquilador» son preguntas distintas.
-- Se expone el código para poder separarlas sin adivinar por el nombre.
CREATE TABLE IF NOT EXISTS marts.dim_almacen (
    almacen_id BIGINT PRIMARY KEY,           -- stock.warehouse id
    codigo     TEXT,                         -- code (MBOG, PIMP, EVE, BIO…)
    nombre     TEXT,
    empresa_id BIGINT,                       -- res.company id (1 y 8, las mismas de contabilidad)
    activo     BOOLEAN,
    _loaded_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota')
);

-- ── dim_ubicacion ────────────────────────────────────────────────────────────
-- Hace falta aparte de `dim_almacen` por dos motivos MEDIDOS:
--   1. ⚠ **4 ubicaciones internas NO tienen `warehouse_id`**: «Physical Locations/Ubicación
--      de subcontratación» (dos), «Temporal» y «gior». Las cuatro están ACTIVAS. Son
--      existencia real, así que se agrupan bajo el centinela -1 = «(sin almacén)» y NO se
--      descartan: descartarlas descuadraría el total.
--   2. ⚠ El nombre NO es único: `EVE/Existencias` existe en los almacenes 5 y 18, y
--      `GBL/Existencias` dos veces en el 31. Se agrupa por ID, jamás por nombre — es el
--      mismo error que costó cinco caídas con los nombres de cliente en este repo.
CREATE TABLE IF NOT EXISTS marts.dim_ubicacion (
    ubicacion_id BIGINT PRIMARY KEY,         -- stock.location id
    nombre       TEXT,                       -- complete_name (BIO/Existencias, MBOG/Stock…)
    usage        TEXT,                       -- internal | customer | supplier | production | …
    almacen_id   BIGINT,                     -- stock.warehouse id, o -1 si no tiene
    empresa_id   BIGINT,
    _loaded_at   TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota')
);

CREATE INDEX IF NOT EXISTS ix_dim_ubicacion_usage ON marts.dim_ubicacion (usage);

-- ── fact_inventario_dia ──────────────────────────────────────────────────────
-- ⚠⚠ LA HISTORIA, Y ES IRREVERSIBLE. `stock.quant` solo conoce el AHORA: ningún
-- `--rebuild` devolverá el stock de la semana pasada. Si esta tabla no empieza a acumular
-- el primer día, ese dato se pierde PARA SIEMPRE. Es el único error irreversible de este
-- diseño, y por eso la tabla entra el mismo día que la foto aunque el tablero todavía no
-- la use.
--
-- Grano: UN DÍA × producto × ubicación × lote. El cierre del día gana (el último refresco
-- sobrescribe), que es la convención de un snapshot periódico.
--
-- ⚠ PRIMERA TABLA DEL REPO CON PK COMPUESTA en vez del id de Odoo, y es inevitable: un
-- snapshot por fecha no tiene un id natural. Se dice aquí para que no parezca un descuido
-- frente a la convención de docs/MODELO_ESTRELLA.md. Hay precedente parcial: las
-- `map_*` y `dim_kit_componente` tampoco usan id de Odoo.
--
-- ⚠⚠ LAS MEDIDAS SON SEMI-ADITIVAS: suman por producto, ubicación y almacén, pero **NO
-- por fecha**. Sumar dos días da el doble del inventario, no la existencia de dos días.
-- Para una serie se toma el cierre del día (o el promedio), nunca la suma.
--
-- El lote va en el grano porque está en el de `stock.quant`, pero ⚠ solo el 52,4 % de los
-- quants lo tiene: `lote_id = -1` es «sin lote», no un hueco de datos.
CREATE TABLE IF NOT EXISTS marts.fact_inventario_dia (
    fecha_key    INTEGER NOT NULL,           -- AAAAMMDD, como dim_fecha
    producto_id  BIGINT  NOT NULL,
    ubicacion_id BIGINT  NOT NULL,
    lote_id      BIGINT  NOT NULL DEFAULT -1,
    almacen_id   BIGINT,                     -- degenerada: se copia de dim_ubicacion para no
                                             -- pagar el join en cada consulta del tablero
    empresa_id   BIGINT,
    cantidad     NUMERIC(18, 4),             -- stock.quant.quantity      (ALMACENADO)
    reservada    NUMERIC(18, 4),             -- stock.quant.reserved_quantity (ALMACENADO)
    _loaded_at   TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota'),
    PRIMARY KEY (fecha_key, producto_id, ubicacion_id, lote_id)
);

CREATE INDEX IF NOT EXISTS ix_fact_inv_dia_fecha ON marts.fact_inventario_dia (fecha_key);
CREATE INDEX IF NOT EXISTS ix_fact_inv_dia_prod  ON marts.fact_inventario_dia (producto_id);
CREATE INDEX IF NOT EXISTS ix_fact_inv_dia_alm   ON marts.fact_inventario_dia (almacen_id);

COMMENT ON TABLE marts.fact_inventario_dia IS
  'Snapshot DIARIO de existencias. Grano fecha x producto x ubicacion x lote. '
  'Medidas SEMI-ADITIVAS: suman por producto/ubicacion/almacen pero NO por fecha. '
  'Solo ubicaciones con usage=internal. Sin columna de valor a proposito (decision de '
  'William: bodega no ve costes). Irreversible: stock.quant solo conoce el ahora.';

-- ── fact_inventario_movimiento_dia ───────────────────────────────────────────
-- El «qué entró y qué salió hoy» de la pantalla. Sale de `stock.move.line`, que tiene
-- ⚠ 1.047.415 filas: NUNCA se lee entero. El ETL lo acota por fecha (`date >= hoy`) y por
-- `state = 'done'`. Medido el 2026-08-13 a las 14:00: 157 líneas hechas hoy, 2.128 en las
-- últimas 24 h. O sea que acotado es barato.
--
-- Grano: día × producto × ubicación origen × ubicación destino. El signo lo da la
-- dirección: se guarda la cantidad y las dos ubicaciones, y el tablero decide si para una
-- ubicación concreta fue entrada o salida. Así una misma línea no se cuenta dos veces.
CREATE TABLE IF NOT EXISTS marts.fact_inventario_movimiento_dia (
    fecha_key      INTEGER NOT NULL,
    producto_id    BIGINT  NOT NULL,
    origen_id      BIGINT  NOT NULL,         -- stock.location de salida
    destino_id     BIGINT  NOT NULL,         -- stock.location de entrada
    cantidad       NUMERIC(18, 4),
    lineas         INTEGER,
    _loaded_at     TIMESTAMP DEFAULT (now() AT TIME ZONE 'America/Bogota'),
    PRIMARY KEY (fecha_key, producto_id, origen_id, destino_id)
);

CREATE INDEX IF NOT EXISTS ix_fact_inv_mov_fecha ON marts.fact_inventario_movimiento_dia (fecha_key);

COMMENT ON TABLE marts.fact_inventario_movimiento_dia IS
  'Movimientos de inventario HECHOS (state=done), agregados por dia x producto x origen x '
  'destino. stock.move.line tiene 1.047.415 filas: se acota por fecha SIEMPRE. El signo lo '
  'decide el tablero segun la ubicacion que mire, para no contar una linea dos veces.';

-- ── v_inventario_actual ──────────────────────────────────────────────────────
-- La foto vigente: el último día cargado. Es la vista que alimenta la MV del tablero.
--
-- ⚠ `libre = cantidad - reservada`, las dos ALMACENADAS. NO se usa
-- `stock.quant.available_quantity`, que es la versión computada de esta misma resta.
DROP VIEW IF EXISTS marts.v_inventario_actual CASCADE;

CREATE VIEW marts.v_inventario_actual AS
WITH ultimo AS (
    SELECT MAX(fecha_key) AS fecha_key FROM marts.fact_inventario_dia
)
SELECT f.fecha_key,
       f.producto_id,
       f.ubicacion_id,
       f.lote_id,
       COALESCE(f.almacen_id, -1)                                   AS almacen_id,
       COALESCE(f.empresa_id, -1)                                   AS empresa_id,
       u.nombre                                                     AS ubicacion,
       -- ⚠ El `usage` se EXPONE aunque la vista ya filtre solo `internal`, y no es
       -- redundante: es lo que permite a la intranet **verificar** la garantía en vez de
       -- confiar en ella. `check_marts §7z` comprueba que todas las filas sean `internal`,
       -- y sin esta columna tendría que mirar `dim_ubicacion`, que está NEGADA al rol de la
       -- app — o sea que el control no podría existir. Un guardarraíl que no puede leer lo
       -- que vigila no es un guardarraíl.
       COALESCE(u.usage, '(sin usage)')                             AS usage,
       COALESCE(NULLIF(btrim(a.codigo), ''), '(sin almacen)')       AS almacen_codigo,
       COALESCE(NULLIF(btrim(a.nombre), ''), '(sin almacen)')       AS almacen,
       p.codigo                                                     AS producto_codigo,
       COALESCE(NULLIF(btrim(p.nombre_comercial), ''), p.nombre)    AS producto,
       COALESCE(NULLIF(btrim(p.categoria), ''), '(sin categoria)')  AS categoria,
       f.cantidad,
       f.reservada,
       (COALESCE(f.cantidad, 0) - COALESCE(f.reservada, 0))         AS libre,
       f._loaded_at                                                 AS foto_at
FROM marts.fact_inventario_dia f
JOIN ultimo ON ultimo.fecha_key = f.fecha_key
LEFT JOIN marts.dim_ubicacion u ON u.ubicacion_id = f.ubicacion_id
LEFT JOIN marts.dim_almacen   a ON a.almacen_id   = f.almacen_id
LEFT JOIN marts.dim_producto  p ON p.producto_id  = f.producto_id;

COMMENT ON VIEW marts.v_inventario_actual IS
  'La foto vigente de existencias (el ultimo dia cargado de fact_inventario_dia), con los '
  'nombres resueltos. `libre` = cantidad - reservada, las dos columnas ALMACENADAS de '
  'stock.quant (NO available_quantity, que es computada). Solo usage=internal.';

-- ── v_inventario_movimiento_hoy ──────────────────────────────────────────────
-- El movimiento del último día cargado, con los nombres resueltos y el `usage` de las dos
-- puntas: el tablero necesita el `usage` para saber si, desde el punto de vista de la
-- bodega, la línea fue una ENTRADA (viene de `supplier`/`production`) o una SALIDA (va a
-- `customer`/`production`). Un traslado entre dos internas no es ni una cosa ni la otra, y
-- contarlo como las dos es el error fácil aquí.
DROP VIEW IF EXISTS marts.v_inventario_movimiento_hoy CASCADE;

CREATE VIEW marts.v_inventario_movimiento_hoy AS
WITH ultimo AS (
    SELECT MAX(fecha_key) AS fecha_key FROM marts.fact_inventario_movimiento_dia
)
SELECT m.fecha_key,
       m.producto_id,
       p.codigo                                                    AS producto_codigo,
       COALESCE(NULLIF(btrim(p.nombre_comercial), ''), p.nombre)   AS producto,
       COALESCE(NULLIF(btrim(p.categoria), ''), '(sin categoria)') AS categoria,
       m.origen_id,
       o.nombre                                                    AS origen,
       COALESCE(o.usage, '(sin usage)')                            AS origen_usage,
       m.destino_id,
       d.nombre                                                    AS destino,
       COALESCE(d.usage, '(sin usage)')                            AS destino_usage,
       m.cantidad,
       m.lineas,
       m._loaded_at                                                AS foto_at
FROM marts.fact_inventario_movimiento_dia m
JOIN ultimo ON ultimo.fecha_key = m.fecha_key
LEFT JOIN marts.dim_ubicacion o ON o.ubicacion_id = m.origen_id
LEFT JOIN marts.dim_ubicacion d ON d.ubicacion_id = m.destino_id
LEFT JOIN marts.dim_producto  p ON p.producto_id  = m.producto_id;

COMMENT ON VIEW marts.v_inventario_movimiento_hoy IS
  'Movimientos del ultimo dia cargado, con el `usage` de origen y destino para que el '
  'tablero decida entrada/salida sin adivinar. Un traslado entre dos internas no es '
  'ninguna de las dos: contarlo como ambas es el error facil de este panel.';

-- ============================================================================
-- ⚠⚠ POR QUÉ ESTA HOJA NO LLEVA VISTAS MATERIALIZADAS (decisión deliberada)
--
-- Las otras siete hojas leen MV porque sus vistas base escanean el hecho contable: la de
-- compras recorre 31.820 líneas con seis joins, así que materializar es la diferencia
-- entre 2 s y 20 ms. Aquí no: la foto de un día son **1.366 filas** sobre un índice, y las
-- vistas de arriba responden en milisegundos sin materializar nada.
--
-- Materializar tendría un coste y ningún beneficio, y en esta hoja el coste es justo lo
-- que se quiere evitar: **una MV añade una ventana en la que el dato mostrado NO es el
-- cargado**, y el objetivo declarado de este tablero es la frescura. También ahorra el
-- footgun de que recrear una MV se lleva su GRANT.
--
-- ⚠ La consecuencia que hay que respetar en la intranet: al no haber MV, estas vistas NO
-- aparecen en `marts.bi_mv_refresh`, así que **`marts.cached_panel` NO sirve para esta
-- hoja** — su clave sale de la marca de refresco del ETL y se quedaría sirviendo la foto
-- vieja sin dar ningún error. Es la trampa de `MarketingMeta` otra vez. La hoja usa su
-- propia versión de datos, `MAX(_loaded_at)`, exactamente como hace `services/pagos_db.py`
-- con la base viva de pagos, que es el precedente correcto para una fuente sin ETL.
-- ============================================================================
