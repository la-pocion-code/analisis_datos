# CLAUDE.md — Proyecto BI La Poción (analisis_datos)

Guía para Claude Code. Repo de scripts ETL/BI del analista de datos de La Poción.
Documentación extendida y roadmap del DW: `docs/ARQUITECTURA_DW.md`.

## Qué es este repo
- Cron en **Railway** que carga el **Data Warehouse** (`Odoo → PostgreSQL marts`) **cada 15 min**.
- Más scripts de BI manual (Excel, Google Drive, correo) en `classes/` y notebooks.
- ⭐ **DIRECCIÓN DEL PROYECTO: los tableros pasan de Power BI a la INTRANET** (app interna de la
  compañía) presentados como **HTML dinámico** (ECharts, consultando la BD en vivo). Power BI es
  fuente **transitoria**, no destino: lo nuevo se hace para la intranet y **la lógica de negocio baja
  al SQL** (lo que era medida DAX o paso de Power Query pasa a vistas/MV/columnas del hecho, porque la
  intranet solo hace `SELECT`). Ver `docs/dashboards_intranet.md`.
- Idioma del proyecto y de la comunicación: **español**.

## Componente principal: el cron del DW
- Entrypoint: **`run_dw.py`**. Disparado por Railway Cron (`railway.toml` → **`*/15 * * * *`**).
  Mismo comando en `Procfile` (worker: `python run_dw.py`).
- **Reparto LIGERO/COMPLETO** (el coste de una corrida es casi todo FIJO, no proporcional al delta):
  - tick **:00** → corrida **COMPLETA**: catálogos + dims + kits + nombre comercial + hecho + **todos
    los pasos de cierre** (reversos, puentes NC/ND, categoría, PUC) + refresco de MV.
  - ticks **:15/:30/:45** → **ligera**: dimensiones por `write_date` + `cargar_hecho` + MV
    (`etl_dw_marts.main(..., cierre=False)`, o `--sin-cierre` a mano).
  - ⚠ En los ticks ligeros las líneas nuevas quedan **sin `categoria`, sin `es_reverso` y sin puente
    NC/ND** hasta el cierre de la hora. Es el precio de la frescura.
- **rebuild** del año actual los días 3 y 24 a las 03h, **solo en el tick :00** (`MINUTO_CIERRE`).
  ⚠ Sin esa guarda, `hour==3` se cumplía en :00/:15/:30/:45 → 4 rebuilds solapados, cada uno con un
  DELETE del año. La hora es la del contenedor = **UTC** (≈22:00 del día anterior en Colombia).
- **Advisory lock** (`pg_try_advisory_lock`, clave `8152026`) en `run_dw.py`: si la corrida anterior
  sigue viva, el tick se **omite** y sale con código 0. Es lo que hace seguro el `*/15`.
- El sync antiguo a `raw.odoo_apuntes` (`etl_odoo_incremental.py`) quedó **archivado**
  (`archivado/`, ya no corre); el DW lee de Odoo directo, no de `raw`. `raw.odoo_apuntes` sigue
  existiendo para el BI legacy pero ya no se actualiza por cron.

## Archivos clave
- `run_dw.py` — **entrypoint del cron** (dispatcher DW: ligera cada 15 min, completa en :00, rebuild 3/24).
- `etl_dw_marts.py` — ETL del DW (ver sección Data Warehouse).
- `cargar_mapeos.py` — carga los mapeos NO-Odoo de ventas (zona/cliente_padre/categoría) de Drive a
  `marts.map_*`. A demanda (ver sección Data Warehouse).
- `cargar_bi_datasets.py` — sube a `marts.bi_*` los datasets que el BI de Power BI leía de archivos
  LOCALES (Drive → PG). A demanda (ver sección "BI Power BI").
- `cargar_cuentas_clave.py` — reproduce en `marts.bi_cuentas_clave_ventas/bi_inventario_cclave/
  bi_tiendas_cclave` las tablas de CUENTAS CLAVE (ventas/inventarios por retailer + países) desde Drive.
- `refrescar_mv_dashboards.py` — refresca las MV que consumen los **dashboards de la intranet**
  (lo llama `run_dw.py` al final de cada corrida). Ver sección "Dashboards de la INTRANET".
- `classes/db_loader.py` — `DBLoader`: conexión PG, auto-DDL, UPSERT, carga incremental.
- `classes/drive_loader.py` — `DriveLoader`: lee Excel/CSV de Google Drive.
- `classes/send_mail.py` — `MailSender`: correos SMTP con adjuntos.
- `classes/clase_reportes_new.py` — `ReportClassNew` (~2500 líneas): motor BI manual.
- `archivado/` — código legacy (incl. `etl_odoo_incremental.py`, el antiguo sync raw ya retirado
  del cron, y `etl_odoo_historico.py`, que solo dropea tablas).

