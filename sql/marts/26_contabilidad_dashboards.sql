-- ============================================================================
-- CONTABILIDAD para los dashboards de la INTRANET. Idempotente.
-- Archivo: sql/marts/26_contabilidad_dashboards.sql
-- Ejecutar DESPUÉS de 23, y volver a ejecutar 24_rol_intranet.sql DESPUÉS de
-- este (el 24 concede los GRANT sobre lo que aquí se crea, y numéricamente va
-- antes; mismo caso que ya ocurre entre 23 y 24).
--
-- ¿QUÉ SUSTITUYE? La hoja «Informe Contabilidad» del PBIX, que son SEIS
-- sub-páginas: PYG · Situación Financiera · Flujo de Efectivo · Comportamientos
-- · Detalle · KPI's. En Power BI todo eso sale de `fact_movimiento_contable`
-- (4,37 M de líneas) más 14 COLUMNAS CALCULADAS DAX sobre `dim_cuenta`, y las
-- matrices evalúan un SWITCH de ~18 medidas por celda. La intranet solo hace
-- SELECT, así que esa clasificación y esa agregación bajan aquí.
--
-- ⚠ REGLA DEL PROYECTO: todo lo de base de datos vive en ESTE repo. La intranet
-- solo consume (rol `intranet_ro`). Contrato en docs/dashboards_intranet.md.
--
-- ── MEDIDO ANTES DE ESCRIBIR ESTO (2026-07-29, producción) ───────────────────
--   Rango contable ......... 2023-12-31 → 2026-08-09  (33 meses)
--   Líneas del hecho ....... 4.375.278
--   SUM(debito)-SUM(credito) = -0,01  ⇒ la partida doble cuadra y la historia
--       está COMPLETA: el asiento de apertura de la empresa 1 está en el
--       2023-12-31 (681 líneas, netas a 0). Sin esto, `saldo_acum` estaría
--       desplazado por una constante y el balance seguiría "cuadrando" —el error
--       sería simétrico e invisible—. La empresa 8 arranca el 2025-12-01.
--   Cuentas: clases 1/2/3 = 960 · clases 4/5/6/7 = 938 · clases 8/9 = 48 SIN UN
--       SOLO MOVIMIENTO (cuentas de orden) ⇒ se excluyen sin coste.
--   Cuentas sin `codigo` en clases 1-7: 0 ⇒ NO hay fallback a `nivel_movimiento`
--       (además ese campo solo está poblado para la empresa 8).
--   Terceros con movimiento contable: 137.612 · centros de costo: 62 de 67.
--   Cuentas usadas por UNA sola empresa: 923; por las DOS: 1 ⇒ los dos PUC son
--       casi disjuntos (ver la regla de empresa única, más abajo).
--
-- ── LAS REGLAS EXACTAS, VERIFICADAS CONTRA EL INFORME ────────────────────────
-- PCN (empresa 8), mayo-2026. Lo que sale de este SQL coincide AL PESO con el
-- PBIX:
--     ingresos operacionales  6.830.236.960
--     costo de ventas         2.801.095.140
--     gastos admin              404.721.946
--     gastos de ventas        2.651.211.097
--     D&A (línea)                 3.132.892
--     D&A total (EBITDA)          6.469.192
--     ingresos no op.            84.808.759
--     gastos no op.              80.724.548
--
-- ⚠ Y LAS DOS REGLAS DE GASTO **NO SON SIMÉTRICAS** — es el error más fácil de
--   cometer aquí:
--       gastos admin  = grupo 51  **EXCLUYENDO** cuenta_codigo 5160/5165
--       gastos ventas = grupo 52  **COMPLETO** (sí incluye 5260/5265)
--       D&A línea  = 5160, 5165          (el renglón que se resta en la UO)
--       D&A total  = 5160, 5165, 5260, 5265   (el addback del EBITDA)
--   Por eso `mv_pyg_mes` lleva `cuenta_codigo` (N4) EN EL GRANO: agregando solo
--   por grupo, 5160/5165 caen dentro del 51 y EBITDA, resultado operativo y la
--   línea de D&A dejan de ser calculables. No se puede recuperar después.
--
-- · Signo de presentación (de reportes-api/reports.py:24, validado):
--       clase_codigo IN ('4','2','3') → -1 ; el resto → +1
--   Así ingresos y gastos salen AMBOS positivos y utilidad bruta = ing - costo.
--
-- · Los estados financieros se atribuyen por **fecha contable** (`fecha_key`),
--   NO por `fecha_factura` como las MV de ventas. Son bases distintas a
--   propósito y está documentado en el contrato: "ingresos por cliente" de aquí
--   NO cuadra con `venta` del tablero de Ventas (aquí no se excluyen reversos ni
--   notas débito, porque contablemente son movimientos reales).
--
-- ── EMPRESA ÚNICA Y OBLIGATORIA ──────────────────────────────────────────────
-- Los estados financieros NUNCA se consolidan aquí. Cinco motivos acumulativos:
--   1. PUC distinto (923 cuentas de una sola empresa, 1 compartida).
--   2. HFA no tiene grupo 51: todo su gasto operativo va en el 52 ⇒ consolidado,
--      la fila "gastos de administración" sería la de PCN sola.
--   3. `seccion`/`concepto`/`nivel_movimiento` (de los reportes Odoo) solo están
--      poblados para la empresa 8.
--   4. INTERCOMPAÑÍA: en el informe, el 4.º proveedor de PCN es la propia
--      empresa 1 con 714 mill. Un consolidado lo duplicaría y nada aquí puede
--      detectarlo.
--   5. Las tasas de renta difieren (39 % HFA / 35 % PCN, ver bi_tasa_renta).
-- Por eso `empresa_id` es la PRIMERA columna de todos los índices y el servicio
-- de la intranet rechaza una consulta sin empresa.
--
-- ── IDEMPOTENCIA ─────────────────────────────────────────────────────────────
-- DROP + CREATE de las MV (no existe CREATE OR REPLACE MATERIALIZED VIEW). El
-- refresco rutinario NO usa este archivo: lo hace refrescar_mv_dashboards.py con
-- REFRESH ... CONCURRENTLY.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- SEMILLA 1 — Tasa de renta por empresa.
-- Va en SQL y no en el código de la intranet porque cambia con cada reforma
-- tributaria y porque la intranet solo lee. Verificado contra el informe:
-- 974.160.097 × 0,35 = 340.956.034, exacto.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_tasa_renta (
    empresa_id     BIGINT NOT NULL,
    vigente_desde  DATE   NOT NULL DEFAULT '2024-01-01',
    tasa           NUMERIC(5,4) NOT NULL,
    PRIMARY KEY (empresa_id, vigente_desde)
);

