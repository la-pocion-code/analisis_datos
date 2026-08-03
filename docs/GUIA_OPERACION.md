# Guía de operación — Data Warehouse La Poción (esquema `marts`)

Cómo funciona y cómo operar el modelo estrella que alimenta Power BI. Diseño detallado del
modelo en [MODELO_ESTRELLA.md](MODELO_ESTRELLA.md); contexto general del repo en
[ARQUITECTURA_DW.md](ARQUITECTURA_DW.md).

## 1. Qué es y cómo fluye
```
Odoo (XML-RPC)                          PostgreSQL (Railway)
  account.move.line  ──┐
  account.move         │   etl_dw_marts.py         ┌─ marts.fact_movimiento_contable (ÚNICO hecho, líneas)
  account.account      ├──►  (por lotes, UPSERT) ──┤─ marts.dim_* (dimensiones)
  res.partner/product  │                           └─ vistas v_ventas / v_cartera / v_balance_comprobacion
  analytic.account…  ──┘
```
- Es el **cron activo** del proyecto (Railway → `run_dw.py`, **cada 15 min**). Reemplazó al antiguo sync raw
  `etl_odoo_incremental.py` (archivado). Lee de Odoo directo; no depende de `raw.odoo_apuntes`.
- Grano: **una línea de asiento** (`account.move.line`, `state='posted'`).
- **Un solo hecho** sirve ventas, cartera y estados financieros (en BI se filtra con DAX; no se
  duplican tablas).

---

## 2. Comandos que puedes correr y cuándo  ⭐

> Todos se corren desde la raíz del repo (`d:\Desktop\analisis_datos`) con el `.env` presente.
> Nada de esto borra Odoo; solo escribe en el esquema `marts` de PostgreSQL.

### 2.1 Ver el estado del DW (solo lectura, no cambia nada)
| Comando | Qué muestra | Cuándo |
|---|---|---|
| `python estado_dw.py` | Si el ETL está corriendo, conteo del hecho por año, rango de fechas, `tipo_cliente`, y **partida doble por empresa** (debe ≈ 0). | Chequeo rápido en cualquier momento |
| `python estado_dw.py --odoo` | Lo anterior **+ cuadre por año vs Odoo** (conteos de `account.move.line` posted). Más lento (consulta Odoo). | Para confirmar que no falta información vs Odoo |

### 2.2 Cargar / actualizar el hecho (`etl_dw_marts.py`)
| Comando | Qué hace | Cuándo usarlo |
|---|---|---|
| `python etl_dw_marts.py --incremental` | Solo cambios por `write_date` (hecho + cartera + dimensiones). Idempotente y rápido. | Actualización normal. **Es lo que corre el cron cada 15 min** (con `--sin-cierre` salvo en el tick :00); rara vez hace falta a mano. |
| `python etl_dw_marts.py --dims` | Refresca **solo catálogos y dimensiones** (cuentas, clasificación de estados financieros, centros de costo, terceros/productos/vendedores) **+ enriquecimiento de ventas** (`dim_tercero`: telefono/email/etiqueta/cliente_padre; `dim_producto.es_kit`) **+ kits** (`dim_kit_componente` desde `mrp.bom`). No toca el hecho (`fact.equipo` se llena al cargar el hecho, no aquí). | Cambió algo de **dimensiones** y quieres verlo ya: cuenta/cliente/producto nuevo, tras cambiar la clasificación, o para **poblar el enriquecimiento de ventas / kits**. ⚠ El refresco total de terceros son ~206k registros (unos minutos). |
| `python etl_dw_marts.py --rebuild` | **DELETE + recarga del AÑO ACTUAL** (años cerrados intactos). Refleja **borrados/ediciones** de Odoo que el incremental no detecta. | El año en curso no cuadra o sospechas datos viejos. El cron lo hace los días **3 y 24** a las 03h. |
| `python etl_dw_marts.py --rebuild --desde 2026-06-01 --hasta 2026-06-30` | **DELETE + recarga de un RANGO** exacto. | Un **mes o rango puntual no cuadra** (p.ej. partida doble ≠ 0 en junio). Lo más quirúrgico. |
| `python etl_dw_marts.py --full` | Carga histórica **completa** (todos los años, sin truncar; UPSERT). Larga (millones de filas). | Primera población, o reconstrucción total tras cambios de fondo. |

