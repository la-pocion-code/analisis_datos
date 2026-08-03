-- ============================================================================
-- Hoja de CARTERA — saldos pendientes, mora y responsable de cobro
-- Archivo: sql/marts/30_cartera_dashboards.sql
--
-- Expone `marts.v_cartera` para la hoja de la intranet: Resumen · Por
-- responsable · Detalle · Fuera de credito.
--
-- ⚠ Tras ejecutarlo hay que RE-EJECUTAR 24_rol_intranet.sql (los GRANT se pierden
-- al recrear una MV).
--
-- ── LO QUE SE MIDIO ANTES DE ESCRIBIR ESTO (2026-08-01, solo lectura) ────────
--
--   v_cartera ................... 6.018 filas · 595 terceros · 836 documentos
--                                 saldo total 9.135.510.346
--                                 fecha contable 2023-12-31 → 2026-08-01
--                                 vencimientos   2024-08-16 → 2026-11-03
--
--   por tipo_movimiento:
--     entry ..................... 5.738 filas · solo 142 con vencimiento
--     out_invoice ...............   240 filas · 240 con vencimiento (100 %)
--     out_refund ................    40 filas ·  40 con vencimiento (100 %)
--
--   saldo positivo .............. 3.073 filas · 14.057.543.480
--   saldo negativo .............. 2.945 filas · −4.922.033.134 · 475 terceros
--     de los negativos, 2.905 son `entry` y 40 son notas credito
--
--   por empresa: HFA 4.011 filas / 639.925.131 · PCN 2.007 / 8.495.585.215
--   estado_pago: not_paid 5.933 · partial 72 · paid 12 · reversed 1
--
--   responsables en bi_cartera (volcado del 2026-07-23):
--     DIANA RIOS 130 filas / 61 clientes · DANIELA DURAN 76 / 5
--     SHELLSY VELASCO 16 / 3
--
-- ── LAS OCHO TRAMPAS DE ESTE DATASET ────────────────────────────────────────
--
-- 1. ⚠⚠ **SOLO LAS FACTURAS ADMITEN MORA.** `fecha_vencimiento_key` sale de
--    `account.move.line.date_maturity`, y Odoo solo la calcula cuando el
--    documento tiene termino de pago — o sea, cuando es una factura. Los 5.738
--    `entry` (recibos de caja, reclasificaciones, ajustes contra la 13xx) no lo
--    tienen: 142 de 5.738. Construir el aging sobre `v_cartera` en crudo deja el
--    95 % de las filas en «sin clasificar» y los rangos dejan de sumar el total.
--    Por eso la MV publica `admite_mora`, y la hoja separa los dos mundos.
--    El pipeline viejo lo resolvia a lo bruto con `Numero.str.startswith('F')`,
--    que ademas se comia los anticipos sin decirlo (trampa 4).
--
-- 2. ⚠⚠ **`tipo_cliente` NO ES `categoria`, y la diferencia cambia el resultado.**
--    `v_cartera` expone el valor CRUDO de Odoo (`account.move.partner_type_id`);
--    `fact.categoria` ya esta normalizada por `map_categoria`
--    (`FARMACIAS`→`FARMACIA`, `Catalogo`→`CATALOGO`, `EXTERIOR`→`EXPORTACION` en
--    ventas) y **nunca es NULL** (cae a `CALL CENTER`). El pipeline de cartera
--    siempre filtro por el crudo, y esta MV hace lo mismo. Mezclarlos da otro
--    conjunto sin dar ningun error.
--
-- 3. ⚠ **NO TODO LO QUE HAY EN CARTERA ES CARTERA DE CREDITO.** Los 10 tipos que
--    el negocio cobra estan en `bi_cartera_tipo_credito`. Fuera quedan, medido:
--      CLIENTE ......... 482 filas / 2.260.217.120  (venta de contado)
--      Proveedores ......  18 filas / 1.020.322.669
--      (sin tipo) ..... 4.978 filas / −3.224.517.242
--      Empleado .........   6 filas / −3.241
--      «Wrote Judge.me web review» 4 filas / 0   ← basura literal de Odoo
--    No se descartan: la hoja los presenta aparte. Descartarlos en silencio es
--    lo que hacia el pipeline viejo, y son 3.280 MM de saldo real.
--
-- 4. ⚠ **LOS SALDOS NEGATIVOS SON ANTICIPOS Y VAN APARTE, SIN NETEAR** (decision
--    de William, 2026-08-01). Un `saldo_pendiente < 0` en una linea
--    `asset_receivable` es saldo a favor del cliente: anticipo recibido, nota
--    credito sin aplicar o pago en exceso. Son 2.945 filas por −4.922 MM. No se
--    restan del pendiente por cobrar: **un anticipo no cancela una factura
--    vencida**, y netearlos esconderia mora real. La MV los marca con
--    `es_anticipo` y la hoja les da su propio bloque.
--    ⚠ El grueso no es de clientes: 2 filas de «SALDOS INICIALES» valen
--    −2.861.536.788 y 4.721 filas no traen ni tercero (−1.061.763.278).
--
-- 5. ⚠ **EL AGING NO SE MATERIALIZA, Y ES DELIBERADO.** `dias_atraso` depende de
--    HOY. Si se calculara dentro de la MV quedaria congelado en el instante del
--    refresco y, cruzando la medianoche, la hoja mostraria la mora de ayer sin
--    avisar. La MV publica los hechos estables —`fecha_vencimiento` y
--    `dias_credito`— y el corte por rangos lo hace la intranet contra
--    `CURRENT_DATE`, en un solo fragmento compartido.
--
-- 6. ⚠ **EL RESPONSABLE DE COBRO NO EXISTE EN EL ALMACEN.** El `.pbix` repunto la
--    tabla `Cartera` a `v_cartera` y perdio la columna por el camino: hoy es
--    literalmente `Table.AddColumn(ORD, "RESPONSABLE", each null)`. El dato vive
--    en el Excel `base_cartera.xlsx` (hoja `Responsables`) y en el volcado
--    `bi_cartera`. Se modela aqui, en `bi_cartera_responsable`.
--
-- 7. ⚠ **`v_cartera` EXPONE EL NIT** (`identificacion`), y por eso esta negada a
--    `intranet_ro`. Esta MV **no lo propaga**. No añadirlo: la hoja no lo
--    necesita y seria dato personal saliendo a una app web.
--
-- 8. ⚠⚠ **UNA NOTA DEBITO NACE VENCIDA Y SE DISFRAZA DE FACTURA** (2026-08-03).
--    Odoo emite las ND con `move_type = 'out_invoice'`, identico a una factura de
--    venta: lo UNICO que las distingue es el diario
--    (`dim_diario.codigo IN ('NDY','NDEXP')` — por CODIGO, no por nombre; misma
--    regla que ya usan 14_ventas.sql y 25_nd_factura.sql). Ademas no llevan
--    termino de pago, asi que `date_maturity = date`: cero dias de credito y
--    entran directas en «61-90» o peor.
--    Medido el 2026-08-03: `NDY4` (NOVAVENTA, 55.569.759, «FE7281, Ajuste por
--    precio») era el **48 % de la mora de DIANA RIOS** y el **98 % de su rango
--    61-90**. El informe viejo no la mostraba, pero por accidente: su filtro era
--    `Numero.startswith('F')` (trampa 1), que se come todo lo que empiece por N.
--    ⚠ **Si son deuda real** — el cliente debe ese cargo. Por eso no se
--    descartan: `v_cartera` publica `es_nota_debito` y `documento_origen`, la MV
--    los propaga y la hoja les da su propio bloque, como a los anticipos.
--    ⚠ `v_cartera` esta definida DOS VECES (06_cartera_en_hecho.sql y
--    07_widen_text.sql, que corre despues y gana). Las dos tienen que coincidir.
--
-- ⚠ De paso, dos rarezas que NO son errores:
--    · 12 lineas con `estado_pago = 'paid'` y saldo distinto de cero: son
--      facturas de exportacion (FYEX…) con residuos de redondeo de divisa, el
--      mayor de 323.844. Se conservan; filtrarlas por estado dejaria fuera saldo
--      real.
--    · Los 142 `entry` CON vencimiento suman −26.394.723 en 96 terceros: son
--      anticipos con fecha pactada. Entran en el bloque de anticipos, no en el
--      aging.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- bi_cartera_tipo_credito — SEMILLA: que tipos de cliente son cartera de credito.
--
-- La lista vivia en una variable de `ejecuciones_anilista.ipynb` que alguien
-- tenia que editar y volver a ejecutar a mano. Va en una tabla para que se pueda
-- cambiar sin desplegar codigo.
--
-- ⚠ La CAJA importa: son los valores crudos de Odoo, y ahi conviven
-- `Surticosmeticos`, `Catalogo` y `Distribuidor` en CamelCase con el resto en
-- mayusculas. Normalizarlos aqui romperia el empalme.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_cartera_tipo_credito (
    tipo_cliente TEXT    PRIMARY KEY,
    es_credito   BOOLEAN NOT NULL DEFAULT TRUE,
    nota         TEXT
);

