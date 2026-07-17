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
- `classes/db_loader.py` — `DBLoader`: conexión PG, auto-DDL, UPSERT, carga incremental.
- `classes/drive_loader.py` — `DriveLoader`: lee Excel/CSV de Google Drive.
- `classes/send_mail.py` — `MailSender`: correos SMTP con adjuntos.
- `classes/clase_reportes_new.py` — `ReportClassNew` (~2500 líneas): motor BI manual.
- `archivado/` — código legacy (incl. `etl_odoo_incremental.py`, el antiguo sync raw ya retirado
  del cron, y `etl_odoo_historico.py`, que solo dropea tablas).

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
  **Ventas (14–16):** `14_ventas.sql` (`v_ventas_producto`, ventas netas a grano de producto),
  `15_dims_ventas.sql` (enriquece `dim_tercero`: telefono/email/etiqueta/cliente_padre;
  `dim_producto.es_kit`; y `fact.equipo`), `15b_kits.sql` (`dim_kit_componente` + `v_ventas_explotada`) y
  `16_mapeos_ventas.sql` (mapeos NO-Odoo `map_zona/map_zona_cundinamarca/map_zona_bogota/
  map_cliente_padre/map_categoria`, poblados por `cargar_mapeos.py`).
- **Ventas desde el DW (reemplaza el pipeline de Excel `ReportClassNew.pipeline_bi`):**
  `v_ventas_producto` = líneas clase 4 con `es_venta` y `es_reverso IS NOT TRUE`, producto comercial
  (`codigo` LIKE `PCN%/KD%/TNG%/B8%`); netas por `venta_neta`/`cantidad_neta` (NC restan, la contabilidad
  ya enlaza la NC → no se casa por `ref`). Enriquecimiento antes local, ahora desde Odoo: `dim_tercero`
  += `telefono/email/etiqueta` (`res.partner.category`) `/cliente_padre` (`commercial_partner_id`);
  `dim_producto.es_kit` (`bom_count>0`). **`equipo` (Equipo de ventas) va en el HECHO**, no en el
  tercero: `res.partner.team_id` está VACÍO en este Odoo (0 de ~206k) y el equipo vive en el asiento
  (`account.move.team_id`, 99,97% de las líneas de venta) — igual que el Excel, que lo mapea por
  factura. Se guarda como columna degenerada del hecho (patrón de `vendedor_id`). Kits: `dim_kit_componente` desde
  `mrp.bom` phantom (`cargar_kits`) + `v_ventas_explotada` (unidades × cantidad BOM, valor prorrateado
  por cantidad BOM). Poblado: `python etl_dw_marts.py --dims` (dims + kits). Ver `docs/MODELO_ESTRELLA.md`.
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
- **Fuente:** todo de Odoo (`account.move.line`+`account.move`, catálogos), salvo `dim_fecha`
  (calendario generado) y `correcciones` (overrides manuales).
- **Reglas del hecho:** `es_venta`/`es_reverso` (ventas = clase 4 sin reversos totales
  `payment_state='reversed'`), `es_cxc`+`saldo_pendiente` (cartera = residual por línea de CxC),
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
  - ⏳ Fase 5: validar `v_ventas_producto` mensual vs `base_ventas` del Excel + documentar diferencias.

## Reglas de trabajo
- NO ejecutar el cron, ni conectarse a Odoo/Postgres en vivo, sin que el usuario lo pida.
- NUNCA exponer valores de `.env`; referenciar variables por nombre.
- Antes de tocar el ETL, leer `docs/ARQUITECTURA_DW.md` (estado actual + plan por fases).
- Roadmap del DW: empezar por ventas + contable; ver fases en `docs/ARQUITECTURA_DW.md`.