INSERT INTO marts.bi_tasa_renta (empresa_id, vigente_desde, tasa) VALUES
    (1, '2024-01-01', 0.3900),      -- HFA Aristizábal Héctor Fabio
    (8, '2024-01-01', 0.3500)       -- PCN Poción
ON CONFLICT (empresa_id, vigente_desde) DO UPDATE SET tasa = EXCLUDED.tasa;

COMMENT ON TABLE marts.bi_tasa_renta IS
  'Tasa de impuesto de renta por empresa. La provisión se calcula por empresa '
  'con SU tasa: un margen neto consolidado como un único ratio está mal.';


-- ════════════════════════════════════════════════════════════════════════════
-- SEMILLA 2 — Catálogo y ORDEN de los renglones del estado de resultados.
--
-- En Power BI estos renglones son "filas virtuales" añadidas a dim_cuenta por
-- Power Query, con `orden_informe` DECIMAL (5.1, 9.1, 10.1, 12.5, 15.5) para
-- colarse entre los renglones reales. Vive en SQL y no en un dict de Python para
-- que el orden no se duplique ni derive respecto a `v_dim_cuenta_bi`.
--
-- `tipo`:  base  = sale directo de mv_pyg_mes agregando por concepto_contable
--          calc  = aritmética sobre otros renglones (la hace el servicio)
-- `formula_id` identifica la aritmética; la intranet la implementa una sola vez.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_pyg_renglon (
    renglon    TEXT PRIMARY KEY,
    orden      NUMERIC(5,1) NOT NULL,
    tipo       TEXT NOT NULL CHECK (tipo IN ('base','calc')),
    formula_id TEXT,
    es_total   BOOLEAN NOT NULL DEFAULT FALSE,   -- se pinta en negrita
    pct_base   BOOLEAN NOT NULL DEFAULT FALSE    -- el 100 % de la columna %
);

TRUNCATE marts.bi_pyg_renglon;
INSERT INTO marts.bi_pyg_renglon (renglon, orden, tipo, formula_id, es_total, pct_base) VALUES
    ('INGRESOS OPERACIONALES',                    1.0, 'base', NULL,            FALSE, TRUE),
    ('COSTO DE VENTAS',                           2.0, 'base', NULL,            FALSE, FALSE),
    ('UTILIDAD BRUTA',                            3.0, 'calc', 'utilidad_bruta',TRUE,  FALSE),
    ('GASTOS OPERACIONALES DE ADMINISTRACIÓN',    4.0, 'base', NULL,            FALSE, FALSE),
    ('GASTOS OPERACIONALES DE VENTAS',            5.0, 'base', NULL,            FALSE, FALSE),
    ('GASTOS OP. Y DE VENTAS',                    5.1, 'calc', 'gastos_op',     TRUE,  FALSE),
    ('UTILIDAD OPERATIVA',                        6.0, 'calc', 'utilidad_op',   TRUE,  FALSE),
    ('DEPRECIACIÓN + AMORTIZACIÓN',               7.0, 'calc', 'dya_linea',     FALSE, FALSE),
    ('EBITDA',                                    8.0, 'calc', 'ebitda',        TRUE,  FALSE),
    ('INGRESOS NO OPERACIONALES',                 9.0, 'base', NULL,            FALSE, FALSE),
    ('TOTAL OTROS INGRESOS',                      9.1, 'calc', 'otros_ingresos',TRUE,  FALSE),
    ('GASTOS NO OPERACIONALES',                  10.0, 'base', NULL,            FALSE, FALSE),
    ('TOTAL GASTOS NO OPERACIONALES',            10.1, 'calc', 'otros_gastos',  TRUE,  FALSE),
    ('UTILIDAD ANTES DE IMPUESTOS',              12.0, 'calc', 'uai',           TRUE,  FALSE),
    ('PROVISIÓN DEL IMPUESTO DE RENTA',          12.5, 'calc', 'provision',     FALSE, FALSE),
    ('UTILIDAD (PÉRDIDA) DEL PERIODO',           15.0, 'calc', 'resultado',     TRUE,  FALSE),
    ('UTILIDAD/NETA',                            15.5, 'calc', 'utilidad_neta', TRUE,  FALSE);

COMMENT ON TABLE marts.bi_pyg_renglon IS
  'Catálogo y orden de los renglones del estado de resultados. `orden` es '
  'DECIMAL porque los renglones derivados se intercalan entre los reales '
  '(5.1, 9.1, 10.1, 12.5, 15.5), igual que en el informe de Power BI.';