COMMENT ON TABLE marts.bi_cartera_tipo_credito IS
  'Que valores de dim_tercero.tipo_cliente son cartera de credito. Los que no '
  'estan aqui (o estan con es_credito=false) se presentan APARTE, no se descartan: '
  'son 3.280 MM de saldo real. Caja exacta de Odoo, no normalizar.';

INSERT INTO marts.bi_cartera_tipo_credito (tipo_cliente, es_credito, nota) VALUES
    ('MAYORISTA NV',    TRUE,  NULL),
    ('FARMACIAS',       TRUE,  'Ojo: en ventas se normaliza a FARMACIA, singular.'),
    ('EXTERIOR',        TRUE,  'Exportacion. Dias de credito pactados aparte.'),
    ('Surticosmeticos', TRUE,  NULL),
    ('COOPIDROGAS',     TRUE,  NULL),
    ('Catalogo',        TRUE,  'Novaventa. En ventas se normaliza a CATALOGO.'),
    ('ESPECIALIZADAS',  TRUE,  NULL),
    ('Distribuidor',    TRUE,  NULL),
    ('KRIKA',           TRUE,  'Lucego. Hoy en saldo negativo (−30.922.933).'),
    ('HOLE COSMETICS',  TRUE,  NULL),
    -- Los de abajo se registran para poder EXPLICARLOS en la hoja, no para
    -- incluirlos. Sin fila tambien quedarian fuera, pero sin motivo a la vista.
    ('CLIENTE',         FALSE, 'Venta de contado. 2.260 MM, el mayor bloque fuera de credito.'),
    ('Proveedores',     FALSE, 'No es venta: son saldos con proveedores.'),
    ('Empleado',        FALSE, 'Ventas a empleados.'),
    ('Wrote Judge.me web review', FALSE,
     'Basura literal de Odoo: una opinion de la web quedo grabada como tipo de cliente.'),
    -- Centinela que pone la MV cuando el tercero no tiene `partner_type_id`. Se
    -- registra para poder EXPLICARLO: son 4.982 filas por −3.223 MM, y el grueso
    -- no son clientes (2 filas de «SALDOS INICIALES» valen −2.861 MM y 4.721 no
    -- traen ni nombre de tercero). Sin esta fila la hoja lo mostraria sin motivo.
    ('(sin tipo)',      FALSE,
     'El tercero no tiene tipo en Odoo. Casi todo son asientos contables sin cliente, '
     'no deuda: SALDOS INICIALES y lineas sin tercero.')