## BI Power BI — modelo `DASHBOARD POCION` (desconexión de archivos locales)
⚠ **TRANSITORIO**: se está reemplazando por los dashboards de la **intranet** (HTML dinámico, ver la
sección "Dashboards de la INTRANET" y `docs/dashboards_intranet.md`). Se mantiene mientras dure la
migración; **no se amplía**. Lo que aquí sea DAX/Power Query debe **portarse a SQL** en el DW.
Se trabaja **en vivo** contra el modelo abierto en Power BI Desktop vía **MCP `powerbi-modeling-mcp`**
(ListLocalInstances → Connect → `partition_operations`/`measure_operations`/`dax_query_operations`…).
Conexión PG del modelo: **DSN ODBC `pocion_marts`** (driver *PostgreSQL Unicode(x64)*, `SSLmode=require`),
base `railway`, esquema `marts`. ⚠ Se migró del conector nativo
`PostgreSQL.Database("switchback.proxy.rlwy.net:37790","railway")` a **ODBC** para que el refresco en el
Service no falle: el Postgres de Railway presenta un cert `CN=localhost` que rompe *verify-full*;
`SSLmode=require` cifra sin verificar hostname. Tablas → `Odbc.DataSource` (navegación `railway→marts→tabla`);
vistas `v_*` → `Odbc.Query` (el driver no las lista en la navegación). Ver `docs/bi_conexiones_marts.md`
(código M de cada consulta) y `docs/bi_refresco_gateway.md` (crear el DSN local/VPS + gateway).
- **Objetivo cumplido (2026-07-23):** el modelo ya **no lee archivos locales `G:\`**. 11 tablas repuntadas:
  - 9 a `marts.bi_*` (por `cargar_bi_datasets.py` + `cargar_cuentas_clave.py`): LINEAS, OFERTAS,
    BASE_CUENTAS_CLAVE, Clientes Impulso, PRESUPUESTO GENERAL, cliente_credito, CUENTAS_CLAVE ANEXO,
    INV CCLAVE ANEXO, TIENDAS_CCLAVE.
  - **NIELSEIQ** → `marts.bi_nielsen` (FECHA sale de `periods` "…fin dd/mm/yy"; MARCAS reclasificado en el loader).
  - **Cartera** → vista **`marts.v_cartera`** (agrupada por `numero`; RANGO MORA/ORDEN calculados en M).
  - Único que sigue no-local: `respuestas_cartera` (Google Sheets, web) — fuera de alcance.
- **⚠ Los cambios por MCP NO persisten:** hay que **Guardar el .pbix** (Ctrl+S). Reabrir sin guardar
  **borra todos los repuntes**.
- **Gotchas al repuntar** (ver memoria `pbi_desconexion_local`): nombres de columna en `bi_*` =
  `DBLoader._limpiar_columnas` (minúsculas, sin tildes/ñ, espacios→`_`) + `_loaded_at`/`_source_file` +
  `id SERIAL`; **NO quitar `id` en el M**; forzar cultura **`"en-US"`** en conversiones numéricas/fecha
  (el locale es-CO lee el punto decimal como miles e infla ×10/×1000); `partition Update` en batch a
  veces da "base version negative" (transitorio → reintentar); descubrir columnas reales con una
  tabla-sonda `Table.FromList(Table.ColumnNames(x),…)` + refresh + EVALUATE.
- **Deck financiero** (PyG/Balance): medidas `marts …` en `_medidas_odoo` sobre `fact_movimiento_contable`;
  clasificación por **código PUC** (no `nivel_movimiento`); la tabla legacy `Medidas PYG`/`base pyg`
  (CSV `base_consolidada`) fue **eliminada** (0 refs). Ver `docs/guia_bi_reporting.md` y memoria `pbi_pyg_clasificacion`.

## Data Warehouse — modelo estrella (esquema `marts`)  ⭐ trabajo activo
Nuevo pipeline separado del cron `raw`. **Un solo hecho** a grano de línea contable que sirve
ventas, cartera y estados financieros; en Power BI se importa ese hecho + dimensiones y se filtra
con **DAX** (no se duplican tablas). Docs: `docs/MODELO_ESTRELLA.md` y `docs/GUIA_OPERACION.md`.
**Referencia de comandos que se pueden correr y en qué casos: `docs/GUIA_OPERACION.md` §2.**
- `etl_dw_marts.py` — ETL del DW. Modos: `--full` (histórico), `--incremental` (write_date),
  `--rebuild [--desde --hasta]` (recrea por rango), `--dims` (solo dimensiones). Carga **por año,
  más reciente primero**; reintentos ante 502 de Odoo + reconexión de BD; refresco de dimensiones
  por su `write_date`; `marcar_reversos` y `aplicar_correcciones` al cierre.
- `run_dw.py` — **entrypoint del cron de Railway** (`railway.toml` → `*/15 * * * *`): ligera cada
  15 min, completa en el tick :00, rebuild del año actual días 3 y 24 a las 03h (solo :00), con
  advisory lock anti-solapamiento. Reemplazó al antiguo sync raw (archivado).
- `sql/marts/01..12_*.sql` — DDL: dims (`dim_fecha/cuenta/tercero/producto/diario/vendedor/
  empresa/centro_costo`), hecho `fact_movimiento_contable`, vistas (`v_ventas`, `v_cartera`,
  `v_balance_comprobacion`, `v_dq_analitica`), control (`etl_control`), calidad, `correcciones`,
  `10_centro_costo_odoo.sql` (dim CC 100% Odoo), `11_puc_canonico.sql` (canonicalización PUC, no
  destructivo), `12_estados_financieros.sql` (`seccion/concepto/nivel_movimiento` para estados
  financieros, desde `account.report`) y `13_puc_nombres.sql` (`clase/grupo/cuenta/subcuenta_nombre`
  desde `account.group`). `09_nivel_movimiento.sql` quedó **superseded** por 12. Todos idempotentes.
  **Ventas (14–16):** `14_ventas.sql` (`v_ventas_producto`, ventas netas a grano de producto, +
  `v_nc_sin_asignar` = NC sin factura enlazada, excluidas de ventas),
  `15_dims_ventas.sql` (enriquece `dim_tercero`: telefono/email/etiqueta/cliente_padre;
  `dim_producto.es_kit`; y `fact.equipo`), `15b_kits.sql` (`dim_kit_componente` + `v_ventas_explotada`) y
  `16_mapeos_ventas.sql` (mapeos NO-Odoo `map_zona/map_zona_cundinamarca/map_zona_bogota/
  map_cliente_padre/map_categoria`, poblados por `cargar_mapeos.py`).
- **Ventas desde el DW (reemplaza el pipeline de Excel `ReportClassNew.pipeline_bi`):**
  `v_ventas_producto` = líneas clase 4 con `es_venta` y `es_reverso IS NOT TRUE`, producto comercial
  (`codigo` LIKE `PCN%/KD%/TNG%/B8%`); netas por `venta_neta`/`cantidad_neta` (NC restan, la contabilidad
  ya enlaza la NC → no se casa por `ref`). Enriquecimiento antes local, ahora desde Odoo: `dim_tercero`
  += `telefono/email/etiqueta` (`res.partner.category`) `/cliente_padre` (`commercial_partner_id`);
  `dim_producto.es_kit`. **`equipo` (Equipo de ventas) va en el HECHO**, no en el
  tercero: `res.partner.team_id` está VACÍO en este Odoo (0 de ~206k) y el equipo vive en el asiento
  (`account.move.team_id`, 99,97% de las líneas de venta) — igual que el Excel, que lo mapea por
  factura. Se guarda como columna degenerada del hecho (patrón de `vendedor_id`). Kits: `dim_kit_componente` desde
  `mrp.bom` phantom (`cargar_kits`) + `v_ventas_explotada`. Poblado: `python etl_dw_marts.py --dims`.
  **Ventas en BI: ver `docs/guia_bi_ventas.md`** (las 2 formas de ver los kits + medidas DAX).
- **La NOTA CRÉDITO resta en el mes de SU FACTURA (`fecha_venta`)** — `19_nc_factura.sql` +
  `enlazar_notas_credito`. ⭐ **Auditar con `python diagnosticar_fecha_venta.py`** (solo lectura).
  El enlace NC→factura se arma con una **CASCADA DE EVIDENCIA** y el método queda en
  `map_nc_factura.metodo_enlace`: 1) `reversed_entry` (`reversed_entry_id`, el más fuerte), 2) `ref`
  (número de factura en la referencia, mismo cliente y candidato único), 3) `conciliacion`
  (`account.partial.reconcile`, el más débil: "se aplicó contra" ≠ "corrige a"). Medido 2026:
  2.617 NC enlazadas — **2.053 por `reversed_entry`**, 513 por conciliación, 51 por `ref`. La cascada
  rescató ~394 NC que antes no tenían conciliación y quedaban FUERA de ventas (`v_nc_sin_asignar` bajó
  de 45 NC/−304M a 11 NC/−31,9M en 2026; abril bajó 202M al entrar `RFEX2` −200,8M).
  ⚠ **`es_reverso` no ve las anulaciones que Odoo deja sin `reversed_entry_id`** → 2ª pasada
  `marcar_reversos_puente` (tras `enlazar_notas_credito` en `main`; exige `proporcion`>0,999 +
  cobertura clase 4 ≥99% + **misma firma producto:cantidad**). Sin ella, factura y NC netean a 0 pero
  **inflan el bruto y las unidades**: `FE7301` (09-mar-2026, 662,2M) ↔ `RINV254` (28-abr, −662,2M)
  dejaba marzo con bruto 8.252,9M en vez de 7.307,8M (neto igual). Ese par era el causante del salto
  ±600M mar/abr que se veía al comparar `fecha_venta` vs `fecha_factura`.
- **NOTA DÉBITO: NO es venta, salvo si ANULA una nota crédito** (`25_nd_factura.sql` +
  `enlazar_notas_debito`, puente `marts.map_nd_factura`). Regla de negocio: ventas = facturas − devoluciones.
  Si una devolución se anuló, no hubo devolución → se repone el valor **en el mes de la factura**, no en
  el de la ND. Cadena **ND → NC → FACTURA** por el `ref` de la ND (formato fijo `"<documento>, <motivo>"`,
  41 de 44 ND): `FE7281` (09-mar-2026) ← la anula `RINV/2026/0062` ← la anula `NDY1` (24-abr, 612,9M) →
  **`NDY1` suma en MARZO**. Antes sumaba en abril e inflaba el mes (era la 2ª mitad del salto mar/abr).
  Diarios ND = `dim_diario.codigo IN ('NDY','NDEXP')` (⚠ por **código**, no por nombre; son `tipo='sale'`
  igual que una factura). Las ND que apuntan a una FACTURA (cargo extra: `NDY4` 49,2M "Ajuste por
  precio") o sin `ref` quedan **fuera** → `marts.v_notas_debito_excluidas`. `es_nota_debito` sigue en
  `v_ventas_producto`/`v_ventas_bi` pero ahora marca **solo las ND que sí son venta**.
  Simetría útil: si la NC no se pudo enlazar a una factura, tampoco entra su ND → ninguna de las dos
  cuenta (2026: 21 de 44 ND son venta; ~103M de ND que anulan NC sin factura quedan fuera con su NC).
  ⚠ La visión **CONTABLE** sí lleva las ND (son ingreso): `v_ventas`, `v_balance_comprobacion` y
  `v_exportaciones` **no** las excluyen a propósito.
  ⚠ Al comparar contra el Excel, agrupar por `fecha_factura` **reproduce el error del Excel** (descarta
  las NC que no casan por `ref`+producto): sirve para comparar, no para reportar. Antes una NC restaba en su propio mes: `NCR1858` (mar-2026) corrige
  `FEVY80693` (nov-2025) y deprimía marzo e inflaba noviembre. Medido 2025-2026: **777 NC** en un mes
  distinto al de su factura, ~**6.584M** mal atribuidos. El enlace **solo existe en la CONCILIACIÓN**
  (`account.partial.reconcile`): la mayoría de NC no traen `ref` ni `reversed_entry_id`. El puente
  `marts.map_nc_factura` guarda `proporcion` (una NC puede corregir varias facturas → se **prorratea**;
  por eso `linea_id` no es único en la vista, ~76 de ~2.200 NC) y `fecha_venta`.
  ⚠ Se **excluyen las notas débito**: también son `out_invoice` y solo se distinguen por el **diario**
  (`Nota Debito Nacional Yumbo`/`Exportacion`). **3 fechas en `v_ventas_producto`:** `fecha_venta`
  (⭐ para VENTAS) · `fecha_factura` (propia del doc, para informe de NC por mes) · `fecha` (contable).
  ⚠ **NC SIN factura asignada NO cuentan en ventas:** una NC que no se pudo enlazar (no está en
  `map_nc_factura`) queda **fuera** de `v_ventas_producto`/`v_ventas_bi` (`AND NOT (out_refund AND
  m.nc_factura_id IS NULL)` en `14_ventas.sql`); se aíslan en **`v_nc_sin_asignar`** para conciliar a
  mano. Medido 2026: 45 NC / ~−304M (~−233M con factura de 2026, el resto emitidas contra facturas de
  años previos).
- **KITS — dos presentaciones y reparto de valor:** `v_ventas_producto` = **kits vendidos** (el kit es
  la unidad, tal como se factura); `v_ventas_explotada` = **unidades de producto** (kit repartido en
  componentes). ⚠ **No sumar ambas**: es el mismo dinero (los totales coinciden exacto).
  El valor del kit se prorratea por el **precio individual de cada componente**, con el promedio
  **dentro de su categoría de cliente** (`marts.v_precio_componente`; cascada: precio en su categoría →
  promedio global → partes iguales). A partes iguales desviaba 20-25% por producto.
  ⚠ **`es_kit` = kit REAL** (BOM phantom con componentes, 39 productos), **NO** `bom_count>0` — eso
  marcaba también los **fabricados** (139). Lo fija `cargar_kits`, no `refrescar_dimensiones`.
  ⚠ Odoo tiene **2 BOM phantom por kit** (77 para 39): `cargar_kits` toma **una sola** (la más reciente)
  y normaliza por el lote (`bom.product_qty`); sumarlas duplicaba las unidades de la explosión.
- **Mapeos de negocio NO-Odoo (única excepción local, a demanda):** `cargar_mapeos.py` lee de Drive
  (`DriveLoader` + `DRIVE_IDS`) → `marts.map_*`: ZONA por depto+categoría (+ Cundinamarca por
  depto+ciudad), CLIENTE PADRE, y CATEGORÍA normalizada. Correr cuando cambie un Excel.
  `map_zona_bogota` quedó **DEPRECADA** (`Base_bogota.xlsx` ya no se usa; tabla creada pero vacía).
- **CATEGORÍA (tipo de cliente) consolidada — `fact.categoria`** (`17_categoria.sql` +
  `consolidar_categoria`, paso de cierre post-carga). Sirve igual a **ventas y contabilidad**. Se arma
  de **2 fuentes de Odoo, ninguna basta sola**:
  1. `partner_type_id` (cabecera del asiento) → `dim_tercero.tipo_cliente`. **Manda** cuando existe.
  2. Analítico **plan 21 "Canal"** (`analytic_line_ids/x_plan21_id`) → **ya está como `fact.canal`**
     (el rol se deriva del nombre del plan). **Rellena** cuando falta (1). Existe porque la utilidad
     por cliente se mira por nombre del cliente pero **hay gastos de esos clientes cargados a
     TERCEROS** que desaparecerían del análisis; es lo que rescata las clases 5/6.
  Luego se replican las reglas de respaldo del Excel (`transformar_base`) **en su orden**:
  **EXPORTACION** (`es_venta` a cliente `EXTERIOR` **o** centro de costo `[EXPO]`; el `es_venta` evita
  meter gastos de proveedores extranjeros como AWS/Odoo Inc) → `equipo='Shopify'`→SHOPIFY →
  `equipo='Punto de venta'`→CALL CENTER → `CLIENTE`→CALL CENTER → base → default **CALL CENTER**.
  Cierra normalizando con `marts.map_categoria`. (La antigua regla "país extranjero→nombre del país"
  se **eliminó**: metía proveedores extranjeros como "United States".)
  ⚠ `fact.categoria` = categoría de **CLIENTE**; `dim_producto.categoria` es la de **PRODUCTO**
  (en `v_ventas_producto` se expone como `producto_categoria`). Son cosas distintas.
- **Exportaciones (PyG por país y cliente) — `18_exportaciones.sql` + `v_exportaciones`:** dos planes
  analíticos nuevos de Odoo. **Plan 20 "País"** (`[PAIS-*]`) ya está en `fact.pais_analitico`. **Plan 22
  "Cliente"** (`[CLI-ZAR-EC]`…) se captura ahora como **`fact.cliente_analitico`** (rol `cliente` en
  `derivar_plan_rol`/`construir_hecho`) — atribuye **ventas y gastos** al cliente correcto (los gastos
  de logística van a proveedores como TRANSTAINER, no al cliente; el analítico es lo que los enlaza).
  Backfill de lo ya cargado: `backfill_cliente_analitico` (vía `account.analytic.line.x_plan22_id`,
  ~4k líneas). **`fact.pais`** = `dim_tercero.pais` de la línea (país estricto; se puebla en
  `consolidar_categoria`). El código del cliente trae el país en el sufijo (`-EC/-PE/-US/-DO/-CO`);
  el "error de Colombia" venía de que `x_plan20` quedaba en `[PAIS-CO]` por defecto. `v_exportaciones`
  = todo lo `EXPORTACION` (o con `cliente_analitico`) para auditar y proyectar el PyG por país×cliente.
  **PyG por país: agrupar por `v_exportaciones.pais_destino`**, NO por `pais`: los gastos de
  exportación se cargan a proveedores logísticos colombianos, así que `pais` los deja en Colombia.
  El país sale del **plan 22 (cliente)**, no del tercero: `pais_destino` = **nombre del cliente**
  (`marts.map_cliente_pais`) → sufijo del cliente analítico (`[CLI-ZAR-EC]`→Ecuador) → país en el nombre
  del centro `[EXPO]` → `pais` si no es Colombia. El **nombre manda** porque al inicio el país se
  clasificaba mal y quedaba en Colombia: si un código quedara en `-CO` por error, el nombre lo corrige.
  `map_cliente_pais` es editable (patrón `ILIKE` → país; se siembra con `cargar_mapeos.py`); al sumar un
  cliente del exterior basta agregar la fila. ⚠ Se consulta con **subconsulta escalar `LIMIT 1`, no
  con JOIN**: un nombre puede matchear 2 patrones (el analítico de Leopharma contiene "Lepharma" y
  "LEOPHARMA") y un JOIN duplicaría la línea, doblando los importes.
  **Toda línea con plan 22 de un cliente NO-CO es `EXPORTACION`** (regla en `consolidar_categoria`):
  así entran los **costos** (clase 6) y los gastos de terceros que el analítico asocia a la exportación
  aunque el tercero sea colombiano — sin esa regla quedaban como `EXTERIOR` y el PyG perdía ~395M.
  Validado 2026 (ingresos/costo/gastos): Ecuador 947M/324M/26M · USA 273M/30M/8M · Dominicana
  261M/41M/18M · Perú 175M/**0**/9M. Pendientes de fuente: Perú sin costo tagueado y
  `[EXPO] EPO-08-2026 FEX 7` (14,4M) sin país en el nombre.
- ⚠ **`EXPORTACION` ≠ `EXTERIOR`** (Odoo usa la MISMA etiqueta `EXTERIOR` para clientes de exportación
  y para proveedores extranjeros): `EXPORTACION` = lo que **vendemos** afuera (+ logística `[EXPO]`);
  `EXTERIOR` = lo que **compramos** afuera (AWS, Odoo Inc, Apple…, clases 5/6 ≈ 3.465M). La regla manda
  a EXPORTACION solo las **ventas** (`es_venta`). Las categorías miden **ventas**; `EXTERIOR` queda
  como bucket de gastos de proveedores del exterior (hoy no se usa para reportar).
- **Fuente:** todo de Odoo (`account.move.line`+`account.move`, catálogos), salvo `dim_fecha`
  (calendario generado) y `correcciones` (overrides manuales).
- **Reglas del hecho:** `es_venta`/`es_reverso` (ventas = clase 4 sin **anulaciones reales**:
  factura + NC de reversión que la cubre ≥99%; **NO** por `payment_state='reversed'`, que en este Odoo
  lo pone también el **factoring** y las NC **parciales** — esas son ventas reales que sí cuentan),
  `es_cxc`+`saldo_pendiente` (cartera = residual por línea de CxC),
  `empresa_id` (multiempresa: 1=Aristizabal Hector Fabio, 8=PCN Poción), PUC por prefijo del código
  (`clase_codigo`/`grupo_codigo`). Fechas como DATE (`fecha`, `fecha_factura`,
  `fecha_vencimiento`) además de las `*_key`.
- **Clasificación para estados financieros (100% de los reportes de Odoo):** `dim_cuenta` trae 3
  niveles del árbol del reporte (`account.report`, es_CO): **`seccion`** (raíz:
  ACTIVOS/PASIVO/PATRIMONIO · Ingresos/Gastos/Costos…), **`concepto`** (intermedio, padre del leaf:
  Gastos, Activos corrientes, PATRIMONIO…) y **`nivel_movimiento`** (DETALLE/hoja, el nivel del PyG:
  Operacionales de administración, Costo de ventas, Deudores…), vía `cargar_clasificacion_reportes`
  (Balance id 24 + Estado de Resultados id 38). Cubre **todas las clases** (1–7). Match por
  **prefijo de código** de las líneas hoja (`engine='account_codes'`, prefijo más largo, con
  exclusiones `\(...)`): NO siempre a 2 díg (17/28 corriente/no corriente; 51 excluye 5160/5165). Sin
  dict manual `NIVEL_N2`. Flujo de efectivo (report 5) no tiene líneas por cuenta → follow-up. Ver
  `docs/MODELO_ESTRELLA.md` §11.
- **Jerarquía PUC por cuenta (nombres):** `dim_cuenta` también trae `clase_nombre/grupo_nombre/
  cuenta_nombre/subcuenta_nombre` desde `account.group` (es_CO, nombre más frecuente por prefijo;
  `cargar_puc_nombres`, `13_puc_nombres.sql`). Complementa (no reemplaza) los `*_codigo` y la
  clasificación de reportes. Ej.: 510506 → 5 GASTOS / 51 OPERACIONALES DE ADMINISTRACION / 5105
  GASTOS DE PERSONAL / 510506 GASTOS DE PERSONAL SALARIOS.
- **Roles de planes analíticos** (`canal`/`cliente_analitico`/`linea_producto`/`tipo_producto`/
  `pais_analitico`/`centro`) se **derivan del nombre** de `account.analytic.plan` en Odoo
  (`derivar_plan_rol`), no de IDs fijos; plan `La Poción` (id 3) = excepción legacy de centro de costo.
  Plan 22 "Cliente" → `cliente_analitico` (ver Exportaciones).
- **Canonicalización PUC (no destructivo):** en Odoo coexisten 2 códigos para la misma cuenta
  (8 vs 9 díg). `dim_cuenta` tiene `cuenta_canonica_id`/`codigo_canonico`/`nombre_canonico`
  (`11_puc_canonico.sql` + `canonicalizar_puc`): canónico = variante **más usada** de misma
  subcuenta (6 díg) + mismo nombre normalizado. El **hecho conserva el `cuenta_id` real de Odoo**;
  en Power BI se agrupa por `codigo_canonico`. Docs: `docs/MODELO_ESTRELLA.md` §10.

## Dashboards de la INTRANET — ⭐ EL DESTINO DE LOS TABLEROS (reemplazan a Power BI)
**Decisión del proyecto:** los tableros dejan Power BI y pasan a la **intranet**, una **app interna de
la compañía** (**otro repo**: `proyecto pocion/intranet`, app `apps/dashboards`), presentados como
**HTML dinámico** (gráficos web con ECharts que consultan la BD en vivo, con permisos por tablero
definidos por el admin). **Contrato de datos completo:
[`docs/dashboards_intranet.md`](docs/dashboards_intranet.md).**

- **Consecuencia de diseño: la lógica de negocio BAJA AL SQL.** La intranet solo hace `SELECT`, así que
  todo lo que en Power BI era una medida DAX o un paso de Power Query tiene que quedar resuelto en el
  DW (vistas, MV, columnas del hecho). Ya portados: exclusión de notas débito (era filtro DAX), mes de
  la nota crédito (`fecha_venta`), cruce ventas vs presupuesto por categoría (era un join manual).
  **Al añadir una regla nueva, va en SQL — no en DAX.**
- Se quitan de encima el techo de refrescos de **Power BI Pro** (8/día) y el **gateway**: el cron corre
  cada 15 min y la intranet lee directo.

- **⚠ REGLA: los dos repos NO se mezclan.** Todo lo de base de datos (DDL, MV, roles, refresco) vive
  **aquí** y se documenta **aquí**; la intranet solo hace `SELECT`. Cada repo cumple una función puntual.
- **Por qué hacen falta MV:** Power BI *importa* y agrega en memoria; un dashboard web consulta **en
  vivo** en cada carga. `v_ventas_bi` es vista sobre `v_ventas_explotada` (window functions) sobre
  `v_ventas_producto` (7 joins) → se reconstruye entera (910k filas) en CADA consulta. Medido
  2026-07-28: *ventas por mes* **6.892 ms** y *top 10 clientes* **8.277 ms** → con las MV, 318 ms y
  712 ms (**22× / 12×**). Con 5-6 paneles eran ~40 s de CPU de BD por usuario que abría el tablero.
- **`sql/marts/23_mv_dashboards.sql`** (fase 1 = hoja **Ventas**): `mv_ventas_dia` (176.979 filas,
  series temporales), `mv_ventas_mes` (851.515, desgloses y top-N — incluye producto),
  `mv_ventas_kpi_mes` (296, conteos DISTINTOS: facturas/clientes/líneas), `mv_presupuesto_mes` (347,
  tipa `bi_presupuesto` que es todo `VARCHAR`) y **`mv_ventas_presupuesto_mes`** (360, ventas vs
  presupuesto por **mes × categoría**). Cada una con **índice ÚNICO** (lo exige
  `REFRESH … CONCURRENTLY`) + índices por fecha/periodo y por cada FK de filtro. Cuadre verificado:
  155.384.962.862 idéntico al origen, diferencia 0 mes a mes.
  Idempotente vía DROP+CREATE ⇒ re-ejecutarlo **reconstruye** (~43 s); el refresco rutinario NO usa
  este archivo.
- **PRESUPUESTO ↔ categorías de Odoo (filtro dinámico)** ⭐: la categoría del presupuesto es
  **`bi_presupuesto.canal`**, ⚠ **NO `categoria_cliente`** (esa es el NIVEL del cliente —
  DIAMOND/SILVER/GOLD — y viene vacía en 302 de 347 filas). `mv_presupuesto_mes` expone ahora
  **`categoria`** = `canal` normalizado con **`map_categoria`**, que es el vocabulario de
  `fact.categoria`. Los dos vocabularios ya coincidían casi 1:1; solo hubo que añadir a
  `cargar_mapeos.py`: **`INTERNACIONAL`→`EXPORTACION`** y los typos de Odoo
  **`CL,IENTE`/`CLENTE`/`CLIENTE`→`CALL CENTER`**. El cruce vive en
  **`mv_ventas_presupuesto_mes`** (`FULL OUTER JOIN`, para que una categoría con presupuesto y sin
  ventas —o al revés— siga apareciendo) con `venta/presupuesto/cumplimiento_pct/falta`.
  ⚠ Se refresca **AL FINAL** (lee de `mv_ventas_mes` y `mv_presupuesto_mes`). Asimetrías: presupuesto
  **solo 2026**, **sin empresa** (suma HFA+PCN) y `venta` por **`fecha_venta`** (no admite
  `date_basis=factura`). Cuadre verificado: `SUM(venta)` idéntico a `v_ventas_bi`.
- **`sql/marts/24_rol_intranet.sql`**: rol **`intranet_ro`** + vistas de lookup `v_lk_tercero`,
  `v_lk_producto`, `v_lk_vendedor`, `v_lk_empresa`. Espejo de `20_agente.sql`: **NO** se concede
  acceso al hecho, a `dim_*` crudas, a `v_ventas_bi` ni a las `bi_*`. `v_lk_tercero` **excluye
  a propósito** NIT/teléfono/email (208k terceros; un tablero de ventas no necesita datos personales).
  Verificado: lee los 9 objetos permitidos y recibe `permission denied` en los 6 prohibidos y en
  `CREATE`/`INSERT`/`REFRESH`. La contraseña **no va en el repo** (`ALTER ROLE … PASSWORD` aparte).
- **`refrescar_mv_dashboards.py`**: lo llama **`run_dw.py` al final** de cada corrida (después del
  incremental **y** del rebuild), en `try/except` — si un tablero no se refresca, el ETL igual
  termina bien. ~45 s las 4 MV. Registra cada refresco en **`marts.bi_mv_refresh`** (la intranet usa
  `MAX(refreshed_at)` como versión de caché y para mostrar "datos actualizados hace X").
  ⚠ `REFRESH … CONCURRENTLY` **no admite transacción** → la conexión va en `autocommit`; y exige la
  vista ya poblada + índice único (si falla por eso, reintenta sin `CONCURRENTLY`).
- **Reglas de uso** (detalle en el doc): el valor **siempre** con `venta`; ⚠ nunca `cantidad_neta`
  (nivel KIT, se repite → infla ~30%); **`facturas` NO es aditivo** (usar `mv_ventas_kpi_mes`); la
  fecha de negocio es `fecha_venta` (la NC resta en el mes de SU factura); ids nulos → `-1` y textos
  → `'(sin …)'` por el índice único.
- **Limitaciones del origen a tener en cuenta**: el presupuesto es **solo 2026** y **no tiene columna
  de empresa** (no se puede separar HFA / PCN); las ventas empiezan **2024-06-01** (YoY 2025 vs 2024
  parcial); todas las `bi_*` son `VARCHAR(512)` → si cambia el Excel origen, **revalidar los casts**.
- **`sql/marts/26_contabilidad_dashboards.sql`** (fase 2 = hoja **Contabilidad**, aplicada
  2026-07-29): sustituye las **seis** sub-páginas del informe (PYG · Situación Financiera · Flujo de
  Efectivo · Comportamientos · Detalle · KPIs). ⚠ **Re-ejecutar `24_rol_intranet.sql` DESPUÉS** de
  este (concede los `GRANT` y numéricamente va antes). El DDL entero se aplica en **12,7 s**.
  - **`v_dim_cuenta_bi`** — las **14 columnas que en Power BI son calculadas DAX**
    (`concepto_contable`, `orden_informe`, `categoria_gasto`, `concepto_balance`/`orden_balance`,
    `clasif_liquidez`/`orden_liquidez`, `bal_nivel1`/`bal_nivel2` + sus `orden_*`, `cuenta_etiqueta`,
    `flujo_renglon`/`flujo_actividad`/`orden_flujo_actividad`) más `es_dya`, `es_dya_linea`,
    `signo_pyg`, `signo_bal`.
    ⚠ **ES UNA VISTA, no columnas materializadas en `dim_cuenta`, y es deliberado**: el `upsert` del
    ETL solo escribe las columnas del DataFrame, así que un `ALTER`+`UPDATE` sobreviviría en las
    cuentas existentes pero **cada cuenta nueva entraría con las 14 en NULL** y caería a un bucket sin
    etiqueta hasta que alguien re-ejecutara el UPDATE a mano. Y entran de continuo: `dim_cuenta` pasó
    de **1.939 (1-jul) a 1.945 (29-jul)**. Son 1.945 filas: el `CASE` es gratis.
    ⚠ La clasificación va por **CÓDIGO PUC**, nunca por `nivel_movimiento`/`seccion`/`concepto`: esos
    solo están poblados para la **empresa 8**, así que basar las medidas en ellos deja a HFA en blanco.
  - **`mv_contab_cuenta_mes`** (9.366 filas) — la **única** MV que escanea el hecho; grano
    empresa × mes × cuenta, por **fecha CONTABLE**. Las tres siguientes **derivan de ella** (así se
    pasa de 4 escaneos de 4,37 M a 1).
  - **`mv_balance_mes`** (14.949) — clases 1/2/3, con `movimiento`, `saldo_acum` y
    `saldo_presentacion`. ⚠ **DENSA** (`generate_series` × pares empresa/cuenta con `LEFT JOIN`):
    una cuenta bancaria sin movimientos **sigue teniendo saldo**; sin la rejilla desaparecería en los
    meses tranquilos y el acumulado saltaría sin hueco.
  - **`mv_pyg_mes`** (972) — clases 4/5/6/7, grano empresa × mes × `concepto_contable` ×
    **`cuenta_codigo` (N4)**. ⚠ **El N4 en el grano no es opcional**: sin él 5160/5165 caen dentro del
    grupo 51 y EBITDA, resultado operativo y la línea de D&A **dejan de ser calculables**, y no se
    recupera después.
  - **`mv_flujo_mes`** (351) — solo los renglones **agregables**, con el signo ya resuelto (un aumento
    de activo consume caja). Los derivados y los de stock (caja inicial/final) los arma la intranet.
  - **`mv_contab_tercero_mes`** (250.526) — ⚠ **PIVOTADA a columnas** (`ingresos`, `costos`, `gastos`,
    `ingresos_no_op`, `gastos_no_op`, `utilidad`): hay **137.612** terceros con movimiento contable, y
    con el concepto en filas serían ~1,7 M. El top-N se resuelve con `LIMIT` **en SQL**.
  - **`mv_contab_centro_mes`** (564, expone `plan` porque los centros mezclan centros reales con
    proyectos `[EXPO]`) y **`mv_contab_canal_mes`** (611).
  - **Semillas `bi_pyg_renglon`** (catálogo y **orden decimal** de los renglones derivados: 5.1, 9.1,
    10.1, 12.5, 15.5) y **`bi_tasa_renta`** (`(1, 0.39)`, `(8, 0.35)`). Van en la base y no en el
    código de la intranet: las tasas cambian con cada reforma y el orden no puede vivir duplicado.
  - **`v_lk_cuenta`** para `intranet_ro`. El **hecho contable y `dim_cuenta` siguen NEGADOS**
    (verificado con `has_table_privilege`).
- **⚠ LAS DOS REGLAS DE GASTO NO SON SIMÉTRICAS** (lo más fácil de equivocar de la hoja):
  admin = grupo **51 EXCLUYENDO** `cuenta_codigo` 5160/5165 · ventas = grupo **52 COMPLETO** (sí
  incluye 5260/5265) · D&A del renglón = 5160/5165 · **addback del EBITDA** = 5160/5165/**5260/5265**.
  Signo de presentación: `clase_codigo IN ('4','2','3') → −1`.
  **Verificado al peso** contra el informe (PCN mayo-2026): ingresos 6.830.236.960, costo
  2.801.095.140, admin 404.721.946, ventas 2.651.211.097, D&A 3.132.892, D&A total 6.469.192,
  provisión al 35 % 340.956.034.
- **⚠ Lo que se midió antes de escribir el DDL** (y que lo hace defendible): 33 meses de datos
  contables (2023-12-31 → 2026-08-09), 4.375.278 líneas, y `SUM(debito)-SUM(credito) = **−0,01**` ⇒
  la partida doble cuadra y el asiento de apertura está dentro del rango, así que **el saldo
  acumulado se puede calcular**. Sin ese dato los saldos estarían desplazados por una constante y el
  balance **seguiría "cuadrando"**: el error sería simétrico e invisible. Además: **0** cuentas sin
  código en las clases 1-7 y las clases 8/9 **sin un solo movimiento**.
- **⚠ Los estados financieros NUNCA se consolidan** (la intranet exige UNA empresa y responde 400 sin
  ella). Cinco motivos: los dos PUC son casi disjuntos (**923 de 924** cuentas las usa una sola
  empresa), HFA no tiene grupo 51 (todo su opex va al 52), la clasificación de Odoo solo está poblada
  para la 8, las tasas de renta difieren, y hay **intercompañía** — el 4.º proveedor de PCN es la
  propia empresa 1 con 714 mill., que un consolidado duplicaría sin que nada lo detecte.
- **⚠ Tres cálculos del informe de Power BI están MAL y la intranet los CORRIGE** (decisión de
  William): la Situación Financiera mostraba el **movimiento del mes** y lo llamaba balance, con las
  cuentas de resultado dentro (de ahí que su `Total` diera 0,00 — era la partida doble completa);
  el Flujo daba a «financiación» el mismo valor que a «inversión» (85.188.799,95) teniendo
  obligaciones financieras de −1.702.189.935,80; y los KPIs salían en **días negativos**. Con
  `saldo_acum` el balance cuadra: `ACTIVO = PASIVO + PATRIMONIO + resultado`, diferencia **0,00** en
  la empresa 8. **No volver a "arreglarlo" para que cuadre con Power BI.**
- **Refresco separado**: `refrescar_mv_dashboards.py` divide `MVS_VENTAS` (cada tick) de
  `MVS_CONTAB` (**solo el tick `:00`**, cuando `run_dw` corre completo). Las contables son de grano
  mensual y en los ticks ligeros las líneas nuevas llegan aún **sin `categoria`**.
  ⚠ **El orden de `MVS_CONTAB` no es negociable**: `mv_contab_cuenta_mes` va primera y tres derivan
  de ella; al revés se servirían datos del refresco anterior con un `refreshed_at` nuevo, y **nada lo
  delataría**.
- **`sql/marts/27_ventas_dashboards_fase2.sql`** (fase 3 = las **9 sub-páginas restantes de Ventas**
  + una nueva que no existe en Power BI, aplicada 2026-07-30, 20,6 s). ⚠ **Re-ejecutar
  `24_rol_intranet.sql` DESPUÉS.** Añade `mv_ventas_kit_mes` (800 filas), `mv_ventas_cliente_primera`
  (120.978) y `mv_ventas_recompra` (1.075), más dos semillas de negocio
  (`bi_producto_lanzamiento`, `bi_ciclo_vida`). Los dos lookups crecieron **en el 24**, que es su
  casa: `v_lk_producto` + `linea`/`linea_categoria`, `v_lk_tercero` + `zona`.
  Contrato completo y todas las mediciones en `docs/dashboards_intranet.md` §10.
  - ⚠ **`bi_lineas` se une por el CÓDIGO de los corchetes, no por el nombre**: por código casan
    35/35 filas y cubren el **94,43 %** del valor 2026; por nombre solo 16/35 y **39,90 %**. Con el
    join malo el negocio cae en «(sin línea)» y parece un hueco de datos que no existe. Los 5
    productos que sí faltan son reales (PCN32-36, CONTROL CASPA/ANTICAÍDA) y hay que **añadirlos al
    Excel `LINEAS Y CATEGORIAS.xlsx`**.
  - ⚠ **Las unidades de kit salen de `v_ventas_producto`, NO de la vista explotada**: allí un kit
    aparece una vez por componente con la *misma* `cantidad_neta` → 139.370 en 2026 contra las
    **32.851** reales (×4,2). El **valor** sí coincide por las dos vías (6.269.015.175 en 2025).
  - ⚠ **`mv_ventas_recompra` lleva columna `nivel` y sus niveles NO se suman**: un
    `COUNT(DISTINCT factura_id)` por cliente no se rueda hacia arriba (comprar A una vez y B una vez
    = «1 vez» por producto pero «2 veces» en total). Medido: `canal` suma 43.741 clientes y `total`
    da 43.557. Y **no lleva empresa a propósito**.
  - ⚠ **La fecha de lanzamiento NO es derivable** y no se debe sacar de `MIN(fecha_venta)`: la
    historia arranca en 2024-06 y el informe muestra lanzamientos de 2021. Sembradas 17 fechas
    legibles de la captura; el resto queda sin fila y la intranet dice «sin fecha de lanzamiento».
  - ⚠ **No fijar cifras de 2026 en un test**: el ETL corre cada 15 min. En el mismo día MAYORISTA NV
    pasó de 18.126.483.426 a 18.135.911.362. Usar **2025** (cerrado: venta 82.417.391.917) o
    invariantes («las 4 zonas suman el total del canal»).
- **Fases siguientes** (cada hoja añade sus MV aquí + su `GRANT`): Nielsen → cuentas clave/KAM →
  cartera (portar los buckets de mora que hoy calcula Power Query). Contabilidad y Ventas ya están.
  ⚠ **El margen bruto NO se puede hacer por la vía de ventas**: `dim_producto` no tiene
  `standard_price` ni ningún costo. Solo existe por la vía contable (`mv_contab_canal_mes`:
  ingresos grupo 41 − costos grupo 61).
  ⚠ Tres KPIs de contabilidad quedaron **fuera del v1 por falta de fuente**: el **desperdicio de
  materia prima no es derivable** (el DW no extrae `stock.move` ni manufactura) y los dos de
  **anticipos** necesitan que contabilidad indique el código PUC exacto. En Power BI los tres dan
  0,00, o sea que allí también están vacíos.

## Variables de entorno (en `.env`, NO versionado — usar solo nombres, nunca valores)
- Odoo: `url`, `db`, `username_odoo`, `password`.
- PostgreSQL (Railway): `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- Correo: `SENDER_EMAIL`, `SENDER_PASSWORD`.
- Google Drive: `GOOGLE_CREDENTIALS_PATH` (ruta al JSON de service account).