Opciones comunes:
- `--desde AAAA-MM-DD` / `--hasta AAAA-MM-DD` acotan el rango en `--rebuild` (y `--desde` en `--full`).
- Sin `--desde`, `--rebuild` toma **el año actual**; `--full` toma desde 2018.

### 2.3 Aplicar cambios de esquema (SQL DDL)
Los archivos `sql/marts/01..12_*.sql` son **idempotentes** (se pueden re-ejecutar). Solo hace falta
correrlos cuando **cambia el esquema** (columnas/vistas nuevas). Población de datos = vía el ETL.
```bash
# aplicar un archivo DDL (ejemplo)
python -c "import sys; sys.path.insert(0,'.'); from classes.db_loader import DBLoader; \
c=DBLoader().get_connection(); cur=c.cursor(); \
cur.execute(open('sql/marts/12_estados_financieros.sql',encoding='utf-8').read()); c.commit(); \
print('aplicado')"
```
Tras un DDL que agrega columnas de dimensión, correr `python etl_dw_marts.py --dims` para poblarlas.

### 2.4 El cron automático (no hay que correrlo a mano)
`run_dw.py` es el entrypoint del cron de Railway (`railway.toml` → **`*/15 * * * *`**). No todos los
ticks hacen lo mismo, porque el coste de una corrida es casi todo **fijo** (full scans del hecho,
TRUNCATE+rebuild de los puentes NC/ND, full scan de productos en Odoo) y repetirlo 4×/hora no trae ni
una fila más:

| Tick | Qué corre | Duración medida |
|---|---|---|
| **:00** | corrida **COMPLETA**: catálogos + dims + kits + nombre comercial + hecho + **todos los pasos de cierre** + las 12 MV | **~4,6 min** |
| **:15 / :30 / :45** | **ligera**: dimensiones por `write_date` + `cargar_hecho` + las 5 MV de ventas | **~1,6 min** |
| días 3 y 24, **03:00** | además `--rebuild` del año actual (**solo en el tick :00**) | mucho más de 15 min |

- ⚠ **Las duraciones son las de RAILWAY**, medidas en la primera corrida del `*/15` (2026-07-29
  19:15 UTC). Las de minutos que aparecen en `db_loader.log` son de una máquina local, donde manda la
  latencia a Odoo y a Postgres — **no** sirven para dimensionar el cron: allí el ETL tarda minutos y en
  Railway ~1,5 min con todo el cierre. Lo que ahorra el reparto ligero/completo es **4× tráfico
  XML-RPC a Odoo, 4× full scans del hecho y 4× el refresco de las 7 MV de contabilidad**.
  ⚠ Hoy **la fase dominante es el refresco de MV**, no el ETL (§6.1).
- ⚠ En los ticks ligeros las líneas nuevas quedan **sin `categoria`, sin `es_reverso` y sin puente
  NC/ND** hasta el cierre de la hora. Es el precio de la frescura.
- ⚠ **Advisory lock** (`pg_try_advisory_lock`, clave `8152026`): si la corrida anterior sigue viva, el
  tick se **omite** con un warning y sale con código 0. Sin esto, dos corridas solapadas dejarían los
  puentes NC/ND incompletos.
- ⚠ **Qué tick hace el cierre se decide por ESTADO, no por el reloj**: se registra en
  `marts.etl_control` (clave `cierre_dw`) y se hace el cierre si no ha corrido en la hora en curso.
  El cron de Railway se retrasa 0-4 min (medido), así que una guarda por minuto podría dejar una hora
  entera sin consolidar si la deriva se comiera la ventana. Si el cierre falla, el siguiente tick lo
  reintenta (solo se marca cuando termina bien).
- ⚠ La hora es la del **contenedor = UTC**, no Colombia: la ventana "días 3 y 24 a las 03h" cae en
  realidad a las ~22:00 del día anterior en hora local.

Para probarlo localmente igual que el cron: `python run_dw.py`. Para forzar solo la parte ligera:
`python etl_dw_marts.py --incremental --sin-cierre`.