ON CONFLICT (tipo_cliente) DO UPDATE
    SET es_credito = EXCLUDED.es_credito, nota = EXCLUDED.nota;


-- ════════════════════════════════════════════════════════════════════════════
-- bi_cartera_responsable — SEMILLA: quien responde por el cobro.
--
-- Reemplaza el Excel `base_cartera.xlsx` (hoja `Responsables`), que se cruzaba en
-- pandas y no llegaba a ninguna tabla.
--
-- ⚠ Se simplifica la regla del pipeline viejo, que asignaba por TIPO CLIENTE
-- salvo cuando un tipo aparecia repetido en la hoja, y entonces desempataba por
-- CLIENTE. Esa condicion —«repetido en la hoja»— depende del contenido del
-- Excel, no del negocio, asi que el mismo cliente podia cambiar de dueño porque
-- alguien añadio una fila en otro sitio. Aqui la precedencia es explicita:
--
--     1. fila con `tercero_id` = el id del tercero    (la mas especifica)
--     2. fila con `cliente` = el nombre del tercero
--     3. fila con `cliente IS NULL` y su `tipo_cliente`
--     4. la fila con `es_default`
--     5. si no hay ninguna, '(sin responsable)'
--
-- ⚠⚠ `tercero_id` SE AÑADIO EL 2026-08-03 Y ES LA LLAVE BUENA. El cruce por
-- razon social se cae en silencio, y se midio cayendose DOS veces:
--
--   · la hoja dice `FARMATODO COLOMBIA SA` y quien factura en Odoo es
--     `FARMATODO COLOMBIA S.A`, con puntos. Un punto de diferencia dejaba
--     853.168.462 pesos —el 12,7 % de la cartera— SIN RESPONSABLE, y por tanto
--     fuera del informe de todo el mundo. Existe ademas un duplicado del mismo
--     cliente sin ventas, que es el que casa con el nombre de la hoja: el que
--     factura es el `tercero_id` 268476.
--   · el notebook cablea `C&L SOLUTIONS LLC.` y Odoo dice `C&L SOLUTIONS LLC`,
--     asi que ese cliente NO estaba recibiendo sus 120 dias de credito pactados.
--
-- El cruce por texto se conserva como respaldo —una fila sin `tercero_id` sigue
-- funcionando— pero deja de ser el camino principal. Al re-sembrar desde el
-- Excel, rellenar `TERCERO_ID`.
--
-- Arranca con lo que hay en `bi_cartera`, que es el ultimo volcado real del
-- pipeline (2026-07-23): DIANA RIOS, DANIELA DURAN y SHELLSY VELASCO. ⚠ Ese
-- volcado NO es la fuente de verdad y se quedo corto: le faltan ANDRES VASQUEZ,
-- MIRIAM BURGOS y MARIA PAULA, y le sobra DANIELA DURAN. La fuente de verdad es
-- la hoja `Responsables` de `base_cartera.xlsx`, y quien la trae es
-- `cargar_cartera_responsables.py`.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS marts.bi_cartera_responsable (
    id           BIGSERIAL PRIMARY KEY,
    -- Cualquiera de los tres manda; varios a la vez acotan mas.
    tercero_id   INTEGER,
    tipo_cliente TEXT,
    cliente      TEXT,
    responsable  TEXT    NOT NULL,
    ubicacion    TEXT,
    es_default   BOOLEAN NOT NULL DEFAULT FALSE,
    nota         TEXT
);

