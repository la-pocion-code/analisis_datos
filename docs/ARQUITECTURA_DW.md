# Arquitectura del repositorio y plan de evolución a Data Warehouse

Proyecto BI de **La Poción**. Este documento describe el estado actual del cron de
sincronización Odoo → PostgreSQL y propone un plan por fases para evolucionar hacia un
data warehouse con modelo estrella.

> Las secciones **1–4** son hechos verificados en el código. Las secciones **5–6** son
> propuesta de mejora, separadas de los hechos.

> **ACTUALIZACIÓN (2026-07):** el cron de Railway ya **no** corre el sync raw
> `etl_odoo_incremental.py` (archivado en `archivado/`). El entrypoint del cron es ahora
> **`run_dw.py`** (ETL del DW `marts`, horario). Las secciones 2–4 describen el sync raw histórico
> —hoy archivado— y se conservan como referencia. Operación vigente del DW:
> `docs/MODELO_ESTRELLA.md` y `docs/GUIA_OPERACION.md`.

---

## 1. Mapa del repositorio

Punto de entrada del cron: **`run_dw.py`** (DW `marts`). El antiguo `etl_odoo_incremental.py`
(sync raw) quedó archivado.

```
analisis_datos/
├── run_dw.py                   ★ ENTRYPOINT del cron: ETL del DW marts (incremental + rebuild)
├── etl_dw_marts.py             ETL del modelo estrella (Odoo XML-RPC → marts)
├── Procfile                    worker: python run_dw.py
├── railway.toml                Railway: startCommand + cronSchedule "0 * * * *" (horario)
├── archivado/etl_odoo_incremental.py   sync raw histórico (retirado del cron)
├── requirements.txt            pandas, numpy, psycopg2-binary, python-dotenv
├── requirements_local.txt      variante local
├── README.md                   mínimo (1 línea)
├── .env                        credenciales (ignorado en git) — NO versionado
├── .gitignore                  ignora virtual-env/, .env, *.xlsx/*.csv/*.pdf, build/, dist/
├── google_credentials.json     service account Google Drive (ignorado en git)
├── classes/
│   ├── db_loader.py            ★ DBLoader: conexión PG, auto-DDL, UPSERT, carga incremental
│   ├── drive_loader.py         DriveLoader: lee Excel/CSV de Google Drive → DW
│   ├── send_mail.py            MailSender: correos SMTP con adjuntos
│   └── clase_reportes_new.py   ReportClassNew (~2500 líneas): motor BI manual (Excel)
├── conexion_odoo.ipynb         notebook de pruebas/ejecución Odoo
├── odoo_api_test.ipynb         notebook de pruebas API Odoo
├── ejecuciones_anilista.ipynb  notebook de ejecuciones ad-hoc
├── archivado/                  código legacy (etl_odoo_historico.py = reset de tablas, etc.)
├── build/ · dist/              artefactos PyInstaller (legacy)
└── virtual-env/                entorno virtual (versionado por error; debería ignorarse)
```

**Observación:** `virtual-env/` está **commiteado** pese a estar en `.gitignore` (~17k archivos).
Limpieza recomendada (fuera de alcance de esta documentación).

---

## 2. Cómo funciona el cron

- **Disparador:** Railway Cron (no APScheduler ni while-loop). `railway.toml` →
  `cronSchedule = "*/15 * * * *"` → ejecuta `python etl_odoo_incremental.py` cada 15 min
  de inicio a fin. `Procfile` define el mismo comando como `worker`.
- **Arranque defensivo:** `verificar_db()` (`etl_odoo_incremental.py:13-34`) abre conexión PG
  con `connect_timeout=10`; si falla → `sys.exit(1)` (aborta antes de tocar Odoo).
- **Detección de cambios = watermark incremental por `write_date`:**
  - `ultima_fecha()` (`:77-81`) hace `SELECT MAX(write_date) FROM raw.<tabla>`;
    default `2024-01-01 00:00:00`.
  - Domain Odoo (`:94-97`): `["write_date", ">", desde]` +
    `["move_id.move_type", "in", ["out_invoice","out_refund"]]`.
  - El watermark **se persiste implícitamente** en la propia columna `write_date` de la
    tabla destino (no hay tabla de control aparte).
- **Lectura paginada de Odoo:** `descargar_modelo_paginado()` (`:54-67`) usa `search_read`
  con `limit=2000`, `offset` incremental, `order='id asc'`.
- **Escritura:** `expandir()` (`:71-74`) desdobla Many2one `[id,nombre]` en `<col>_id` y
  `<col>_nombre`; luego `DBLoader.preparar_y_cargar()` hace **UPSERT**
  `INSERT ... ON CONFLICT (id) DO UPDATE` (`db_loader.py:365-405`), creando la tabla con
  auto-DDL si no existe.