-- ════════════════════════════════════════════════════════════════════════════
-- v_dim_cuenta_bi — las 14 columnas que hoy son DAX, como CASE en SQL.
--
-- ⚠ ES UNA VISTA, NO COLUMNAS MATERIALIZADAS EN dim_cuenta, y es deliberado:
-- el ETL carga dim_cuenta con un `upsert` cuyo ON CONFLICT DO UPDATE solo toca
-- las columnas del DataFrame, así que columnas añadidas por un ALTER+UPDATE
-- sobrevivirían en las cuentas existentes pero **cada cuenta nueva entraría con
-- las 14 en NULL** y caería a un bucket sin etiqueta hasta que alguien
-- re-ejecutara el UPDATE a mano. Y entran cuentas nuevas de continuo: 1.939 el
-- 1-jul → 1.944 el 23-jul → 1.945 el 29-jul. Son 1.945 filas: el CASE es gratis
-- y no hay nada que mantener sincronizado.
--
-- La clasificación va por CÓDIGO PUC (clase/grupo/N4) y NUNCA por
-- `nivel_movimiento`: ese campo (y `seccion`/`concepto`) solo está poblado para
-- la empresa 8, así que basar las medidas en él deja a HFA en blanco.
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW marts.v_dim_cuenta_bi AS
SELECT
    c.cuenta_id,
    c.codigo,
    c.nombre,
    c.clase_codigo,
    c.grupo_codigo,
    c.cuenta_codigo,                      -- N4 real de dim_cuenta (4 dígitos)
    c.subcuenta_codigo,
    c.clase_nombre, c.grupo_nombre, c.cuenta_nombre, c.subcuenta_nombre,
    c.seccion, c.concepto, c.nivel_movimiento,   -- ⚠ solo poblados en empresa 8
    c.codigo_canonico, c.nombre_canonico,

    -- ── Etiqueta de la cuenta individual (4.º nivel de la matriz de balance) ──
    (COALESCE(NULLIF(btrim(c.nombre), ''), 'CUENTA ' || c.cuenta_id))::TEXT
        AS cuenta_etiqueta,

    -- ── concepto_contable: el renglón del informe al que pertenece la cuenta ──
    CASE
        WHEN c.grupo_codigo = '41' THEN 'INGRESOS OPERACIONALES'
        WHEN c.grupo_codigo = '42' THEN 'INGRESOS NO OPERACIONALES'
        WHEN c.grupo_codigo = '61' THEN 'COSTO DE VENTAS'
        WHEN c.grupo_codigo = '62' THEN 'COMPRAS'
        WHEN c.clase_codigo = '7'  THEN 'COSTOS DE PRODUCCIÓN'
        -- ⚠ 5160/5165 salen del renglón de administración y forman el de D&A.
        WHEN c.cuenta_codigo IN ('5160','5165') THEN 'DEPRECIACIÓN + AMORTIZACIÓN'
        WHEN c.grupo_codigo = '51' THEN 'GASTOS OPERACIONALES DE ADMINISTRACIÓN'
        WHEN c.grupo_codigo = '52' THEN 'GASTOS OPERACIONALES DE VENTAS'
        WHEN c.grupo_codigo = '53' THEN 'GASTOS NO OPERACIONALES'
        WHEN c.grupo_codigo = '54' THEN 'IMPUESTO DE RENTA Y COMPLEMENTARIOS'
        WHEN c.grupo_codigo = '47' THEN 'IMPUESTO DIFERIDO (INGRESO)'
        WHEN c.grupo_codigo = '57' THEN 'IMPUESTO DIFERIDO (GASTO)'
        WHEN c.grupo_codigo = '59' THEN 'GANANCIAS Y PÉRDIDAS (CIERRE)'
        WHEN c.grupo_codigo = '11' THEN 'EFECTIVO Y EQ EFECTIVO'
        WHEN c.grupo_codigo = '12' THEN 'INVERSIONES'
        WHEN c.grupo_codigo = '13' THEN 'DEUDORES'
        WHEN c.grupo_codigo = '14' THEN 'INVENTARIOS'
        WHEN c.grupo_codigo = '15' THEN 'PROPIEDAD PLANTA Y EQUIPO'
        WHEN c.grupo_codigo = '16' THEN 'INTANGIBLES'
        WHEN c.grupo_codigo = '17' THEN 'DIFERIDOS'
        WHEN c.grupo_codigo = '18' THEN 'OTROS ACTIVOS'
        WHEN c.grupo_codigo = '19' THEN 'VALORIZACIONES'
        WHEN c.grupo_codigo = '21' THEN 'OBLIGACIONES FINANCIERAS'
        WHEN c.grupo_codigo = '22' THEN 'PROVEEDORES'
        WHEN c.grupo_codigo = '23' THEN 'CUENTAS POR PAGAR'
        WHEN c.grupo_codigo = '24' THEN 'IMPUESTOS'
        WHEN c.grupo_codigo = '25' THEN 'BENEFICIOS A EMPLEADOS'
        WHEN c.grupo_codigo = '26' THEN 'PASIVOS ESTIMADOS Y PROVISIONES'
        WHEN c.grupo_codigo = '27' THEN 'DIFERIDO'
        WHEN c.grupo_codigo IN ('28','29') THEN 'OTROS PASIVOS'
        WHEN c.clase_codigo = '3'  THEN 'PATRIMONIO'
        ELSE NULL
    END AS concepto_contable,

    -- ── orden_informe: independiente de concepto_contable, por código ────────
    CASE
        WHEN c.grupo_codigo = '41' THEN  1.0
        WHEN c.grupo_codigo = '61' THEN  2.0
        WHEN c.grupo_codigo = '51' AND c.cuenta_codigo NOT IN ('5160','5165') THEN 4.0
        WHEN c.grupo_codigo = '52' THEN  5.0
        WHEN c.cuenta_codigo IN ('5160','5165') THEN 7.0
        WHEN c.grupo_codigo = '42' THEN  9.0
        WHEN c.grupo_codigo = '53' THEN 10.0
        WHEN c.grupo_codigo = '54' THEN 12.5
        WHEN c.grupo_codigo = '62' THEN 20.0
        WHEN c.clase_codigo = '7'  THEN 21.0
        ELSE 99.0
    END AS orden_informe,

    -- ── categoria_gasto: N4, para la hoja de composición del gasto ───────────
    CASE
        WHEN c.clase_codigo <> '5' THEN NULL
        WHEN c.cuenta_codigo IN ('5105','5205') THEN 'Personal'
        WHEN c.cuenta_codigo IN ('5110','5210') THEN 'Honorarios'
        WHEN c.cuenta_codigo IN ('5115','5215') THEN 'Impuestos'
        WHEN c.cuenta_codigo IN ('5120','5220') THEN 'Arrendamientos'
        WHEN c.cuenta_codigo IN ('5125','5225') THEN 'Contribuciones y afiliaciones'
        WHEN c.cuenta_codigo IN ('5130','5230') THEN 'Seguros'
        WHEN c.cuenta_codigo IN ('5135','5235') THEN 'Servicios'
        WHEN c.cuenta_codigo IN ('5140','5240') THEN 'Gastos legales'
        WHEN c.cuenta_codigo IN ('5145','5245') THEN 'Mantenimiento y reparaciones'
        WHEN c.cuenta_codigo IN ('5150','5250') THEN 'Adecuación e instalación'
        WHEN c.cuenta_codigo IN ('5155','5255') THEN 'Gastos de viaje'
        WHEN c.cuenta_codigo IN ('5160','5260') THEN 'Depreciaciones'
        WHEN c.cuenta_codigo IN ('5165','5265') THEN 'Amortizaciones'
        WHEN c.cuenta_codigo IN ('5195','5295') THEN 'Diversos'
        WHEN c.cuenta_codigo IN ('5199','5299') THEN 'Provisiones'
        WHEN c.grupo_codigo = '53' THEN 'No operacionales'
        ELSE 'Otros'
    END AS categoria_gasto,

    -- ── Jerarquía del ESTADO DE SITUACIÓN FINANCIERA (4 niveles) ─────────────
    -- nivel 1
    CASE c.clase_codigo WHEN '1' THEN 'ACTIVO'
                        WHEN '2' THEN 'PASIVO'
                        WHEN '3' THEN 'PATRIMONIO' END        AS bal_nivel1,
    CASE c.clase_codigo WHEN '1' THEN 1 WHEN '2' THEN 2
                        WHEN '3' THEN 3 END                    AS orden_bal_nivel1,
    -- nivel 2: corriente / no corriente
    CASE
        WHEN c.clase_codigo = '1' AND c.grupo_codigo IN ('11','12','13','14') THEN 'CORRIENTE'
        WHEN c.clase_codigo = '1' THEN 'NO CORRIENTE'
        WHEN c.clase_codigo = '2' AND c.grupo_codigo IN ('21','22','23','24','25','26') THEN 'CORRIENTE'
        WHEN c.clase_codigo = '2' THEN 'NO CORRIENTE'
        WHEN c.clase_codigo = '3' THEN 'PATRIMONIO'
    END AS clasif_liquidez,
    CASE
        WHEN c.clase_codigo = '1' AND c.grupo_codigo IN ('11','12','13','14') THEN 1
        WHEN c.clase_codigo = '1' THEN 2
        WHEN c.clase_codigo = '2' AND c.grupo_codigo IN ('21','22','23','24','25','26') THEN 1
        WHEN c.clase_codigo = '2' THEN 2
        WHEN c.clase_codigo = '3' THEN 1
    END AS orden_liquidez,
    -- nivel 3: el grupo con nombre de negocio (mismo vocabulario que el informe)
    CASE
        WHEN c.clase_codigo IN ('1','2','3') THEN
            CASE
                WHEN c.grupo_codigo = '11' THEN 'EFECTIVO Y EQ EFECTIVO'
                WHEN c.grupo_codigo = '12' THEN 'INVERSIONES'
                WHEN c.grupo_codigo = '13' THEN 'DEUDORES'
                WHEN c.grupo_codigo = '14' THEN 'INVENTARIOS'
                WHEN c.grupo_codigo = '15' THEN 'PROPIEDAD PLANTA Y EQUIPO'
                WHEN c.grupo_codigo = '16' THEN 'INTANGIBLES'
                WHEN c.grupo_codigo = '17' THEN 'DIFERIDOS'
                WHEN c.grupo_codigo = '18' THEN 'OTROS ACTIVOS'
                WHEN c.grupo_codigo = '19' THEN 'VALORIZACIONES'
                WHEN c.grupo_codigo = '21' THEN 'OBLIGACIONES FINANCIERAS'
                WHEN c.grupo_codigo = '22' THEN 'PROVEEDORES'
                WHEN c.grupo_codigo = '23' THEN 'CUENTAS POR PAGAR'
                WHEN c.grupo_codigo = '24' THEN 'IMPUESTOS'
                WHEN c.grupo_codigo = '25' THEN 'BENEFICIOS A EMPLEADOS'
                WHEN c.grupo_codigo = '26' THEN 'PASIVOS ESTIMADOS Y PROVISIONES'
                WHEN c.grupo_codigo = '27' THEN 'DIFERIDO'
                WHEN c.grupo_codigo IN ('28','29') THEN 'OTROS PASIVOS'
                ELSE COALESCE(NULLIF(btrim(c.grupo_nombre), ''), 'PATRIMONIO')
            END
    END AS bal_nivel2,
    COALESCE(NULLIF(c.grupo_codigo, '')::INTEGER, 99)          AS orden_bal_nivel2,

    -- concepto_balance: alias de negocio del nivel 3, para el rollup del ESF
    CASE WHEN c.clase_codigo IN ('1','2','3')
         THEN COALESCE(NULLIF(btrim(c.grupo_nombre), ''), 'SIN CLASIFICAR') END
        AS concepto_balance,
    COALESCE(NULLIF(c.grupo_codigo, '')::INTEGER, 99)          AS orden_balance,

    -- ── Flujo de efectivo (método indirecto) — SOLO los renglones agregables.
    -- Los derivados (EFECTIVO GENERADO…, FLUJO DEL PERIODO) y los de stock
    -- (FLUJO DE CAJA INICIAL/FINAL) NO son un GROUP BY: los arma el servicio.
    CASE
        WHEN c.grupo_codigo = '13' THEN 'AUMENTO (DISMINUCIÓN) CUENTAS POR COBRAR'
        WHEN c.grupo_codigo = '14' THEN 'AUMENTO (DISMINUCIÓN) INVENTARIOS'
        WHEN c.grupo_codigo = '22' THEN 'AUMENTO (DISMINUCIÓN) PROVEEDORES'
        WHEN c.grupo_codigo = '23' THEN 'AUMENTO (DISMINUCIÓN) CUENTAS POR PAGAR'
        WHEN c.grupo_codigo IN ('17','18') THEN 'AUMENTO (DISMINUCIÓN) OTROS ACTIVOS CORRIENTES'
        WHEN c.grupo_codigo IN ('24','25','26','27','28','29')
             THEN 'AUMENTO (DISMINUCIÓN) OTROS PASIVOS CORRIENTES'
        WHEN c.grupo_codigo IN ('15','16') THEN 'CAPEX (PPE, INTANGIBLES)'
        WHEN c.grupo_codigo = '21' THEN 'AUMENTO (DISMINUCIÓN) OBLIGACIONES FINANCIERAS'
        WHEN c.clase_codigo = '3'  THEN 'AUMENTO (DISMINUCIÓN) APORTES SOCIALES'
        WHEN c.cuenta_codigo IN ('5160','5165','5260','5265') THEN 'DEPRECIACIÓN + AMORTIZACIÓN'
    END AS flujo_renglon,
    CASE
        WHEN c.grupo_codigo IN ('13','14','22','23','17','18','24','25','26','27','28','29')
             THEN 'ACTIVIDADES DE OPERACIÓN'
        WHEN c.cuenta_codigo IN ('5160','5165','5260','5265') THEN 'ACTIVIDADES DE OPERACIÓN'
        WHEN c.grupo_codigo IN ('15','16') THEN 'ACTIVIDADES DE INVERSIÓN'
        WHEN c.grupo_codigo = '21' THEN 'ACTIVIDADES DE FINANCIACIÓN'
        WHEN c.clase_codigo = '3'  THEN 'ACTIVIDADES DE FINANCIACIÓN'
    END AS flujo_actividad,
    CASE
        WHEN c.grupo_codigo IN ('13','14','22','23','17','18','24','25','26','27','28','29') THEN 1
        WHEN c.cuenta_codigo IN ('5160','5165','5260','5265') THEN 1
        WHEN c.grupo_codigo IN ('15','16') THEN 2
        WHEN c.grupo_codigo = '21' THEN 3
        WHEN c.clase_codigo = '3'  THEN 3
    END AS orden_flujo_actividad,

    -- ── Banderas y signos ───────────────────────────────────────────────────
    (c.cuenta_codigo IN ('5160','5165','5260','5265'))  AS es_dya,        -- addback EBITDA
    (c.cuenta_codigo IN ('5160','5165'))                AS es_dya_linea,  -- renglón del informe
    CASE WHEN c.clase_codigo = '4' THEN -1 ELSE 1 END::SMALLINT AS signo_pyg,
    CASE WHEN c.clase_codigo IN ('2','3') THEN -1 ELSE 1 END::SMALLINT AS signo_bal
