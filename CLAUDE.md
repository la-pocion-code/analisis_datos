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
  destructivo) y `12_estados_financieros.sql` (columnas `nivel_movimiento/seccion/subseccion` para
  estados financieros; se pueblan desde `account.report`). `09_nivel_movimiento.sql` quedó
  **superseded** por 12. Todos idempotentes.
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
- **Roles de planes analíticos** (`canal`/`linea_producto`/`tipo_producto`/`pais_analitico`/
  `centro`) se **derivan del nombre** de `account.analytic.plan` en Odoo (`derivar_plan_rol`), no de
  IDs fijos; plan `La Poción` (id 3) = excepción legacy de centro de costo.
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

## Reglas de trabajo
- NO ejecutar el cron, ni conectarse a Odoo/Postgres en vivo, sin que el usuario lo pida.
- NUNCA exponer valores de `.env`; referenciar variables por nombre.
- Antes de tocar el ETL, leer `docs/ARQUITECTURA_DW.md` (estado actual + plan por fases).
- Roadmap del DW: empezar por ventas + contable; ver fases en `docs/ARQUITECTURA_DW.md`.