- **Errores/reintentos:** `main()` (`:158-165`) envuelve cada job en try/except con
  `logging(exc_info=True)` y continúa con el siguiente. **No hay lógica de reintentos.**
  En `preparar_y_cargar` el UPSERT es una **única transacción** (all-or-nothing). En
  `cargar()` (ruta no usada por el cron) el commit es por lote de 5000 con rollback por lote.
- **Frecuencia real:** cada 15 min; el único job activo (`JOBS`, `:151-155`) es
  `sync_apuntes_contables`.

### Diagrama de flujo

```
Railway Cron (cada 15 min)
  └─ etl_odoo_incremental.py
       ├─ verificar_db()                      [timeout 10s; exit(1) si falla]
       └─ main()
            ├─ conectar_odoo()                [XML-RPC authenticate]
            └─ for job in JOBS:               [try/except + log, sin retry]
                 └─ sync_apuntes_contables()
                      ├─ ultima_fecha()       [SELECT MAX(write_date)]
                      ├─ domain: write_date > desde + move_type in (...)
                      ├─ descargar_modelo_paginado()  [limit 2000, offset]
                      ├─ expandir()           [Many2one → _id / _nombre]
                      └─ preparar_y_cargar()  [CREATE IF NOT EXISTS + UPSERT(id)]
```

---

## 3. Inventario de modelos/tablas sincronizados HOY

| Modelo Odoo | Estado | Tabla destino | Filtro |
|---|---|---|---|
| `account.move.line` | **ACTIVO** | `raw.odoo_apuntes` | `write_date > MAX` + `move_type in (out_invoice, out_refund)` |
| `purchase.order` | stub comentado (`:118-129`) | `raw.odoo_purchase_orders` | — |
| `stock.quant` | stub comentado (`:132-147`) | `raw.odoo_stock` (+ snapshot) | — |

**Campos leídos de `account.move.line`** (`:98-102`):
`id, date, invoice_date, move_id, account_id, partner_id, quantity, price_unit,
price_subtotal, debit, credit, balance, name, write_date`.
Los Many2one `account_id, partner_id, move_id` se expanden a `_id` / `_nombre`.

**DDL de `raw.odoo_apuntes`** — generado dinámicamente por `preparar_y_cargar` (no existe
DDL versionado). `id BIGINT PRIMARY KEY`; numéricos → `NUMERIC`; `write_date` → `TIMESTAMP`;
**`date` / `invoice_date` aterrizan como `VARCHAR(512)`** porque Odoo los devuelve como
string y `_pg_type` solo mapea a `TIMESTAMP` los dtypes `datetime64` reales.
`preparar_y_cargar` **no** añade las columnas de auditoría `_loaded_at` / `_source_file`
(sí lo hace `cargar()`, que es una ruta distinta no usada por el cron).

---

## 4. Conexión y configuración (solo nombres de variables — nunca valores)

- **Odoo:** `xmlrpc.client` contra `/xmlrpc/2/common` (auth) y `/xmlrpc/2/object` (datos).
  Variables: **`url`, `db`, `username_odoo`, `password`** (`etl_odoo_incremental.py:40-50`).
- **PostgreSQL (Railway):** `psycopg2`. Variables:
  **`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`**
  (`db_loader.py:62-66`, `etl_odoo_incremental.py:16-23`).
- **Correo:** `MailSender` usa **`SENDER_EMAIL`, `SENDER_PASSWORD`** (`send_mail.py:27-28`).
- **Google Drive:** `DriveLoader` usa service account vía
  **`GOOGLE_CREDENTIALS_PATH`** (ruta al JSON de credenciales) (`drive_loader.py:86`).
- `load_dotenv()` carga `.env` en cada módulo. **No existe `.env.example`.**

---

## 5. Brechas frente a la meta de DW *(propuesta)*

1. **Sin separación staging/mart:** todo aterriza en `raw.odoo_apuntes`, una tabla
   semi-transformada (Many2one ya desdoblados) que mezcla extracción y modelado. No hay
   capa `marts` ni dimensiones/hechos.
2. **Sin dimensiones conformes:** no se extraen `account.account` (cuentas/PUC),
   `res.partner`, `account.journal`, `product.*`, `res.users`. No hay `dim_fecha`.
3. **Jerarquía PUC no resuelta:** solo `account_id_id` / `account_id_nombre` planos; falta
   clase/grupo/cuenta/subcuenta para balance y estado de resultados.
4. **Tipo de cliente ausente:** no se extrae el campo de Odoo que clasifica al tercero;
   requerido por el hecho de ventas.
5. **Sin historización (SCD):** el UPSERT por `id` sobrescribe; cambios de atributos de
   dimensión (p. ej. tipo de cliente, nombre de cuenta) no quedan historiados.