### 2.6 Mapeos de negocio de ventas (NO-Odoo) — `cargar_mapeos.py`
`python cargar_mapeos.py` lee de Google Drive (vía `DriveLoader`) los Excel de **zonas** (general,
Cundinamarca, Bogotá), **clientes padres** y **categorías**, y recrea las tablas `marts.map_*`
(TRUNCATE + insert). Es el **único insumo NO-Odoo** del DW y se corre **a demanda** (cuando cambie
alguno de esos Excel). Requiere el DDL `sql/marts/16_mapeos_ventas.sql` aplicado.

### 2.6b Datasets del BI (NO-Odoo) — `cargar_bi_datasets.py`
`python cargar_bi_datasets.py` sube a `marts.bi_*` los datasets que el BI todavía leía de archivos
locales, para **desconectar Power BI del PC**. Lee de Google Drive (`DriveLoader`) y recarga completa
(`if_exists='replace'`). Se corre **a demanda** (cuando cambie un archivo en Drive). Tablas:

| Tabla | Origen (Drive) |
|---|---|
| `bi_lineas` | LINEAS Y CATEGORIAS.xlsx |
| `bi_ofertas` | OFERTAS.xlsx |
| `bi_presupuesto` | PRESUPUESTO GENERAL.xlsx |
| `bi_clientes_impulso` | Clientes Impulso.xlsx (shortcut de Drive; se resuelve) |
| `bi_cuentas_clave` | cuentas_clave/base_cuentas_clave.xlsx |
| `bi_cartera` | cartera_procesada.csv |
| `bi_cliente_credito` | cliente_cartera.xlsx |
| `bi_nielsen` | consolida los 9 Excel de la carpeta `nielseiq` (⚠ columnas `unnamed_*`: los Excel no traen encabezado limpio) |

**Pendiente / decisiones:**
- `bi_base_pyg` (base_consolidada.csv, ~1.09M filas) **NO se migra por defecto**: es redundante con el
  modelo contable del DW (`fact_movimiento_contable` / `v_balance_comprobacion`). El BI debería leer el
  PyG del DW, no un CSV duplicado. Está definido en el script (`BASE_PYG`) pero fuera del run por defecto.
- **Cuentas clave — combinados** (ventas por retailer, inventarios, tiendas): su lógica vive en un
  notebook exploratorio incompleto (`archivado/cuentas_clave.ipynb`, solo 4 de ~9 retailers, mezclada
  con un modelo de reposición). Falta definir la fuente limpia antes de portarlos a `bi_*`.

### 2.6c Marketing y responsables de cartera (2026-08-03)

**`python cargar_marketing.py`** — carga la hoja de Marketing de la intranet en las tablas de
aterrizaje de `sql/marts/31_marketing_dashboards.sql`. Ventana móvil de 7 días; **el día en curso
no se carga** (las cuatro fuentes lo entregan incompleto).

| Opción | Para qué |
|---|---|
| `--desde 2026-01-01` | backfill |
| `--solo-trm` | solo la tasa de cambio (la única fuente que funciona hoy) |
| `--seco` | valida e informa, no escribe |

⚠⚠ **Solo la TRM funciona.** Supermetrics, GA4, Search Console y Shopify esperan credenciales que
no existen en este repo. Cada conector avisa y devuelve vacío; la corrida no se cae. Lo que hace
falta está en el repo de la intranet, `docs/dashboards/marketing-contrato.md` §0 Fase A.

⚠ Va enganchado a `run_dw.py` (paso 2b, solo tick `:00`) con import perezoso. Después hay que
refrescar: `python refrescar_mv_dashboards.py --mv mv_marketing_gasto_dia --mv mv_marketing_web_dia
--mv mv_marketing_atribucion_dia`.

**`python cargar_cartera_responsables.py`** — re-siembra `marts.bi_cartera_responsable` desde la
hoja `Responsables` de `base_cartera.xlsx` (Drive). A demanda: **cambiar un responsable es editar
el Excel y correr esto**. Con `--seco` valida sin escribir.

⚠⚠ La hoja necesita una columna **`TERCERO_ID`**, rellena SOLO en las filas de nivel cliente (las
de nivel tipo cruzan por `TIPO CLIENTE` y el id las rompería). El cruce por razón social se cae en
silencio y se le vio caerse dos veces: `FARMATODO COLOMBIA SA` contra `FARMATODO COLOMBIA S.A`
(853 MM sin responsable) y `C&L SOLUTIONS LLC.` contra `C&L SOLUTIONS LLC`. El id de FARMATODO que
factura es **268476** — hay un duplicado sin ventas que es justo el que casa por nombre.

