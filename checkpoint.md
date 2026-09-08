# checkpoint.md — estado del repositorio

**Índice, no manual.** ⚠ **Regla que mantiene este archivo por debajo de 200 líneas: si algo
necesita explicación, va en su doc y aquí queda solo el puntero.** Las trampas medidas de cada hoja
**no se copian aquí** — se duplicarían y divergirían (ya pasó con `AGENTS.md`, que se quedó una
semana atrás y perdió la hoja de compras entera).

- **Contexto completo y trampas críticas:** [`CLAUDE.md`](CLAUDE.md)
- **Cómo operar (todos los comandos):** [`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md) §2
- **Contrato de datos de los tableros:** [`docs/dashboards_intranet.md`](docs/dashboards_intranet.md)

Última actualización: **2026-08-12**.

---

## 1. Qué corre solo

**Cron de Railway** (`railway.toml` → `*/15 * * * *`) ejecutando **`run_dw.py`**, que reparte:

| Tick | Qué hace | Duración medida |
|---|---|---|
| **:00** | corrida **COMPLETA**: catálogos + dims + kits + OC + hecho + **cierre** (reversos, puentes NC/ND, categoría, PUC) + marketing + **las 29 MV** | ~4,6 min |
| **:15 / :30 / :45** | **ligera**: dims por `write_date` + hecho + **las 12 MV de ventas y compras** | ~1,6 min |
| días 3 y 24, 03h UTC | además `--rebuild` del año actual (**solo en el tick :00**) | >15 min |

- **Advisory lock** (`pg_try_advisory_lock`, clave `8152026`): si la corrida anterior sigue viva el
  tick se omite y sale con código 0. Es lo que hace seguro el `*/15`.
- ⚠ En los ticks ligeros las líneas nuevas quedan **sin `categoria`, sin `es_reverso` y sin puente
  NC/ND** hasta el cierre de la hora. Es el precio de la frescura.
- ⚠ La hora del contenedor es **UTC**, no Colombia.
- El sync antiguo a `raw.odoo_apuntes` (`etl_odoo_incremental.py`) está **archivado**: ya no corre.

## 2. Estado por hoja

Todas las hojas alimentan la **intranet** (`proyecto pocion/intranet`, otro repo), que solo hace
`SELECT`. ⚠ **La lógica de negocio baja al SQL**: lo que era DAX o Power Query vive en vistas/MV.

| Hoja | Estado | DDL | MV | Refresco | Doc |
|---|---|---|---|---|---|
| **Ventas** | ✅ completa | `14`,`15`,`15b`,`16`,`19`,`21`,`23`,`25`,`27`,`32` | 8 | cada tick | [dashboards §10](docs/dashboards_intranet.md) · [guia_bi_ventas](docs/guia_bi_ventas.md) |
| **Contabilidad** | ✅ completa | `26` | 8 | `:00` | [dashboards §9](docs/dashboards_intranet.md) |
| **Nielsen** | ✅ completa | `28` | 2 | `:00` | [dashboards §11](docs/dashboards_intranet.md) |
| **Cuentas clave** | ✅ completa | `29` | 3 | `:00` | [cuentas_clave_migracion](docs/cuentas_clave_migracion.md) |
| **Cartera** | ✅ completa | `30` | 1 | `:00` | [dashboards](docs/dashboards_intranet.md) |
| **Marketing** | 🟡 parcial | `31` | 3 | `:00` | [dashboards](docs/dashboards_intranet.md) |
| **Compras** | ✅ **nueva (2026-08-12)** | `34`,`35` | 4 | cada tick | [dashboards §12](docs/dashboards_intranet.md) |

**30 MV en total.** DDL en `sql/marts/NN_*.sql` (todos idempotentes).
⚠ **`24_rol_intranet.sql` se re-ejecuta DESPUÉS de cualquier DDL que recree una MV**: los `GRANT` a
`intranet_ro` se pierden al recrearla.

### Compras (lo último construido)

Hecho de compras sobre los documentos de proveedor (`in_invoice`/`in_refund`: 17.875 + 642 docs
desde 2024-06), con **tres lecturas del valor en COP que no se suman entre sí** —base,
base+IVA y total pagado al proveedor— más la **dimensión de orden de compra** (`dim_orden_compra`
1.066 · `dim_oc_linea` 2.215, de `purchase.order`) enlazada al hecho por `purchase_line_id`.
4 MV: valor/cantidad por mes, conteos distintos, lead time por OC y recompra en 3 ejes.

⚠ Dos límites que el tablero debe decir, y **el detalle está en [dashboards §12](docs/dashboards_intranet.md)**:
**solo el 14,9 % de las líneas de compra tiene OC** (el resto se contabiliza sin orden), y el **lead
time** está poblado en el **81 %** de las OC.

### Marketing: qué falta

✅ funcionan la **TRM** (datos.gov.co) y el **gasto publicitario** (Meta/Google/TikTok por
Supermetrics). ⚠ **Shopify está IMPLEMENTADO (2026-08-12) y SIN PROBAR contra la API real**:
falta un token por tienda (6 tiendas, un par de variables cada una; runbook en el §1d del
contrato, en el repo de la intranet). Su lógica pura sí está verificada — 30 comprobaciones
a mano, porque este repo no tiene runner de tests. **No confundir «implementado» con
«probado»**, igual que antes no había que confundir «escrito» con «implementado».
⚠ **GA4 y Search Console siguen siendo esqueletos SIN IMPLEMENTAR**: tienen firma y
comprueban su credencial, pero **no hay código que llame a esas APIs**.

## 3. Comandos a demanda

Detalle completo y cuándo usar cada uno: **[`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md) §2**.