FROM marts.dim_cuenta c;

COMMENT ON VIEW marts.v_dim_cuenta_bi IS
  'dim_cuenta + las 14 columnas que en Power BI son calculadas DAX. Es VISTA y '
  'no columnas materializadas porque el upsert del ETL no las mantendría y cada '
  'cuenta nueva entraría en NULL (dim_cuenta creció 1.939→1.945 en 4 semanas). '
  'Clasifica por CÓDIGO PUC, nunca por nivel_movimiento (solo poblado en la '
  'empresa 8).';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_contab_cuenta_mes — LA BASE. Es la única MV que escanea el hecho completo;
-- las cuatro siguientes DERIVAN de ella (así se pasa de 4 escaneos de 4,37 M a
-- 1). Grano: empresa × periodo × cuenta. Por FECHA CONTABLE.
-- Cardinalidad esperada: ~31.000 filas.
--
-- No se excluyen reversos: contablemente son movimientos reales. Se excluyen
-- las clases 8/9 (cuentas de orden), que además no tienen ni un movimiento.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_contab_cuenta_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_contab_cuenta_mes AS
SELECT
    f.empresa_id,
    d.periodo_aaaamm,
    d.anio,
    d.mes,
    date_trunc('month', d.fecha)::DATE                 AS fecha_mes,
    f.cuenta_id,
    SUM(f.debito)                                      AS debito,
    SUM(f.credito)                                     AS credito,
    SUM(f.debito - f.credito)                          AS movimiento,
    COUNT(*)                                           AS n_movimientos