-- Migracion para las bases que ya tenian la tabla sin `tercero_id`. Va aqui y no
-- en un fichero aparte para no tener que duplicar la definicion de la MV, que es
-- de 100 lineas y se desincronizaria al primer cambio.
ALTER TABLE marts.bi_cartera_responsable
    ADD COLUMN IF NOT EXISTS tercero_id INTEGER;

-- El CHECK cambia (ahora `tercero_id` tambien vale como llave), asi que se
-- reemplaza. DROP IF EXISTS + ADD es idempotente como pareja.
ALTER TABLE marts.bi_cartera_responsable
    DROP CONSTRAINT IF EXISTS bi_cartera_responsable_algo_que_casar;
ALTER TABLE marts.bi_cartera_responsable
    ADD  CONSTRAINT bi_cartera_responsable_algo_que_casar
    CHECK (es_default OR tercero_id IS NOT NULL
           OR tipo_cliente IS NOT NULL OR cliente IS NOT NULL);

-- Un tercero no puede tener dos dueños, ni un cliente, ni un tipo. Y solo puede
-- haber un default: sin esto, `LIMIT 1` elegiria uno al azar en cada refresco.
CREATE UNIQUE INDEX IF NOT EXISTS ux_bi_cartera_resp_tercero
    ON marts.bi_cartera_responsable (tercero_id) WHERE tercero_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_bi_cartera_resp_cliente
    ON marts.bi_cartera_responsable (cliente) WHERE cliente IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_bi_cartera_resp_tipo
    ON marts.bi_cartera_responsable (tipo_cliente)
    WHERE cliente IS NULL AND tipo_cliente IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_bi_cartera_resp_default
    ON marts.bi_cartera_responsable ((TRUE)) WHERE es_default;