| Para qué | Comando |
|---|---|
| Ver el estado del DW (solo lectura) | `python estado_dw.py` · `--odoo` para cuadrar contra Odoo |
| Cambió **un** Excel del BI en Drive | `python cargar_bi_datasets.py --dataset <clave>` (~8 s) |
| Validar sin escribir antes de una carga larga | `python cargar_bi_datasets.py --dataset nielsen --seco` |
| Cambió un Excel de zonas / clientes padres / categorías | `python cargar_mapeos.py` |
| Cliente / producto / centro de costo / **OC** nuevo | `python etl_dw_marts.py --dims` |
| Un mes no cuadra (partida doble ≠ 0) | `python etl_dw_marts.py --rebuild --desde … --hasta …` |
| Refrescar tableros a mano | `python refrescar_mv_dashboards.py [--mv …]` |
| Rellenos de una sola vez (**no** los corre el cron) | `--backfill-iva` · `--backfill-compras` · `--backfill-terceros` · `--backfill-productos` |

⚠ **`--dims` NO puebla un campo NUEVO de dimensión**: va por `write_date` y solo relee lo que cambió
en Odoo. Para eso hay que releer el catálogo (ver la guía de operación).

## 4. Mapa del repo

| Ruta | Qué es |
|---|---|
| `run_dw.py` | ⭐ **entrypoint del cron**: reparte ligera/completa y toma el advisory lock |
| `etl_dw_marts.py` | ETL del DW (`--full`/`--incremental`/`--rebuild`/`--dims` + backfills) |
| `sql/marts/*.sql` | DDL del modelo estrella, vistas, MV, roles. Idempotentes |
| `refrescar_mv_dashboards.py` | refresca las 30 MV; decide qué va en cada tick |
| `cargar_bi_datasets.py` · `cargar_cuentas_clave.py` · `cargar_mapeos.py` · `cargar_marketing.py` | cargas **a demanda** desde Google Drive y APIs |
| `estado_dw.py` · `validar_ventas.py` · `validar_nc.py` · `diagnosticar_fecha_venta.py` · `conciliar_shopify.py` · `diagnosticar_producto_comercial.py` | diagnóstico y conciliación (solo lectura) |
| `classes/` | `DBLoader` (PG), `DriveLoader` (Drive), `MailSender` (SMTP), `ReportClassNew` (BI manual) |
| `ejecuciones_anilista.ipynb` | **operacional**: procesos que se ejecutan a mano desde el área (informes diarios, correos) |
| `notebooks/pruebas/` | exploración; **nada de esto es automatización** ([README](notebooks/pruebas/README.md)) |
| `archivado/` | código legacy retirado del cron |
| `docs/` | documentación extendida (ver §6) |

## 5. Pendientes reales

- **Marketing:** (a) **probar Shopify contra la API real** — hacen falta los 6 tokens y los
  3 códigos de país nuevos, y validar `venta_neta` contra un día cerrado tienda por tienda;
  (b) implementar **GA4 y Search Console**, que siguen siendo esqueletos.
- **Nielsen ↔ ventas:** el UPC de Nielsen **sí es nuestro EAN** (18 de 18, medido 2026-08-06), así
  que el cruce sell-out ↔ sell-in es posible por `dim_producto.codigo_barras`, pero **el puente no
  está construido**: `mv_nielsen_item_semana` no trae `producto_id`.
- **Ventas:** faltan fechas de lanzamiento de producto y confirmar los cortes de ciclo de vida.
- **Contabilidad:** 3 KPI fuera del v1 **por falta de fuente** (desperdicio de materia prima y los
  dos de anticipos). En Power BI también dan 0,00.
- **Producto:** `hs_code` (arancelario) y `unspsc_code_id` (DIAN) están **vacíos al 100 % en Odoo**;
  el día que el negocio los llene, es una línea de DDL.
- **Repo:** `virtual-env/` está commiteado por error (está en `.gitignore`).

## 6. Dónde están las trampas

⚠ **No se copian aquí a propósito.** Cada hoja tiene las suyas **medidas** en su doc, y las críticas
(la moneda de las exportaciones, los nombres de los markets de Nielsen, el mes de la nota crédito)
están en [`CLAUDE.md`](CLAUDE.md), que se carga automáticamente en cada sesión de Claude Code.

| Doc | Qué cubre |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | contexto completo + las trampas críticas de todas las hojas |
| [`docs/ARQUITECTURA_DW.md`](docs/ARQUITECTURA_DW.md) | árbol del repo, cron y plan por fases |
| [`docs/MODELO_ESTRELLA.md`](docs/MODELO_ESTRELLA.md) | diseño del hecho y las dimensiones |
| [`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md) | ⭐ qué comando correr y cuándo |
| [`docs/dashboards_intranet.md`](docs/dashboards_intranet.md) | contrato de datos de las 7 hojas y sus trampas |
| [`docs/guia_bi_ventas.md`](docs/guia_bi_ventas.md) · [`guia_bi_reporting.md`](docs/guia_bi_reporting.md) | medidas de ventas y del deck financiero |
| [`docs/bi_conexiones_marts.md`](docs/bi_conexiones_marts.md) · [`bi_refresco_gateway.md`](docs/bi_refresco_gateway.md) | Power BI (transitorio): conexión ODBC y refresco |
| [`docs/cuentas_clave_migracion.md`](docs/cuentas_clave_migracion.md) | hoja de cuentas clave |
| [`docs/agente_runbook.md`](docs/agente_runbook.md) | runbook del rol de agente |