FROM marts.fact_movimiento_contable f
JOIN marts.dim_fecha  d ON d.fecha_key = f.fecha_key
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.empresa_id IS NOT NULL
  AND c.clase_codigo IN ('1','2','3','4','5','6','7')
GROUP BY f.empresa_id, d.periodo_aaaamm, d.anio, d.mes, 5, f.cuenta_id;

CREATE UNIQUE INDEX ux_mv_contab_cuenta_mes
    ON marts.mv_contab_cuenta_mes (empresa_id, periodo_aaaamm, cuenta_id);
CREATE INDEX ix_mv_contab_cuenta_mes_anio
    ON marts.mv_contab_cuenta_mes (empresa_id, anio);

COMMENT ON MATERIALIZED VIEW marts.mv_contab_cuenta_mes IS
  'Movimiento contable agregado por empresa × mes × cuenta (fecha CONTABLE). '
  'Base de la que derivan mv_balance_mes, mv_pyg_mes y mv_flujo_mes: es la '
  'única que escanea el hecho completo.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_balance_mes — ESTADO DE SITUACIÓN FINANCIERA. Clases 1/2/3.
--
-- ⚠ ES DENSA A PROPÓSITO. Una cuenta bancaria sin movimientos en un mes SIGUE
-- TENIENDO SALDO: si la fila no existiera, la matriz la haría desaparecer en los
-- meses tranquilos y el acumulado saltaría de mes en mes sin hueco (se leería
-- como 0, o como blanco, en vez de "el mismo saldo que el mes anterior"). Por
-- eso se construye una rejilla (pares empresa/cuenta × todos los meses) y se le
-- hace LEFT JOIN al movimiento.
--
-- `saldo_acum` es correcto porque la historia está completa (ver la cabecera:
-- el asiento de apertura está en el hecho y la partida doble cuadra en -0,01).
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_balance_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_balance_mes AS
WITH pares AS (      -- pares (empresa, cuenta) que existen en el balance
    SELECT DISTINCT m.empresa_id, m.cuenta_id
      FROM marts.mv_contab_cuenta_mes m
      JOIN marts.dim_cuenta c ON c.cuenta_id = m.cuenta_id
     WHERE c.clase_codigo IN ('1','2','3')
), meses AS (        -- todos los meses con actividad contable
    SELECT DISTINCT periodo_aaaamm, anio, mes, fecha_mes
      FROM marts.mv_contab_cuenta_mes
), rejilla AS (
    SELECT p.empresa_id, p.cuenta_id, s.periodo_aaaamm, s.anio, s.mes, s.fecha_mes
      FROM pares p CROSS JOIN meses s
), con_mov AS (
    SELECT r.*,
           COALESCE(m.movimiento, 0)                  AS movimiento,
           SUM(COALESCE(m.movimiento, 0)) OVER (
               PARTITION BY r.empresa_id, r.cuenta_id
               ORDER BY r.periodo_aaaamm
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           )                                          AS saldo_acum
      FROM rejilla r
      LEFT JOIN marts.mv_contab_cuenta_mes m
             ON m.empresa_id     = r.empresa_id
            AND m.cuenta_id      = r.cuenta_id
            AND m.periodo_aaaamm = r.periodo_aaaamm
)
SELECT
    cm.empresa_id,
    cm.periodo_aaaamm,
    cm.anio,
    cm.mes,
    cm.fecha_mes,
    cm.cuenta_id,
    c.cuenta_etiqueta,
    c.codigo                                           AS cuenta_codigo_full,
    c.clase_codigo,
    c.grupo_codigo,
    COALESCE(c.bal_nivel1,      '(sin nivel)')         AS bal_nivel1,
    COALESCE(c.orden_bal_nivel1, 9)                    AS orden_bal_nivel1,
    COALESCE(c.clasif_liquidez, '(sin clasificar)')    AS clasif_liquidez,
    COALESCE(c.orden_liquidez,   9)                    AS orden_liquidez,
    COALESCE(c.bal_nivel2,      '(sin grupo)')         AS bal_nivel2,
    COALESCE(c.orden_bal_nivel2, 99)                   AS orden_bal_nivel2,
    cm.movimiento,
    cm.saldo_acum,
    -- Signo de presentación: ACTIVO, PASIVO y PATRIMONIO todos positivos.
    cm.saldo_acum * c.signo_bal                        AS saldo_presentacion,
    cm.movimiento * c.signo_bal                        AS movimiento_presentacion