## Convenciones
- Esquema crudo actual: `raw`. Objetivo del DW: `staging` (crudo) + `marts` (estrella).
- Clave primaria de las tablas sincronizadas = `id` natural de Odoo (BIGINT).
- Idempotencia vía UPSERT por `id`; el watermark vive en la columna `write_date` destino.
- `_pg_type` mapea tipos pandas→PG; default `VARCHAR(512)`, `TEXT` para columnas largas.

## Avisos / gotchas
- `date` / `invoice_date` aterrizan como `VARCHAR(512)` (Odoo los devuelve string y
  `_pg_type` solo convierte a TIMESTAMP los dtypes datetime64 reales).
- `preparar_y_cargar` NO añade columnas de auditoría `_loaded_at` / `_source_file`
  (sí lo hace `cargar()`, ruta no usada por el ETL del DW).
- El ETL del DW (`etl_dw_marts.py`) tiene reintentos (502 Odoo + reconexión BD); el sync raw
  archivado no los tenía.
- El watermark `write_date` no detecta hard-deletes; por eso el DW se **recrea** (`--rebuild`) ~2×/mes.
- `virtual-env/` está commiteado por error (está en `.gitignore`); no editarlo.
- DW: cargar **por año** (el `id` de Odoo NO sigue el orden de fecha; `id asc` deja años parciales).
- DW: las empresas 1 y 8 pueden tener **PUC distinto** (al crear PCN cambiaron cuentas) → validar y
  agregar el estado de resultados **por empresa**, nunca mezclando ambas.