Después: `python refrescar_mv_dashboards.py --mv mv_cartera_saldo`, y comprobar desde la intranet
con `python manage.py check_marts` (la sección 7r debe pasar a verde).

### 2.7 Recetas rápidas (síntoma → comando)
| Situación | Qué correr |
|---|---|
| "¿Cómo va el DW / cuadra con Odoo?" | `python estado_dw.py --odoo` |
| Cliente/producto/centro de costo nuevo no aparece | `python etl_dw_marts.py --dims` |
| Cambié la clasificación de cuentas (estados financieros) | aplicar el DDL si tocó columnas + `python etl_dw_marts.py --dims` |
| Poblar enriquecimiento de ventas / kits (tel/email/etiqueta/es_kit) | aplicar DDL 15/15b + `python etl_dw_marts.py --dims` |
| Cambió un Excel de zonas / clientes padres / categorías | `python cargar_mapeos.py` |
| Cambió un dataset del BI (líneas/ofertas/presupuesto/nielsen/cartera/…) | `python cargar_bi_datasets.py` |
| Cambió un archivo de CUENTAS CLAVE (ventas/inventarios por retailer) | `python cargar_cuentas_clave.py cargar` · `... inventarios` · `... tiendas` |
| Un **mes no cuadra** (partida doble ≠ 0) | `python etl_dw_marts.py --rebuild --desde AAAA-MM-01 --hasta AAAA-MM-31` |
| El **año en curso** trae datos raros/borrados | `python etl_dw_marts.py --rebuild` |
| Reconstruir **todo** desde cero | `python etl_dw_marts.py --full` |

---

## 3. Refresco de dimensiones
- **Cada corrida** refresca en full los catálogos pequeños: `dim_cuenta` (incluye
  `seccion/concepto/nivel_movimiento` derivados de los reportes de Odoo), `dim_diario`,
  `dim_centro_costo` (100% Odoo) y `dim_empresa`.
- **`dim_tercero`, `dim_producto`, `dim_vendedor`** se refrescan por su propio `write_date`
  (nuevos o modificados, aunque no tengan transacción nueva). En `--full`/`--rebuild` el refresco
  es total; en `--incremental`/`--dims`, solo cambios.
- `tipo_cliente` (de `partner_type_id` del asiento) no se pisa al refrescar el tercero.

## 4. Reglas de negocio (recalculadas al cierre de cada carga)

> ⚠⚠ **«Recalculadas» va en serio: un `UPDATE` a mano NO sobrevive.** Todo lo de esta sección se
> recalcula entero en cada tick de cierre, **con el código que está commiteado en este repo**.
> Corregir un dato a mano sin desplegar el código que lo sostiene da un arreglo que dura hasta el
> siguiente tick — y en el intervalo todo se ve bien, así que se da por bueno un informe que va a
> dejar de cuadrar solo. Pasó con `es_reverso` de `FVX1` (159,2 M) el 2026-08-01: se revirtió en
> dos días. **El orden es commit + push → re-aplicar → refrescar las MV.**

- **Ventas sin reversos:** factura anulada (`payment_state='reversed'`) + su NC → `es_reverso=TRUE`,
  excluidas de ventas. Devoluciones **parciales** (factura `paid`) sí restan vía `venta_neta`
  (`marcar_reversos`).
  ⚠ **Una NC ya cancelada por su nota débito NO cuenta para declarar anulada la factura**
  (`nc_muerta`, añadido 2026-08-01). Sin eso, `FVX1` (+159,2M) quedaba fuera de ventas sin
  haberse anulado: sus dos reversiones estaban compensadas al peso por `NDEXP1`/`NDEXP2` del
  mismo día, los cuatro documentos neteaban cero y aun así sumaban cobertura 2,187. Junio-2024
  en exportación daba **−46,8M** en vez de **+111,0M**. Detalle en
  [guia_bi_ventas.md §6.5](guia_bi_ventas.md).
- **Cartera:** líneas `es_cxc` (`account_type='asset_receivable'`) con `saldo_pendiente`
  (residual por línea) y `fecha_vencimiento_key` para aging. `v_cartera` = `es_cxc` con saldo ≠ 0.