FROM con_mov cm
JOIN marts.v_dim_cuenta_bi c ON c.cuenta_id = cm.cuenta_id;

CREATE UNIQUE INDEX ux_mv_balance_mes
    ON marts.mv_balance_mes (empresa_id, periodo_aaaamm, cuenta_id);
CREATE INDEX ix_mv_balance_mes_nivel
    ON marts.mv_balance_mes (empresa_id, periodo_aaaamm, bal_nivel1);
CREATE INDEX ix_mv_balance_mes_grupo
    ON marts.mv_balance_mes (empresa_id, grupo_codigo);

COMMENT ON MATERIALIZED VIEW marts.mv_balance_mes IS
  'Estado de situación financiera: empresa × mes × cuenta, clases 1/2/3. DENSA '
  '(rejilla de meses) porque una cuenta sin movimiento sigue teniendo saldo. '
  'Trae movimiento del mes Y saldo acumulado, con y sin signo de presentación. '
  '⚠ El informe de Power BI muestra el MOVIMIENTO y lo llama balance; aquí lo '
  'correcto es saldo_presentacion.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_pyg_mes — ESTADO DE RESULTADOS. Clases 4/5/6/7.
-- Grano: empresa × periodo × concepto_contable × cuenta_codigo (N4).
--
-- ⚠ EL N4 EN EL GRANO NO ES OPCIONAL. Sin él, 5160/5165 quedan dentro del grupo
-- 51 y se pierde para siempre la separación que hace calculables la línea de
-- D&A, la utilidad operativa y el EBITDA (que usa OTRO subconjunto: +5260/5265).
--
-- ⚠ `clase_codigo` se conserva separable y la clase 7 (costos de producción) NO
-- se suma al margen bruto: se capitaliza a inventario y sumarla con el 61
-- duplicaría el costo. Hoy no tiene movimiento, pero el día que lo tenga el
-- error sería silencioso.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_pyg_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_pyg_mes AS
SELECT
    m.empresa_id,
    m.periodo_aaaamm,
    m.anio,
    m.mes,
    m.fecha_mes,
    COALESCE(c.concepto_contable, '(sin concepto)')    AS concepto_contable,
    COALESCE(c.orden_informe, 99.0)                    AS orden_informe,
    COALESCE(NULLIF(btrim(c.cuenta_codigo), ''), '(sin n4)') AS cuenta_codigo,
    COALESCE(c.categoria_gasto, '(sin categoria)')     AS categoria_gasto,
    c.clase_codigo,
    c.grupo_codigo,
    bool_or(c.es_dya)                                  AS es_dya,
    bool_or(c.es_dya_linea)                            AS es_dya_linea,
    SUM(m.movimiento)                                  AS movimiento,
    -- Ingresos y gastos AMBOS positivos.
    SUM(m.movimiento * c.signo_pyg)                    AS valor_pyg
FROM marts.mv_contab_cuenta_mes m
JOIN marts.v_dim_cuenta_bi c ON c.cuenta_id = m.cuenta_id
WHERE c.clase_codigo IN ('4','5','6','7')
GROUP BY m.empresa_id, m.periodo_aaaamm, m.anio, m.mes, m.fecha_mes,
         6, 7, 8, 9, c.clase_codigo, c.grupo_codigo;

CREATE UNIQUE INDEX ux_mv_pyg_mes
    ON marts.mv_pyg_mes (empresa_id, periodo_aaaamm, concepto_contable,
                         cuenta_codigo, categoria_gasto);
CREATE INDEX ix_mv_pyg_mes_anio  ON marts.mv_pyg_mes (empresa_id, anio);
CREATE INDEX ix_mv_pyg_mes_grupo ON marts.mv_pyg_mes (empresa_id, grupo_codigo);

COMMENT ON MATERIALIZED VIEW marts.mv_pyg_mes IS
  'Estado de resultados por empresa × mes × concepto × cuenta N4. El N4 es '
  'imprescindible: gastos admin = grupo 51 EXCLUYENDO 5160/5165, gastos de '
  'ventas = grupo 52 COMPLETO, D&A línea = 5160/5165 y el addback del EBITDA = '
  '5160/5165/5260/5265. valor_pyg lleva el signo de presentación.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_flujo_mes — FLUJO DE EFECTIVO, solo los renglones AGREGABLES.