- `marts.fact_movimiento_contable._loaded_at` ya usa hora **Colombia** (`America/Bogota`).
- **Refresco de dimensiones: SIEMPRE por páginas.** Un `search_read` sin `limit` de `res.partner`
  (~206k con contacto/etiqueta/padre) hace que Odoo **corte la respuesta a medias** →
  `http.client.IncompleteRead`. `refrescar_dimensiones` pagina con `PAGINA`. No quitar el paginado
  "porque cabe": el payload creció al añadir campos y quedó al filo.
- **`IncompleteRead`/`BadStatusLine` heredan de `http.client.HTTPException`, NO de `OSError`** → hay
  que nombrarlas explícitamente en el `except` de `Odoo._exec` o el ETL muere sin reintentar.
- **Self-heal de dimensiones (`asegurar_dims_hecho`):** las dims/catálogos se cargan UNA vez al inicio;
  algo CREADO en Odoo mientras corre el ETL (tercero/producto/cuenta/diario/centro nuevo…) no está en
  su dim → viola la FK del hecho (pasaba con terceros en `--rebuild`, ~1h). Antes de cada `upsert` del
  hecho, `asegurar_dims_hecho` mira las columnas FK del DataFrame ya construido y trae de Odoo **solo
  los ids faltantes** de CADA dim (tercero/producto/vendedor/cuenta/diario/empresa/centro_costo +
  genera `dim_fecha`), reutilizando los mismos row-builders de `cargar_catalogos_pequenos`. Corre
  siempre (incremental y full/rebuild); el hueco normal es 0 → sin lecturas extra. La red de
  aislamiento fila-a-fila del `upsert` queda como último recurso.

