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

-- ⚠ Con `CREATE OR REPLACE VIEW` las columnas nuevas se AÑADEN AL FINAL: se pueden
-- agregar al final pero **no quitar ni reordenar** — insertar una en medio hace
-- fallar la re-ejecución con «cannot change name of view column», y este archivo se
-- re-ejecuta cada vez que se reconstruyen las MV. Para QUITAR una columna hay que
-- hacer DROP + CREATE, como en `v_lk_producto` más abajo.
CREATE OR REPLACE VIEW marts.v_lk_tercero AS
SELECT t.tercero_id,
       t.nombre,
       t.tipo_cliente,
       t.ciudad,
       t.departamento,
       t.pais,
       t.cliente_padre,
       -- Zona comercial por departamento (fase 2 de la hoja de Ventas).
       -- ⚠ `map_zona` es la ZONIFICACIÓN DEL CANAL MAYORISTA: sus 37 filas son
       -- todas de MAYORISTA NV. Pero el mapeo es departamento → zona, así que aquí
       -- queda poblada para CUALQUIER cliente con departamento colombiano (~97,8 %
       -- de los terceros; el resto no tiene departamento). Dentro del mayorista cubre el
       -- 100 % del valor —las 4 zonas suman exactamente su total—; usarla en otro
       -- canal es legítimo como corte geográfico, pero es la zonificación del
       -- mayorista, no una regional propia de ese canal.
       -- Los departamentos extranjeros (California/US, Guayas/EC, Lima/PE,
       -- Distrito Nacional/DO) están mapeados a la etiqueta literal 'sin zona';
       -- NULL es «departamento sin mapear o vacío».
       --
       -- No se usa `map_zona_cundinamarca`: ese mapeo parte Cundinamarca en BOGOTA
       -- NORTE / BOGOTA SUR, una sub-zona más fina que las 4 del informe, y
       -- `map_zona` ya resuelve Cundinamarca → CENTRO. Mezclarlos duplicaría filas.
       --
       -- El JOIN no multiplica: se verificó que ningún departamento tiene dos zonas.
       mz.zona                                                  AS zona
FROM marts.dim_tercero t
LEFT JOIN marts.map_zona mz
       ON btrim(upper(mz.departamento)) = btrim(upper(t.departamento))
      AND btrim(upper(mz.categoria))    = 'MAYORISTA NV';

COMMENT ON VIEW marts.v_lk_tercero IS
  'Lookup de clientes para los tableros: solo lo descriptivo. Excluye a '
  'propósito identificacion (NIT), telefono, email y etiqueta. `zona` es la '
  'zonificación del canal MAYORISTA NV aplicada al departamento del cliente.';

-- ⚠ DROP + CREATE y no `CREATE OR REPLACE`: esta vista **perdió** la columna
-- `linea_categoria` (2026-07-30) y `CREATE OR REPLACE VIEW` puede añadir columnas al
-- final pero **no quitarlas** — fallaría con «cannot drop columns from view». Va sin
-- CASCADE a propósito: si algún día alguien crea algo que dependa de este lookup,
-- preferimos que el script falle a que se lo lleve por delante en silencio.
DROP VIEW IF EXISTS marts.v_lk_producto;