--
-- El estado completo mezcla tres cosas y solo una es un GROUP BY:
--   · Δ de una cuenta de balance (= movimiento del mes)  → SÍ, está aquí.
--   · renglón de P&G (D&A)                              → SÍ, está aquí.
--   · derivados (EFECTIVO GENERADO…, FLUJO DEL PERIODO) → los arma el servicio.
--   · stock (FLUJO DE CAJA INICIAL/FINAL)               → es saldo_acum de la
--     clase 11 al cierre del mes anterior / del mes: sale de mv_balance_mes.
--
-- ⚠ El flujo del informe de Power BI está roto: «ACTIVIDADES DE FINANCIACIÓN»
-- trae exactamente el mismo valor que «ACTIVIDADES DE INVERSIÓN» (85.188.799,95)
-- cuando las obligaciones financieras del mes son −1.702.189.935,80. NO se
-- replica ese cálculo.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_flujo_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_flujo_mes AS
SELECT
    m.empresa_id,
    m.periodo_aaaamm,
    m.anio,
    m.mes,
    m.fecha_mes,
    c.flujo_renglon,
    c.flujo_actividad,
    c.orden_flujo_actividad,
    -- Un AUMENTO de activo consume caja y un aumento de pasivo la genera: el
    -- signo se resuelve aquí para que el servicio solo sume.
    SUM(m.movimiento * CASE WHEN c.clase_codigo = '1' THEN -1 ELSE 1 END) AS valor_flujo,
    SUM(m.movimiento)                                                     AS movimiento
FROM marts.mv_contab_cuenta_mes m
JOIN marts.v_dim_cuenta_bi c ON c.cuenta_id = m.cuenta_id
WHERE c.flujo_renglon IS NOT NULL
GROUP BY m.empresa_id, m.periodo_aaaamm, m.anio, m.mes, m.fecha_mes,
         c.flujo_renglon, c.flujo_actividad, c.orden_flujo_actividad;

CREATE UNIQUE INDEX ux_mv_flujo_mes
    ON marts.mv_flujo_mes (empresa_id, periodo_aaaamm, flujo_renglon);

COMMENT ON MATERIALIZED VIEW marts.mv_flujo_mes IS
  'Flujo de efectivo (método indirecto), SOLO los renglones agregables. Los '
  'derivados y los de stock (flujo de caja inicial/final) los arma la intranet '
  'leyendo también mv_balance_mes y mv_pyg_mes. El signo ya está resuelto: un '
  'aumento de activo consume caja.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_contab_tercero_mes — para «Comportamientos» (top clientes/proveedores) y
-- la tabla de «Detalle».
--
-- ⚠ PIVOTADA A COLUMNAS, no 5 filas por tercero. Hay 137.612 terceros con
-- movimiento contable: con el concepto en filas serían ~1,7 M y dejaría de ser
-- una MV "pequeña". Pivotada son ~344.000 y el top-N se resuelve con LIMIT en
-- SQL, nunca trayéndola a Python.
--
-- Nota de semántica: en las líneas de gasto el tercero es el PROVEEDOR, no el
-- cliente. La columna `utilidad` solo significa algo para terceros que son las
-- dos cosas; por eso la etiqueta correcta es "tercero", no "cliente".
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_contab_tercero_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_contab_tercero_mes AS
SELECT
    f.empresa_id,
    d.periodo_aaaamm,
    d.anio,
    d.mes,
    COALESCE(f.tercero_id, -1)                         AS tercero_id,
    SUM(CASE WHEN c.grupo_codigo = '41' THEN -(f.debito - f.credito) ELSE 0 END) AS ingresos,
    SUM(CASE WHEN c.grupo_codigo = '61' THEN  (f.debito - f.credito) ELSE 0 END) AS costos,
    SUM(CASE WHEN c.grupo_codigo IN ('51','52') THEN (f.debito - f.credito) ELSE 0 END) AS gastos,
    SUM(CASE WHEN c.grupo_codigo = '42' THEN -(f.debito - f.credito) ELSE 0 END) AS ingresos_no_op,
    SUM(CASE WHEN c.grupo_codigo = '53' THEN  (f.debito - f.credito) ELSE 0 END) AS gastos_no_op,
    SUM(CASE WHEN c.grupo_codigo IN ('41','42') THEN -(f.debito - f.credito)
             WHEN c.grupo_codigo IN ('61','51','52','53') THEN -(f.debito - f.credito)
             ELSE 0 END)                               AS utilidad
FROM marts.fact_movimiento_contable f
JOIN marts.dim_fecha d ON d.fecha_key = f.fecha_key
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.empresa_id IS NOT NULL
  AND c.grupo_codigo IN ('41','42','51','52','53','61')
GROUP BY f.empresa_id, d.periodo_aaaamm, d.anio, d.mes, 5;

CREATE UNIQUE INDEX ux_mv_contab_tercero_mes
    ON marts.mv_contab_tercero_mes (empresa_id, periodo_aaaamm, tercero_id);
CREATE INDEX ix_mv_contab_tercero_mes_ing
    ON marts.mv_contab_tercero_mes (empresa_id, anio, ingresos DESC);
CREATE INDEX ix_mv_contab_tercero_mes_gas
    ON marts.mv_contab_tercero_mes (empresa_id, anio, gastos DESC);

COMMENT ON MATERIALIZED VIEW marts.mv_contab_tercero_mes IS
  'Ingresos/costos/gastos por tercero y mes, PIVOTADO a columnas (137.612 '
  'terceros: con el concepto en filas serían ~1,7 M). El top-N se resuelve con '
  'LIMIT en SQL. ⚠ En las líneas de gasto el tercero es el PROVEEDOR.';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_contab_centro_mes — panel «Comportamiento Centro de costo».