COMMENT ON TABLE marts.bi_cartera_responsable IS
  'Responsable de cobro. Precedencia: tercero_id > cliente > tipo_cliente > '
  'default. El cruce por tercero_id es el bueno: por razon social se cayo dos '
  'veces (FARMATODO COLOMBIA S.A y C&L SOLUTIONS LLC). Se mantiene desde la hoja '
  'Responsables de base_cartera.xlsx con cargar_cartera_responsables.py.';

-- ════════════════════════════════════════════════════════════════════════════
-- Arranque desde el ultimo volcado del pipeline viejo — SOLO SI LA TABLA ESTA
-- VACIA.
--
-- ⚠⚠ EL `ON CONFLICT DO NOTHING` NO BASTABA, Y ES UNA TRAMPA CARA (2026-08-03).
-- Parecia idempotente: «si ya hay fila para ese cliente, no se toca». Pero los
-- indices unicos son PARCIALES y por NIVEL, asi que una fila del volcado a nivel
-- CLIENTE nunca colisiona con una fila del Excel a nivel TIPO DE CLIENTE — se
-- inserta al lado. Y como la precedencia de la MV es
-- `tercero_id > cliente > tipo_cliente > default`, **la fila vieja GANA**.
--
-- Medido: re-ejecutar este fichero (que la propia cabecera manda hacer cada vez
-- que se toca la MV) revirtio los responsables al estado del 2026-07-23.
-- `DAVID SANCHEZ` cayo de 1.304 MM a 20.468 pesos y reaparecio
-- `SHELLSY VELASCO` con 1.213 MM, que ya no trabaja esa cartera. En verde, sin
-- un solo error, y `check_marts §7r` no lo caza porque el 100 % de la cartera
-- SIGUE teniendo un responsable: solo que el equivocado.
--
-- La fuente de verdad es la hoja `Responsables` de `base_cartera.xlsx`, y quien
-- la carga es `cargar_cartera_responsables.py`. Este INSERT es solo el arranque
-- de una base nueva.
-- ════════════════════════════════════════════════════════════════════════════
INSERT INTO marts.bi_cartera_responsable (cliente, responsable, nota)
SELECT DISTINCT
       btrim(b.nombre_del_contacto_a_mostrar_en_la_factura),
       btrim(b.responsable),
       'Importado de bi_cartera (volcado del pipeline viejo, 2026-07-23).'
FROM marts.bi_cartera b
WHERE COALESCE(btrim(b.responsable), '') <> ''
  AND COALESCE(btrim(b.nombre_del_contacto_a_mostrar_en_la_factura), '') <> ''
  AND NOT EXISTS (SELECT 1 FROM marts.bi_cartera_responsable)
ON CONFLICT DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════════
-- mv_cartera_saldo — una fila por LINEA de cuentas por cobrar con saldo.
--
-- Grano: `linea_id` (`account.move.line.id`). No se agrega por documento: una
-- factura puede tener varias lineas de CxC con vencimientos distintos, y
-- colapsarlas obligaria a elegir uno. El agregado por documento lo hace la hoja.
--
-- Se resuelven aqui, UNA vez, las tres cosas que la intranet repetiria en cada
-- panel: `es_credito`, `responsable` y el paso de las llaves AAAAMMDD a DATE.
--
-- ⚠ Sin `identificacion` (trampa 7). ⚠ Sin `dias_atraso` ni `rango_mora`
-- (trampa 5): dependen de hoy y se calculan en la consulta.
-- ════════════════════════════════════════════════════════════════════════════
DROP MATERIALIZED VIEW IF EXISTS marts.mv_cartera_saldo CASCADE;

