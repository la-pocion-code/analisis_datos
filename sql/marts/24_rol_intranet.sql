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

-- ⚠ Las columnas nuevas se AÑADEN AL FINAL. `CREATE OR REPLACE VIEW` puede
-- agregar columnas al final pero **no puede quitarlas ni reordenarlas**: insertar
-- una en medio hace fallar la re-ejecución con «cannot change name of view column».
-- Y este archivo se re-ejecuta cada vez que se reconstruyen las MV.
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

CREATE OR REPLACE VIEW marts.v_lk_producto AS
SELECT p.producto_id,
       p.codigo,
       p.nombre,
       p.nombre_comercial,
       COALESCE(NULLIF(btrim(p.nombre_comercial), ''), p.nombre) AS etiqueta,
       p.categoria,
       p.es_kit,
       -- Línea y categoría COMERCIALES (fase 2 de la hoja de Ventas). Salen de
       -- `bi_lineas`, que es el Excel «LINEAS Y CATEGORIAS.xlsx».
       --
       -- ⚠ EL JOIN VA POR EL CÓDIGO DE LOS CORCHETES, no por el nombre.
       -- `bi_lineas.producto` viene con el formato «[PCN01] TRATAMIENTO LA POCION»,
       -- y ese nombre NO coincide letra a letra con `dim_producto.nombre`. Medido:
       --     por código  → 35/35 filas casan, 94,43 % del valor de 2026
       --     por nombre  → 16/35 filas casan, 39,90 %   ← el error fácil
       -- Con el join malo la mayor parte del negocio cae en «(sin línea)» y el
       -- tablero parece tener un hueco de datos que no existe.
       --
       -- El 5,57 % restante son 5 productos reales que FALTAN en el Excel:
       -- PCN32/33/34/35/36 (CONTROL CASPA y ANTICAÍDA). Se ven como «(sin línea)»
       -- hasta que el negocio los añada; la intranet publica la cobertura.
       bl.linea                                                 AS linea,
       bl.categoria                                             AS linea_categoria
FROM marts.dim_producto p
LEFT JOIN marts.bi_lineas bl
       ON upper(btrim(substring(bl.producto FROM '\[(.*?)\]'))) = upper(btrim(p.codigo));

COMMENT ON VIEW marts.v_lk_producto IS
  'Lookup de productos para los tableros. `etiqueta` = nombre_comercial si '
  'existe, si no el nombre técnico (es lo que se muestra en los gráficos). '
  '`linea`/`linea_categoria` vienen de bi_lineas por CÓDIGO (94,4 % del valor).';

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

-- Semillas que la intranet necesita para armar los estados: el catálogo y el orden
-- de los renglones derivados, y la tasa de renta por empresa. Van en la base y no
-- en el código de la intranet para que no se dupliquen ni deriven.
GRANT SELECT ON
    marts.bi_pyg_renglon,
    marts.bi_tasa_renta,
    marts.bi_producto_lanzamiento,    -- fecha de lanzamiento (dato de negocio)
    marts.bi_ciclo_vida               -- tramos de ciclo de vida y meta de crecimiento
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
-- estados y que no tendría sentido duplicar en código Python. `bi_lineas` es el
-- contraejemplo: es el Excel crudo, y la intranet lo ve solo a través de
-- `v_lk_producto`, que además ya resuelve el join por código.
--
-- ⚠ La hoja de contabilidad NO fue una excepción a eso: se concedieron sus siete MV
-- agregadas y `v_lk_cuenta`, pero el hecho contable y `dim_cuenta` siguen negados.
-- Un tablero necesita totales por mes, no el detalle de cada asiento.
