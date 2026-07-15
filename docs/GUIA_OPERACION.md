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
- Es el **cron activo** del proyecto (Railway → `run_dw.py`, horario). Reemplazó al antiguo sync raw
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
| `python etl_dw_marts.py --incremental` | Solo cambios por `write_date` (hecho + cartera + dimensiones). Idempotente y rápido. | Actualización normal. **Es lo que corre el cron cada hora**; rara vez hace falta a mano. |
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
`run_dw.py` es el entrypoint del cron de Railway (`railway.toml` → `0 * * * *`):
- **Cada hora:** `--incremental`.
- **Días 3 y 24, 03:00:** además `--rebuild` del año actual.
Para probarlo localmente igual que el cron: `python run_dw.py`.

### 2.6 Mapeos de negocio de ventas (NO-Odoo) — `cargar_mapeos.py`
`python cargar_mapeos.py` lee de Google Drive (vía `DriveLoader`) los Excel de **zonas** (general,
Cundinamarca, Bogotá), **clientes padres** y **categorías**, y recrea las tablas `marts.map_*`
(TRUNCATE + insert). Es el **único insumo NO-Odoo** del DW y se corre **a demanda** (cuando cambie
alguno de esos Excel). Requiere el DDL `sql/marts/16_mapeos_ventas.sql` aplicado.

### 2.7 Recetas rápidas (síntoma → comando)
| Situación | Qué correr |
|---|---|
| "¿Cómo va el DW / cuadra con Odoo?" | `python estado_dw.py --odoo` |
| Cliente/producto/centro de costo nuevo no aparece | `python etl_dw_marts.py --dims` |
| Cambié la clasificación de cuentas (estados financieros) | aplicar el DDL si tocó columnas + `python etl_dw_marts.py --dims` |
| Poblar enriquecimiento de ventas / kits (tel/email/etiqueta/es_kit) | aplicar DDL 15/15b + `python etl_dw_marts.py --dims` |
| Cambió un Excel de zonas / clientes padres / categorías | `python cargar_mapeos.py` |
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
- **Ventas sin reversos:** factura anulada (`payment_state='reversed'`) + su NC → `es_reverso=TRUE`,
  excluidas de ventas. Devoluciones **parciales** (factura `paid`) sí restan vía `venta_neta`
  (`marcar_reversos`).
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
- Detalle de medidas: [MODELO_ESTRELLA.md §9 y §11](MODELO_ESTRELLA.md).

## 6. Programación en Railway (ya montado)
El cron corre `run_dw.py` (`railway.toml` + `Procfile`):
- *Start Command:* `python run_dw.py` · *Cron Schedule:* `0 * * * *`.
- Variables de entorno requeridas: `url, db, username_odoo, password, DB_HOST, DB_PORT, DB_NAME,
  DB_USER, DB_PASSWORD`.
- Al hacer **push a `main`**, Railway redepliega y el próximo tick horario usa el código nuevo.

## 7. Conciliación / verificación
- **Estado y cuadre:** `python estado_dw.py --odoo` (conteos por año vs Odoo + partida doble).
- **Partida doble:** `SUM(debito) = SUM(credito)` por empresa (debe ≈ 0). Si un período falla →
  `--rebuild` de ese rango (ver receta 2.5).
- **Ventas:** `SUM(venta_neta)` en `v_ventas` (clase 4, sin reversos) vs ingresos de Odoo.
- **Estados financieros:** `v_balance_comprobacion` (por empresa, con `seccion/subseccion/
  nivel_movimiento`) vs los reportes Balance / Estado de Resultados de Odoo.
- **Calidad:** `v_dq_analitica` debería tender a 0 tras corregir en Odoo.