CREATE MATERIALIZED VIEW marts.mv_cartera_saldo AS
WITH base AS (
    SELECT
        v.linea_id,
        v.factura_id,
        COALESCE(NULLIF(btrim(v.numero), ''), '(sin numero)')         AS numero,
        v.tipo_movimiento,
        COALESCE(v.estado_pago, '(sin estado)')                       AS estado_pago,
        -- Centinelas en vez de NULL: en un indice unico los nulos se consideran
        -- distintos entre si y el UNIQUE dejaria de proteger nada.
        COALESCE(v.tercero_id, -1)                                    AS tercero_id,
        COALESCE(NULLIF(btrim(v.tercero_nombre), ''), '(sin tercero)') AS tercero,
        COALESCE(NULLIF(btrim(v.tipo_cliente), ''), '(sin tipo)')     AS tipo_cliente,
        COALESCE(v.empresa_id, -1)                                    AS empresa_id,
        COALESCE(NULLIF(btrim(v.empresa_nombre), ''), '(sin empresa)') AS empresa,
        v.fecha_key,
        v.fecha_vencimiento_key,
        v.saldo_pendiente,
        v.es_nota_debito,
        v.documento_origen
    FROM marts.v_cartera v
),
fechas AS (
    SELECT b.*,
           -- Las llaves son AAAAMMDD enteros; 0 y NULL significan «no hay fecha».
           CASE WHEN COALESCE(b.fecha_key, 0) > 0
                THEN to_date(b.fecha_key::TEXT, 'YYYYMMDD') END       AS fecha,
           CASE WHEN COALESCE(b.fecha_vencimiento_key, 0) > 0
                THEN to_date(b.fecha_vencimiento_key::TEXT, 'YYYYMMDD') END
                                                                      AS fecha_vencimiento
    FROM base b
)
SELECT
    f.linea_id,
    f.factura_id,
    f.numero,
    f.tipo_movimiento,
    f.estado_pago,
    f.tercero_id,
    f.tercero,
    f.tipo_cliente,
    f.empresa_id,
    f.empresa,
    f.fecha,
    f.fecha_vencimiento,
    -- Periodo de la fecha CONTABLE (v_cartera no expone la de factura).
    (EXTRACT(YEAR FROM f.fecha) * 100 + EXTRACT(MONTH FROM f.fecha))::INTEGER
                                                                      AS periodo_aaaamm,
    f.saldo_pendiente,

    -- ── Banderas que deciden en que bloque de la hoja cae cada fila ──
    -- Solo una factura con vencimiento admite mora (trampa 1).
    (f.tipo_movimiento IN ('out_invoice', 'out_refund')
     AND f.fecha_vencimiento IS NOT NULL)                             AS admite_mora,
    -- Saldo a favor del cliente (trampa 4). Va aparte, nunca neteado.
    (f.saldo_pendiente < 0)                                           AS es_anticipo,
    COALESCE(tc.es_credito, FALSE)                                    AS es_credito,
    tc.nota                                                           AS nota_tipo,
    -- ⚠ NOTA DÉBITO (trampa 8). Es `out_invoice` igual que una factura y trae
    -- `date_maturity`, asi que `admite_mora` la deja pasar y NACE VENCIDA: Odoo
    -- no le pone termino de pago, o sea `vencimiento = fecha`, cero dias de
    -- credito. Medido el 2026-08-03: `NDY4` (NOVAVENTA, 55,6 M, «FE7281, Ajuste
    -- por precio») era el 48 % de la mora de DIANA RIOS y el 98 % de su rango
    -- 61-90 dias. El informe viejo nunca las mostro, pero por accidente: su
    -- filtro era `Numero.startswith('F')`.
    -- La MV solo lo MARCA; quien decide es la hoja (`SOLO_CARTERA`).
    COALESCE(f.es_nota_debito, FALSE)                                 AS es_nota_debito,
    -- El documento al que apunta la ND, para no dejar al cobrador adivinando.
    -- Solo tiene sentido en una ND: en el resto es el `ref` del asiento.
    CASE WHEN f.es_nota_debito THEN f.documento_origen END            AS documento_origen,

    -- Estatico: no depende de hoy, al contrario que los dias de atraso.
    CASE WHEN f.fecha_vencimiento IS NOT NULL AND f.fecha IS NOT NULL
         THEN (f.fecha_vencimiento - f.fecha) END                     AS dias_credito,

    -- Precedencia tercero_id > cliente > tipo > default (ver la semilla).
    COALESCE(ri.responsable, rc.responsable, rt.responsable, rd.responsable,
             '(sin responsable)')                                     AS responsable,
    COALESCE(ri.ubicacion, rc.ubicacion, rt.ubicacion)                AS ubicacion