CREATE VIEW marts.v_lk_producto AS
SELECT p.producto_id,
       p.codigo,
       p.nombre,
       p.nombre_comercial,
       COALESCE(NULLIF(btrim(p.nombre_comercial), ''), p.nombre) AS etiqueta,
       p.categoria,
       p.es_kit,
       -- ── LÍNEA COMERCIAL: sale del ÁRBOL DE CATEGORÍAS DE ODOO ──────────────
       -- Decisión de William (2026-07-30): la fuente de verdad es Odoo, no un Excel.
       -- Y al medirlo, además de ser la fuente correcta es objetivamente mejor:
       --
       --                            bi_lineas (Excel)   dim_producto.categoria (Odoo)
       --   cobertura del valor .....     94,43 %              100,000 %  (2024/25/26)
       --   productos sin línea .....       5                     0
       --
       -- Los 5 que el Excel no tenía sí están en Odoo: PCN32/33/36 en «Línea Control
       -- Caspa» —una línea entera que al Excel le falta— y PCN34/35 en «Anti Caída».
       -- Las cifras coinciden: Reparación da 31.410.481.087 en 2025 contra los
       -- 31.410.435.978 que daba TRADICIONAL en el Excel.
       --
       -- ⚠ **NO volver a `bi_lineas`.** Su única ventaja aparente es tener 12 líneas
       -- en vez de 10, pero eso es porque parte «Especializada» en BITE ME +
       -- LANZAMIENTO + BOOSTER + PERFUME (verificado: suman lo mismo, 4.433 mill.).
       -- A cambio deja el 5,6 % del negocio sin clasificar.
       --
       -- La normalización quita el prefijo del árbol y luego el «Línea »/«Linea »
       -- inicial, porque el árbol de Odoo es inconsistente: unos nodos lo llevan y
       -- otros no («Pocion Plus», «Sport», «Anti Caída»), y «Linea Especializada» va
       -- sin acento. Resultado: Reparación · Tongole · Pocion Plus · Anti Caída ·
       -- Especializada · B8 · Kids · Facial · Sport · Control Caspa (+ Kits, Sachet,
       -- Accesorios y Add On's, que son formatos y no líneas, pero son nodos reales
       -- del mismo nivel y se muestran tal cual en vez de esconderlos).
       -- Verificado: ningún par de nodos colisiona en la misma etiqueta.
       --
       -- El `( Importado)?` cubre «Inventario/Producto Terminado Importado/…», que hoy
       -- tiene 2 productos sin ventas: sin él, el día que se vendan caerían en
       -- «(sin línea)» y parecería un hueco de datos.
       --
       -- NULL = el producto no es producto terminado (materia prima, empaques,
       -- gastos…). Es lo correcto: esos no tienen línea comercial.
       CASE WHEN p.categoria ~ '^Inventario/Producto Terminado( Importado)?/' THEN
           NULLIF(
             regexp_replace(
               regexp_replace(btrim(p.categoria),
                              '^Inventario/Producto Terminado( Importado)?/', ''),
               '^L[ií]nea\s+', '', 'i'),
             '')
       END                                                      AS linea
FROM marts.dim_producto p;

COMMENT ON VIEW marts.v_lk_producto IS
  'Lookup de productos para los tableros. `etiqueta` = nombre_comercial si '
  'existe, si no el nombre técnico (es lo que se muestra en los gráficos). '
  '`linea` sale del arbol de categorias de ODOO (100 % de cobertura del valor); '
  'NO de bi_lineas. La categoria de producto (SHAMPOO/MASCARILLA/...) NO existe '
  'en Odoo y por eso ya no se expone.';

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

-- Vistas materializadas de dashboards (hoja Contabilidad — fase 2).
-- DDL en 26_contabilidad_dashboards.sql ⇒ este archivo (24) hay que RE-EJECUTARLO
-- después del 26, aunque numéricamente vaya antes. Mismo caso que ya ocurre con 23.
-- Se conceden solo las AGREGACIONES: el hecho contable (4,37 M de líneas, con el
-- detalle de cada asiento) y `dim_cuenta` siguen fuera del alcance del rol.
GRANT SELECT ON
    marts.mv_contab_cuenta_mes,       -- movimiento por empresa × mes × cuenta
    marts.mv_contab_detalle_mes,      -- detalle por cuenta × tercero × categoría × país × línea
    marts.mv_balance_mes,             -- estado de situación financiera (densa)
    marts.mv_pyg_mes,                 -- estado de resultados (grano N4)
    marts.mv_flujo_mes,               -- flujo de efectivo (renglones agregables)
    marts.mv_contab_tercero_mes,      -- comportamiento de clientes/proveedores
    marts.mv_contab_centro_mes,       -- comportamiento por centro de costo
    marts.mv_contab_canal_mes         -- comportamiento por canal