-- Se expone `plan` porque los 62 centros mezclan centros reales (BODEGA, Bodega
-- Yumbo) con proyectos de exportación ([EXPO] EPO-02-2026), y sin poder filtrar
-- por plan el panel es ilegible.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_contab_centro_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_contab_centro_mes AS
SELECT
    f.empresa_id,
    d.periodo_aaaamm,
    d.anio,
    d.mes,
    COALESCE(f.centro_costo_id, -1)                    AS centro_costo_id,
    COALESCE(NULLIF(btrim(cc.nombre), ''), '(sin centro)') AS centro_nombre,
    COALESCE(NULLIF(btrim(cc.plan), ''),   '(sin plan)')   AS plan,
    SUM(CASE WHEN c.grupo_codigo = '41' THEN -(f.debito - f.credito) ELSE 0 END) AS ingresos,
    SUM(CASE WHEN c.grupo_codigo = '61' THEN  (f.debito - f.credito) ELSE 0 END) AS costos,
    SUM(CASE WHEN c.grupo_codigo IN ('51','52') THEN (f.debito - f.credito) ELSE 0 END) AS gastos,
    SUM(CASE WHEN c.grupo_codigo = '53' THEN  (f.debito - f.credito) ELSE 0 END) AS gastos_no_op
FROM marts.fact_movimiento_contable f
JOIN marts.dim_fecha d ON d.fecha_key = f.fecha_key
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
LEFT JOIN marts.dim_centro_costo cc ON cc.centro_costo_id = f.centro_costo_id
WHERE f.empresa_id IS NOT NULL
  AND c.grupo_codigo IN ('41','42','51','52','53','61')
GROUP BY f.empresa_id, d.periodo_aaaamm, d.anio, d.mes, 5, 6, 7;

CREATE UNIQUE INDEX ux_mv_contab_centro_mes
    ON marts.mv_contab_centro_mes (empresa_id, periodo_aaaamm, centro_costo_id);

COMMENT ON MATERIALIZED VIEW marts.mv_contab_centro_mes IS
  'Ingresos/costos/gastos por centro de costo y mes. Expone `plan` porque los '
  'centros mezclan centros reales con proyectos [EXPO].';


-- ════════════════════════════════════════════════════════════════════════════
-- mv_contab_canal_mes — panel «Comportamiento canales» (el 4.º de la página).
-- Sale de `fact.categoria` (cliente, normalizada con map_categoria en el cierre
-- del ETL) y de `fact.canal` (analítico plan 21).
--
-- ⚠ En los ticks LIGEROS del ETL (:15/:30/:45) las líneas nuevas llegan todavía
-- sin `categoria`, así que aparece un bucket '(sin categoria)' que se vacía al
-- cierre de cada hora. Es otro motivo para refrescar las MV contables solo en el
-- tick :00 (ver refrescar_mv_dashboards.py).
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_contab_canal_mes CASCADE;

CREATE MATERIALIZED VIEW marts.mv_contab_canal_mes AS
SELECT
    f.empresa_id,
    d.periodo_aaaamm,
    d.anio,
    d.mes,
    COALESCE(NULLIF(btrim(f.categoria), ''), '(sin categoria)') AS categoria,
    COALESCE(NULLIF(btrim(f.canal), ''),     '(sin canal)')     AS canal,
    SUM(CASE WHEN c.grupo_codigo = '41' THEN -(f.debito - f.credito) ELSE 0 END) AS ingresos,
    SUM(CASE WHEN c.grupo_codigo = '61' THEN  (f.debito - f.credito) ELSE 0 END) AS costos,
    SUM(CASE WHEN c.grupo_codigo IN ('51','52') THEN (f.debito - f.credito) ELSE 0 END) AS gastos
FROM marts.fact_movimiento_contable f
JOIN marts.dim_fecha d ON d.fecha_key = f.fecha_key
JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
WHERE f.empresa_id IS NOT NULL
  AND c.grupo_codigo IN ('41','51','52','61')
GROUP BY f.empresa_id, d.periodo_aaaamm, d.anio, d.mes, 5, 6;

CREATE UNIQUE INDEX ux_mv_contab_canal_mes
    ON marts.mv_contab_canal_mes (empresa_id, periodo_aaaamm, categoria, canal);

COMMENT ON MATERIALIZED VIEW marts.mv_contab_canal_mes IS
  'Ingresos/costos/gastos por categoría de cliente × canal analítico y mes. '
  'Alimenta el panel de canales de Comportamientos.';


-- ════════════════════════════════════════════════════════════════════════════
-- v_lk_cuenta — lookup para `intranet_ro`.
-- Espejo de v_lk_tercero: se expone un contrato estable y recortado en vez de
-- conceder SELECT sobre dim_cuenta (que además arrastraría los internals de la
-- canonicalización). El hecho contable NUNCA se concede.
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW marts.v_lk_cuenta AS
SELECT c.cuenta_id,
       c.codigo,
       c.nombre,
       c.cuenta_etiqueta,
       c.clase_codigo,
       c.grupo_codigo,
       c.cuenta_codigo,
       c.clase_nombre,
       c.grupo_nombre,
       c.cuenta_nombre,
       c.concepto_contable,
       c.orden_informe,
       c.categoria_gasto,
       c.bal_nivel1,      c.orden_bal_nivel1,
       c.clasif_liquidez, c.orden_liquidez,
       c.bal_nivel2,      c.orden_bal_nivel2,
       c.flujo_renglon,   c.flujo_actividad, c.orden_flujo_actividad,
       c.es_dya, c.es_dya_linea
FROM marts.v_dim_cuenta_bi c;

COMMENT ON VIEW marts.v_lk_cuenta IS
  'Lookup del PUC para los tableros, con la clasificación derivada. No expone '
  'cuenta_canonica_id ni el resto de internals.';


-- ════════════════════════════════════════════════════════════════════════════
-- Siembra de la bitácora.
-- ════════════════════════════════════════════════════════════════════════════
INSERT INTO marts.bi_mv_refresh (mv_name, refreshed_at, filas, ok)
SELECT m.mv, now(), NULL, TRUE
FROM (VALUES ('mv_contab_cuenta_mes'), ('mv_balance_mes'), ('mv_pyg_mes'),
             ('mv_flujo_mes'), ('mv_contab_tercero_mes'),
             ('mv_contab_centro_mes'), ('mv_contab_canal_mes')) AS m(mv)
ON CONFLICT (mv_name) DO UPDATE SET refreshed_at = now(), ok = TRUE, error = NULL;
