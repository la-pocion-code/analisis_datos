# CLAUDE.md — Proyecto BI La Poción (analisis_datos)

Guía para Claude Code. Repo de scripts ETL/BI del analista de datos de La Poción.
Documentación extendida y roadmap del DW: `docs/ARQUITECTURA_DW.md`.

## Qué es este repo
- Cron en **Railway** que carga el **Data Warehouse** (`Odoo → PostgreSQL marts`) cada hora.
- Más scripts de BI manual (Excel, Google Drive, correo) en `classes/` y notebooks.
- Idioma del proyecto y de la comunicación: **español**.

## Componente principal: el cron del DW
- Entrypoint: **`run_dw.py`**. Disparado por Railway Cron (`railway.toml` → `0 * * * *`, horario).
  Mismo comando en `Procfile` (worker: `python run_dw.py`).
- Cada disparo: **incremental** siempre (`etl_dw_marts.main("incremental")`) + **rebuild** del año
  actual los días 3 y 24 a las 03h. Detalles del ETL en la sección "Data Warehouse" abajo.
- El sync antiguo a `raw.odoo_apuntes` (`etl_odoo_incremental.py`) quedó **archivado**
  (`archivado/`, ya no corre); el DW lee de Odoo directo, no de `raw`. `raw.odoo_apuntes` sigue
  existiendo para el BI legacy pero ya no se actualiza por cron.

## Archivos clave
- `run_dw.py` — **entrypoint del cron** (dispatcher DW: incremental horario + rebuild 3/24).
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

## BI Power BI — modelo `DASHBOARD POCION` (desconexión de archivos locales)  ⭐
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
- `run_dw.py` — **entrypoint del cron de Railway** (`railway.toml` → `0 * * * *`): incremental por
  hora + rebuild del año actual días 3 y 24 a las 03h. Reemplazó al antiguo sync raw (archivado).
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

## Dashboards de la INTRANET (migración desde Power BI)  ⭐ nuevo 2026-07-28
La intranet (**otro repo**: `proyecto pocion/intranet`, app `apps/dashboards`) está reemplazando los
tableros de Power BI por gráficos web con ECharts, con permisos por tablero definidos por el admin.
**Contrato de datos completo: [`docs/dashboards_intranet.md`](docs/dashboards_intranet.md).**

- **⚠ REGLA: los dos repos NO se mezclan.** Todo lo de base de datos (DDL, MV, roles, refresco) vive
  **aquí** y se documenta **aquí**; la intranet solo hace `SELECT`. Cada repo cumple una función puntual.
- **Por qué hacen falta MV:** Power BI *importa* y agrega en memoria; un dashboard web consulta **en
  vivo** en cada carga. `v_ventas_bi` es vista sobre `v_ventas_explotada` (window functions) sobre
  `v_ventas_producto` (7 joins) → se reconstruye entera (910k filas) en CADA consulta. Medido
  2026-07-28: *ventas por mes* **6.892 ms** y *top 10 clientes* **8.277 ms** → con las MV, 318 ms y
  712 ms (**22× / 12×**). Con 5-6 paneles eran ~40 s de CPU de BD por usuario que abría el tablero.
- **`sql/marts/23_mv_dashboards.sql`** (fase 1 = hoja **Ventas**): `mv_ventas_dia` (176.979 filas,
  series temporales), `mv_ventas_mes` (851.515, desgloses y top-N — incluye producto),
  `mv_ventas_kpi_mes` (296, conteos DISTINTOS: facturas/clientes/líneas) y `mv_presupuesto_mes` (347,
  tipa `bi_presupuesto` que es todo `VARCHAR`). Cada una con **índice ÚNICO** (lo exige
  `REFRESH … CONCURRENTLY`) + índices por fecha/periodo y por cada FK de filtro. Cuadre verificado:
  155.384.962.862 idéntico al origen, diferencia 0 mes a mes.
  Idempotente vía DROP+CREATE ⇒ re-ejecutarlo **reconstruye** (~43 s); el refresco rutinario NO usa
  este archivo.
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
- **Fases siguientes** (cada hoja añade sus MV aquí + su `GRANT`): Nielsen → cuentas clave/KAM →
  cartera (portar los buckets de mora que hoy calcula Power Query) → contabilidad (antes hay que
  portar a SQL las columnas calculadas DAX de `dim_cuenta`: `concepto_contable`, `concepto_balance`,
  `categoria_gasto`, `flujo_actividad`, `orden_*` — son `CASE` sobre código PUC).

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
- ✅ HECHO: el **cron de Railway** ahora corre `run_dw.py` (horario, `railway.toml`/`Procfile`
  ajustados); el sync raw `etl_odoo_incremental.py` quedó archivado. Falta solo **desplegar** en Railway.
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