TO intranet_ro;

-- Vistas materializadas de dashboards (hoja Ventas — fase 2).
-- DDL en 27_ventas_dashboards_fase2.sql ⇒ mismo caso que 23 y 26: re-ejecutar ESTE
-- archivo después del 27.
GRANT SELECT ON
    marts.mv_ventas_kit_mes,          -- unidades y valor a nivel de KIT
    marts.mv_ventas_cliente_primera,  -- primera/última compra por cliente
    marts.mv_ventas_recompra          -- tasa de recompra por nivel
TO intranet_ro;

-- Vistas materializadas de dashboards (hoja Nielsen — panel de mercado).
-- DDL en 28_nielsen_dashboards.sql ⇒ re-ejecutar ESTE archivo después del 28.
-- ⚠ `bi_nielsen` CRUDO sigue negado: la intranet solo ve las dos MV tipadas.
GRANT SELECT ON
    marts.mv_nielsen_semana,          -- agregada: share, ranking y series
    marts.mv_nielsen_item_semana      -- detalle por item: ranking de productos y dist_num
TO intranet_ro;

-- Semillas que la intranet necesita para armar los estados: el catálogo y el orden
-- de los renglones derivados, y la tasa de renta por empresa. Van en la base y no
-- en el código de la intranet para que no se dupliquen ni deriven.
GRANT SELECT ON
    marts.bi_pyg_renglon,
    marts.bi_tasa_renta,
    marts.bi_producto_lanzamiento,    -- fecha de lanzamiento (dato de negocio)
    marts.bi_ciclo_vida,              -- tramos de ciclo de vida y meta de crecimiento
    marts.bi_nielsen_market,          -- metadatos de los universos (cual es el total)
    marts.bi_nielsen_marca_propia     -- marcas de la casa dentro del panel
TO intranet_ro;

-- Bitácora de refresco: la intranet la lee para invalidar su caché y mostrar
-- "datos actualizados hace X". Solo SELECT (la escribe el ETL con su rol).
GRANT SELECT ON marts.bi_mv_refresh TO intranet_ro;

-- Lookups descriptivos.
GRANT SELECT ON
    marts.v_lk_tercero,
    marts.v_lk_producto,
    marts.v_lk_vendedor,
    marts.v_lk_empresa,
    marts.v_lk_cuenta        -- PUC + clasificación derivada (26_*.sql)
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
-- dim_tercero, dim_producto, v_ventas_bi, v_ventas_explotada, v_ventas_producto,
-- v_cartera ni las bi_* CRUDAS (bi_nielsen, bi_lineas, bi_presupuesto,
-- bi_cuentas_clave*, bi_cartera, bi_cliente_credito…). Cuando se construyan las
-- hojas de Nielsen / cuentas clave / cartera, se añadirán aquí sus MV
-- correspondientes (nunca las tablas base).
--
-- ⚠ Las cinco `bi_*` que SÍ se conceden son SEMILLAS CURADAS, no volcados de Excel:
-- bi_pyg_renglon, bi_tasa_renta, bi_producto_lanzamiento, bi_ciclo_vida y
-- bi_mv_refresh. Son catálogos pequeños que la intranet necesita para armar sus
-- estados y que no tendría sentido duplicar en código Python.
--
-- ⚠ `bi_lineas` ya NO se usa para nada (2026-07-30). El ETL la sigue cargando y el
-- .pbix la sigue leyendo, pero ningún objeto concedido a la intranet la mira: la
-- línea comercial sale del árbol de categorías de Odoo. Ver el comentario largo en
-- `v_lk_producto` antes de volver a engancharla «porque tiene más líneas».
--
-- ⚠ La hoja de contabilidad NO fue una excepción a eso: se concedieron sus siete MV
-- agregadas y `v_lk_cuenta`, pero el hecho contable y `dim_cuenta` siguen negados.
-- Un tablero necesita totales por mes, no el detalle de cada asiento.