## PENDIENTES del DW (retomar aquí)
- Carga inicial `--full` (TRUNCATE + todos los años) — al terminar, **validar**:
  estado de resultados PCN (empresa 8) 2026 vs reporte Odoo (grupos 41/42/51/52/53/61, exacto),
  conteos por año = Odoo, `tipo_cliente` poblado, `fecha` DATE, partida doble.
- ✅ HECHO: `nivel_movimiento` etiqueta canónica completa (41/42/47/51/52/53/54/57/59/61/62/7x;
  `09_nivel_movimiento.sql` aplicado, 0 cuentas P&L en NULL) + roles de planes derivados de Odoo.
- ✅ HECHO: `dim_centro_costo` **100% Odoo** (`account.analytic.account`: `codigo`/`nombre`/`plan`/
  `activo`/`empresa_id`); se eliminaron `adm_vtas`/`origen`/`tipo` (venían del Excel `CC`, no existen
  en Odoo). `10_centro_costo_odoo.sql` aplicado. **Regla: nada en el DW se alimenta de fuentes locales.**
- ✅ HECHO: canonicalización PUC (`11_puc_canonico.sql` + `canonicalizar_puc`): `dim_cuenta` con
  `cuenta_canonica_id`/`codigo_canonico`/`nombre_canonico` (no destructivo, hecho intacto); 401 grupos,
  423 cuentas colapsadas. Docs en `docs/MODELO_ESTRELLA.md` §10.