- **Clasificación estados financieros:** `nivel_movimiento/seccion/subseccion` desde `account.report`
  de Odoo (Balance + Estado de Resultados, es_CO). Ver [MODELO_ESTRELLA.md §11](MODELO_ESTRELLA.md).
- **Canonicalización PUC:** `codigo_canonico`/`cuenta_canonica_id` unifican los códigos 8 vs 9 díg
  de la misma cuenta (no destructivo; `canonicalizar_puc`).
- **Correcciones (`marts.correcciones`):** overrides de datos mal registrados en Odoo, aplicados en
  el DW tras cargar (`aplicar_correcciones`), sin tocar Odoo.

## 5. Consumo en Power BI
- Conectar a PostgreSQL (variables `DB_*`), **modo Import**. Importar las dimensiones +
  **`fact_movimiento_contable`** (único hecho). Relaciones estrella por los `*_id` y `fecha_key`.
- **Ventas/cartera** se calculan con medidas DAX sobre el hecho (sin duplicar tablas).
- **Estado de Resultados / PyG:** filtrar `clase_codigo IN (4,5,6,7)`, agrupar por `nivel_movimiento`
  (detalle: Operacionales, Operacionales de administración, de ventas, Costo de ventas…) con subtotal
  por `seccion` (Ingresos/Gastos/Costos); medida `SUM(credito − debito)`.
- **Balance/ESF:** `clase_codigo IN (1,2,3)`, saldo acumulado `SUM(debito − credito)` hasta la fecha,
  agrupar por `seccion` → `concepto` → `nivel_movimiento`.
- **Jerarquía PUC por cuenta:** `dim_cuenta` trae `clase_nombre/grupo_nombre/cuenta_nombre/
  subcuenta_nombre` (de `account.group`, es_CO). Para una etiqueta "código - nombre", columna
  calculada DAX: `Grupo = dim_cuenta[grupo_codigo] & " - " & dim_cuenta[grupo_nombre]`
  (→ "41 - OPERACIONALES"); igual para clase/cuenta/subcuenta.
- **Ventas comerciales:** usar `marts.v_ventas_producto` (ya netea NC y excluye reversos; producto
  comercial PCN/KD/TNG/B8) — medidas `SUM(venta_subtotal)` y `SUM(cantidad_neta)`. Para ver el kit
  descompuesto en sus componentes: `marts.v_ventas_explotada` (`venta_componente`/`cantidad_componente`,
  `origen` INDIVIDUAL/KIT). Enriquecimiento de cliente/producto ya en `dim_tercero`/`dim_producto`.
- **Categoría (tipo de cliente):** usar **`fact.categoria`** (ya viene en `v_ventas_producto`). Es el
  campo **único y consolidado** que sirve a ventas y a contabilidad: sale de `tipo_cliente`
  (`partner_type_id`, manda) + analítico plan 21 (`fact.canal`, rellena), con las reglas de respaldo
  del Excel y normalizado por `map_categoria`. No tiene nulos (default CALL CENTER). Para agrupar
  gastos/costos por cliente usar también `categoria` (el analítico rescata las líneas cargadas a
  terceros). ⚠ No confundir con `producto_categoria` (categoría de producto).
- **Zona / cliente padre (no-Odoo):** unir con `marts.map_*` (ver §2.6). Orden de zona: `map_zona`
  (depto+categoría) → `map_zona_cundinamarca`. Cliente consolidado por `map_cliente_padre`.
  (`map_zona_bogota` está deprecada y vacía.)
- **Exportaciones (PyG por país y cliente):** usar `marts.v_exportaciones` (líneas `EXPORTACION` =
  **ventas** a clientes EXTERIOR + gastos en centros `[EXPO]`). Para el **PyG por país agrupar por
  `pais_destino`** (NO por `pais`): los gastos de exportación se cargan a proveedores logísticos
  **colombianos**, así que `pais` (país estricto del tercero) los deja en Colombia. `pais_destino`
  resuelve el país real en cascada **desde el plan 22 (cliente), no desde el tercero**: sufijo del
  cliente analítico (`[CLI-ZAR-EC]`→Ecuador) → nombre del centro `[EXPO]` → país del tercero si no es
  Colombia. Además **toda línea con plan 22 de un cliente NO-CO se marca `EXPORTACION`**, así entran
  los **costos** (clase 6) y los gastos de terceros asociados a la exportación aunque el tercero sea
  colombiano (TRANSTAINER, puertos…). Lo que quede en `(sin país)` = centro `[EXPO]` sin país en el
  nombre → corregir en Odoo. `cliente_analitico` (plan 22) sirve además para clientes
  clave domésticos (Novaventa, Copidrogas, Farmatodo, Pasteur).