6. **Borrados/cancelaciones no capturados:** el watermark `write_date` ve modificaciones e
   inserciones, pero **no detecta hard-deletes** en Odoo ni cambios de `state` a cancelado
   si no se incluye el campo `state` / `move.state`.
7. **Fidelidad de tipos:** fechas como `VARCHAR`; falta tipado fuerte y claves foráneas.
8. **Claves:** se usa el `id` natural de Odoo como PK; faltan surrogate keys y claves de
   hecho hacia dimensiones.
9. **Granos para ventas:** hoy `account.move.line` filtra por `move_type` de venta, pero
   incluye **todas** las líneas del asiento (impuestos, cuenta por cobrar), no solo líneas
   de producto. El hecho de ventas necesita grano = línea de producto.
10. **Operacional:** sin `.env.example`, sin tests, sin DDL versionado, `virtual-env/`
    commiteado, `clase_reportes_new.py` monolítico.

---

## 6. Plan por fases *(propuesta)*

Convención objetivo: esquema **`staging`** (crudo Odoo, 1 tabla por modelo) + esquema
**`marts`** (`dim_*`, `fact_*` estrella). Reutilizar `descargar_modelo_paginado()` y
`DBLoader`. Empezar por **ventas + contable**.

### Esquema estrella objetivo

```
                      dim_fecha
                          │
  dim_cuenta ── fact_contable ── dim_tercero ── dim_diario
  (PUC)            (grano:           (tipo
                líneadeasiento)     cliente)

  dim_producto ── fact_ventas ── dim_tercero ── dim_vendedor
                  (grano:           (tipo          dim_fecha
               líneadefactura)     cliente)        dim_diario
```

### Fase 0 — Fundamentos (bajo riesgo, sin romper el cron actual)
- Añadir `.env.example` (solo nombres), versionar DDL, sacar `virtual-env/` del control de
  versiones.
- Corregir tipado de fechas (`date` / `invoice_date` → `DATE`/`TIMESTAMP`) y unificar
  columnas de auditoría `_loaded_at` en `preparar_y_cargar`.
- *Dependencia:* ninguna. *Riesgo:* mínimo.

### Fase 1 — Capa staging formal
- Extraer modelos fuente crudos a `staging`: `stg_account_move`, `stg_account_move_line`,
  `stg_account_account`, `stg_res_partner`, `stg_account_journal`, `stg_product`. Mantener
  watermark `write_date` por tabla (idempotente vía UPSERT por `id`).
- Incluir campos clave hoy ausentes: `state`, `move_type`, `journal_id` (en move), código
  y jerarquía de cuenta, tipo de cliente.
- *Dependencia:* acceso de lectura a esos modelos en Odoo.
  *Riesgo:* volumen del backfill histórico de `account.move.line`.

### Fase 2 — Dimensiones conformes en `marts`
- `dim_fecha` (generada por SQL), `dim_cuenta` (PUC resuelto por prefijo de código →
  clase/grupo/cuenta/subcuenta/auxiliar), `dim_tercero` (con tipo de cliente), `dim_diario`,
  `dim_producto`, `dim_vendedor`.
- Iniciar como SCD Tipo 1 (sobrescritura); marcar candidatas a SCD2 (tipo de cliente, cuenta).
- *Dependencia:* Fase 1. *Riesgo:* reglas del PUC colombiano; validar con contabilidad.

### Fase 3 — `fact_contable` (grano = línea de asiento)
- Medidas: `debit, credit, balance, quantity, price_unit, price_subtotal`.
  FK: `dim_cuenta, dim_tercero, dim_fecha, dim_diario`. Habilita balance de comprobación y
  estado de resultados.
- *Dependencia:* Fases 1–2.

### Fase 4 — `fact_ventas` (grano = línea de factura de producto)
- Filtrar líneas de producto (excluir impuesto/CxC). Medidas:
  `quantity, price_unit, price_subtotal, descuento, total`.
  FK: `dim_producto, dim_tercero (tipo cliente), dim_fecha, dim_vendedor, dim_diario`.
- Decisión a confirmar: fuente del grano = líneas de factura (`account.move.line` de
  producto) vs `sale.order.line`.
- *Dependencia:* Fases 1–2.

### Fase 5 — Robustez y expansión
- Historización SCD2 donde aplique; detección de borrados/cancelaciones (incluir `state`,
  reconciliación periódica de ids); pruebas de calidad de datos; migrar el BI manual
  (`clase_reportes_new.py`) a consumir `marts`.
- Luego sumar **compras** (`purchase.order(.line)`) e **inventario** (`stock.quant` +
  snapshots) ya previstos como stubs.

### Decisión de tooling (a confirmar)
Transformaciones de marts como **scripts SQL versionados** orquestados por el mismo runner,
o adoptar **dbt-core** para staging→marts. Recomendación: empezar con SQL versionado y
evaluar dbt en Fase 5.