- ⚠ **PENDIENTE CRÍTICO — DESPLEGAR EN RAILWAY.** El cron corre `run_dw.py` pero con el código
  **anterior**: cada tick hace `TRUNCATE` del puente NC y lo repuebla solo por conciliación, y
  `marcar_reversos` desmarca lo que puso `marcar_reversos_puente`. Comprobado 2026-07-28/29: los
  arreglos aplicados a mano se **revierten en la siguiente hora**. Hasta el deploy no se sostienen ni
  la cascada NC, ni las anulaciones sin `reversed_entry_id`, ni el puente ND, ni el cron `*/15`.
  (`railway.toml`/`Procfile` ya están ajustados; el sync raw `etl_odoo_incremental.py` quedó archivado.)
- ✅ HECHO (2026-07-29): **hoja de CONTABILIDAD de los tableros** — `26_contabilidad_dashboards.sql`
  aplicado (`v_dim_cuenta_bi` + 7 MV + `bi_pyg_renglon`/`bi_tasa_renta` + `v_lk_cuenta`), `GRANT` en
  `24_rol_intranet.sql`, y `MVS_CONTAB` en `refrescar_mv_dashboards.py` (solo el tick `:00`).
  Verificado al peso contra el informe y con el balance cuadrando (`ACTIVO = PASIVO + PATRIMONIO +
  resultado`, diferencia 0,00 en la empresa 8). ⚠ Portadas a SQL las **14** columnas calculadas DAX de
  `dim_cuenta` —no las 5 que decía el plan— como **VISTA** (`v_dim_cuenta_bi`), no como columnas
  materializadas: el `upsert` del ETL no las mantendría y cada cuenta nueva entraría en NULL.
  Detalle en la sección de dashboards y en `docs/dashboards_intranet.md` §9.