- ⚠ **`EXPORTACION` ≠ `EXTERIOR`** (son conceptos opuestos, Odoo usa la misma etiqueta para ambos):
  `EXPORTACION` = lo que **vendemos** afuera (+ su logística); `EXTERIOR` = lo que **compramos** afuera
  (AWS, Odoo Inc, Apple…). Las categorías miden **ventas** → para ventas del exterior usar
  `EXPORTACION`; `EXTERIOR` es un bucket de gastos de proveedores extranjeros.
- Detalle de medidas: [MODELO_ESTRELLA.md §9 y §11](MODELO_ESTRELLA.md).

## 6. Programación en Railway (ya montado)
El cron corre `run_dw.py` (`railway.toml` + `Procfile`):
- *Start Command:* `python run_dw.py` · *Cron Schedule:* `*/15 * * * *`.
- Variables de entorno requeridas: `url, db, username_odoo, password, DB_HOST, DB_PORT, DB_NAME,
  DB_USER, DB_PASSWORD`.
- Al hacer **push a `main`**, Railway redepliega y el próximo tick (≤15 min) usa el código nuevo.
- Proyecto **keen-wonder** · servicio **analisis_datos** · env **production**. La fuente es el repo
  `la-pocion-code/analisis_datos` (builder RAILPACK), así que **el `cronSchedule` que manda es el del
  `railway.toml` versionado**: si no se hace push, sigue corriendo la frecuencia anterior.

### 6.1 Cuánto cuesta el cron (medido 2026-07-29 con el MCP de Railway)

Tarifas oficiales (`docs.railway.com/pricing`), facturadas **por minuto** de uso real:
**CPU $20/vCPU/mes · RAM $10/GB/mes · egress $0,05/GB · volumen $0,15/GB/mes**
(mes de 30 días = 43.200 min → **$0,000463/vCPU-min** y **$0,000231/GB-min**).

Consumo medido del servicio del cron: **0,10 vCPU pico y 0,07 GB** durante la corrida, egress ≈ 0.

**Duración real de un tick** (medida en la primera corrida del `*/15`, 2026-07-29 19:15 UTC):

| Fase | COMPLETA (:00) | ligera (:15/:30/:45) |
|---|---|---|
| ETL (`etl_dw_marts`) | ~1,5 min (incluye todo el cierre) | segundos |
| Refresco de MV | ~3,2 min (12 MV: ventas + contabilidad) | ~1,4 min (5 MV de ventas) |
| **Total del tick** | **~4,6 min** | **~1,6 min** |

El refresco de MV es ahora **la fase dominante**, no el ETL. La más cara es `mv_ventas_mes` (53 s,
853k filas) y corre en **todos** los ticks; las 7 de contabilidad (`mv_contab_*`, `mv_balance_mes`,
`mv_pyg_mes`, `mv_flujo_mes`) solo en el tick de la hora.

| Concepto | Horario, sin refresco de MV | `*/15` + refresco de MV |
|---|---|---|
| Minutos de cómputo del cron | 24 × ~26 s ≈ **10 min/día** | ≈ **225 min/día** |
| Coste del servicio del cron | ~**$0,02/mes** | ~**$0,27/mes** |
| CPU en Postgres (los `REFRESH`) | — | ~**$1,55/mes** (139 min/día de trabajo de BD) |
| **Diferencia total** | — | **≈ +2 USD/mes** |

Tres cosas que conviene tener claras al decidir frecuencias:
- **La mayor parte de esos 2 USD NO es por bajar a 15 min**, sino por refrescar las MV (antes el cron
  no las refrescaba en absoluto). Lo que paga la frecuencia es la parte de ventas, que sí corre 4×/hora.
- **El gasto real del proyecto es Postgres: ~$33/mes** (RAM 3,18 GB constantes = $31,8 + CPU $0,57 +
  disco 7,5 GB = $1,12). El cron es calderilla al lado (~6%); si algún día hay que recortar factura, el
  sitio donde mirar es la memoria de Postgres, no la frecuencia del ETL.