FROM fechas f
LEFT JOIN marts.bi_cartera_tipo_credito tc ON tc.tipo_cliente = f.tipo_cliente
-- Por id primero: es la unica llave que sobrevive a que renombren el cliente en
-- Odoo, y renombrarlo ya dejo 853 MM sin dueño una vez.
LEFT JOIN marts.bi_cartera_responsable  ri ON ri.tercero_id   = f.tercero_id
LEFT JOIN marts.bi_cartera_responsable  rc ON rc.cliente      = f.tercero
LEFT JOIN marts.bi_cartera_responsable  rt ON rt.cliente IS NULL
                                          AND rt.tipo_cliente = f.tipo_cliente
LEFT JOIN LATERAL (
    SELECT responsable FROM marts.bi_cartera_responsable
    WHERE es_default LIMIT 1
) rd ON TRUE;

-- `linea_id` ya es la PK del hecho, asi que basta para el UNIQUE que
-- `REFRESH ... CONCURRENTLY` exige.
CREATE UNIQUE INDEX ux_mv_cartera_saldo ON marts.mv_cartera_saldo (linea_id);

-- Los filtros de la hoja. `es_nota_debito` entra en el primero porque las cuatro
-- clausulas de la cola de cobro viajan siempre juntas (`SOLO_CARTERA`).
CREATE INDEX ix_mv_cartera_saldo_mora  ON marts.mv_cartera_saldo
    (admite_mora, es_credito, es_nota_debito);
CREATE INDEX ix_mv_cartera_saldo_venc  ON marts.mv_cartera_saldo (fecha_vencimiento);
CREATE INDEX ix_mv_cartera_saldo_resp  ON marts.mv_cartera_saldo (responsable);

COMMENT ON MATERIALIZED VIEW marts.mv_cartera_saldo IS
  'Cartera por linea de CxC. NO trae dias de atraso ni rango de mora: dependen de '
  'HOY y congelarlos en el refresco mostraria la mora de ayer. NO trae el NIT. '
  'Solo admite_mora=true entra en el aging (los entry no tienen vencimiento); '
  'es_anticipo=true son saldos a favor y van aparte, sin netear; '
  'es_nota_debito=true son cargos extra que nacen vencidos (Odoo no les pone '
  'termino de pago) y van en su propio bloque, no en la cola de cobro.';


-- ════════════════════════════════════════════════════════════════════════════
-- Registro en la bitacora de refrescos, para que la intranet pueda invalidar su
-- cache y decir «datos actualizados hace X» desde el primer momento.
-- ════════════════════════════════════════════════════════════════════════════
INSERT INTO marts.bi_mv_refresh (mv_name, refreshed_at, filas, ok)
SELECT 'mv_cartera_saldo', now(), NULL, TRUE
ON CONFLICT (mv_name) DO UPDATE
    SET refreshed_at = now(), ok = TRUE, error = NULL;