- ✅ HECHO (2026-07-30): **hoja de VENTAS completa** — `27_ventas_dashboards_fase2.sql` aplicado
  (`mv_ventas_kit_mes`, `mv_ventas_cliente_primera`, `mv_ventas_recompra` + las semillas
  `bi_producto_lanzamiento` y `bi_ciclo_vida`), `v_lk_producto` con `linea`/`linea_categoria` y
  `v_lk_tercero` con `zona` en `24_rol_intranet.sql`, y las 3 MV en `MVS_VENTAS`. Cuadres verificados:
  SHOPIFY 2026 = 6.774.546.547 (= los «$6,75 mil M» de la hoja *Pagina Web*), presupuesto por zona
  de junio = 2.400.000.001 (= el total del informe, exacto), valor de kits idéntico por las dos vías.
  Detalle y todas las mediciones en `docs/dashboards_intranet.md` §10.
  ⏳ **Le falta dato del negocio**: las fechas de lanzamiento de los productos y kits que no están en
  la captura, y confirmar los cortes 18/36 meses de `bi_ciclo_vida`. Y **añadir PCN32-36 al Excel de
  líneas** (hoy son el 5,57 % del valor cayendo en «(sin línea)»).
- DQ: cuentas usadas con `clase_codigo`/`grupo_codigo` nulo o inesperado.
- **Ventas desde el DW (proyecto por fases):**
  - ✅ Fase 1: `v_ventas_producto` (netas, grano producto, comercial). Aplicada y validada (empresa 8 2026).
  - 🟡 Fases 2–4 (código escrito, **falta aplicar DDL + poblar**): `15_dims_ventas.sql`/`15b_kits.sql`/
    `16_mapeos_ventas.sql` + `etl_dw_marts.py` (dims enriquecidas + `cargar_kits`) + `cargar_mapeos.py`.
    Correr: aplicar DDL 15/15b/16 → `python etl_dw_marts.py --dims` (⚠ refresca ~206k terceros, minutos)
    → `python cargar_mapeos.py`.
  - ✅ Fase 5 (validada): `python validar_ventas.py` concilia `v_ventas_producto` vs `base_ventas`
    (CLEAN DATA). Alinear 3 cosas: combinar empresas + fecha de factura + producto comercial.
    **Destapó un bug de `es_reverso`** (se excluían facturas de factoring/NC-parcial marcadas
    `payment_state='reversed'` como si fueran anuladas): corregido (ver `marcar_reversos`). Tras el
    fix, **TOTAL 2026 Excel vs DW = -0,0%** (antes -5,1%). Residuos mensuales ≤4% (timing/parciales);
    Jul + por timing (DW con más facturas que el CSV).

## Reglas de trabajo
- NO ejecutar el cron, ni conectarse a Odoo/Postgres en vivo, sin que el usuario lo pida.
- NUNCA exponer valores de `.env`; referenciar variables por nombre.
- Antes de tocar el ETL, leer `docs/ARQUITECTURA_DW.md` (estado actual + plan por fases).
- Roadmap del DW: empezar por ventas + contable; ver fases en `docs/ARQUITECTURA_DW.md`.