- Si en el futuro se añaden más MV, **el coste crece por el refresco, no por el ETL**. Antes de meter
  una MV nueva en el ciclo de cada 15 min, mirar su `duracion_ms` en `marts.bi_mv_refresh` y decidir si
  le basta con el tick de la hora (como se hizo con las de contabilidad).

## 7. Conciliación / verificación
- **Estado y cuadre:** `python estado_dw.py --odoo` (conteos por año vs Odoo + partida doble).
- **Partida doble:** `SUM(debito) = SUM(credito)` por empresa (debe ≈ 0). Si un período falla →
  `--rebuild` de ese rango (ver receta 2.5).
- **Ventas:** `SUM(venta_neta)` en `v_ventas` (clase 4, sin reversos) vs ingresos de Odoo.
- **Auditoría de `fecha_venta`:** `python diagnosticar_fecha_venta.py` (solo lectura). Comprueba que
  ninguna FACTURA cambie de mes, cuantifica la reubicación de NC mes a mes, lista las NC responsables
  con su `metodo_enlace`, detecta anulaciones totales sin marcar y aísla las **notas débito**. Es el
  primer sitio al que ir cuando un mes "no cuadra" contra el Excel.
- **Ventas vs Excel (base_ventas del pipeline):** `python validar_ventas.py` concilia
  `v_ventas_producto` contra los CSV de `CLEAN DATA`. Para que cuadre hay que **alinear 3 cosas**:
  (1) **combinar empresas** (el Excel no distingue; ene-2026 estaba en empresa 1, luego en la 8),
  (2) por **`fecha_venta`** (la NC resta en el mes de SU factura; ⚠ agrupar por `fecha_factura` sirve
  para comparar contra el Excel pero **reproduce su error**), (3) producto comercial.
  **`es_reverso` = anulación real** (factura + NC de reversión ≥99%), **NO** `payment_state='reversed'`
  (que en este Odoo lo pone el factoring y las NC parciales — ventas reales que sí cuentan). Cuando
  Odoo deja `reversed_entry_id` NULL, la anulación se detecta por el **puente NC**
  (`marcar_reversos_puente`) y también sale de ventas.
  **TOTAL 2026 Excel vs DW = −1,3%** (DW 53.823,9M vs Excel 54.527,7M), explicado documento por
  documento y con todos los meses del mismo lado: **mar −339M (−4,0%)** = facturas **anuladas** que el
  Excel sigue contando (`FE7301` 662,2M + `FE9576`/`FE9570` 278M) menos `NDY1` (+612,9M, que revive
  `FE7281`); **abr −219M (−2,0%)** = ~200M de NC de exportación que el DW sí resta (`RFEX2`);
  **ene −69M** = `NDY14` (113M) se va a dic-2025, el mes de su factura.
- **NOTAS DÉBITO:** no son venta, salvo las que **anulan una nota crédito** (esas cuentan en el mes de la
  factura que reviven, vía `marts.map_nd_factura`). Las excluidas quedan en
  `marts.v_notas_debito_excluidas`. ⚠ La visión CONTABLE (`v_ventas`, `v_balance_comprobacion`,
  `v_exportaciones`) **sí** las lleva: ahí una ND es ingreso.
  **Diferencia mensual principal = NOTAS CRÉDITO.** El Excel ya viene **neto** (el pipeline resta la
  NC dentro de la fila de la factura al agrupar por `NUMERO_FACTURA-PRODUCTO`), por eso no tiene filas
  negativas; pero **solo resta la NC cuyo `ref` casa** con una factura-producto existente y **descarta
  las que no casan**. El DW resta **todas** → queda más bajo y es el correcto. El reporte lo cuantifica
  con `excel_vs_bruto` (≈0 ⇒ el Excel no restó las NC del mes). Ej. jun-2026: el DW resta 213,9M
  (`RFEX2` 200,8M…) que el Excel no restó. El resto es **timing** (CSV viejo vs DW con más facturas).
- **Estados financieros:** `v_balance_comprobacion` (por empresa, con `seccion/subseccion/
  nivel_movimiento`) vs los reportes Balance / Estado de Resultados de Odoo.
- **Calidad:** `v_dq_analitica` debería tender a 0 tras corregir en Odoo.
