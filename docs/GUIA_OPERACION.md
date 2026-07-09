# Guía de operación — Data Warehouse La Poción (esquema `marts`)

Cómo funciona y cómo operar el modelo estrella que alimenta Power BI. Diseño detallado del
modelo en [MODELO_ESTRELLA.md](MODELO_ESTRELLA.md); contexto general del repo en
[ARQUITECTURA_DW.md](ARQUITECTURA_DW.md).

## 1. Qué es y cómo fluye
```
Odoo (XML-RPC)                          PostgreSQL (Railway)
  account.move.line  ──┐
  account.move         │   etl_dw_marts.py         ┌─ marts.fact_movimiento_contable (ÚNICO hecho, líneas)
  account.account      ├──►  (por lotes, UPSERT) ──┤─ marts.dim_* (8 dimensiones)
  res.partner/product  │                           └─ vistas v_ventas / v_cartera / v_dq_analitica
  analytic.account…  ──┘
```
- Es el **cron activo** del proyecto (Railway → `run_dw.py`, horario). Reemplazó al antiguo sync raw
  `etl_odoo_incremental.py` (archivado). Lee de Odoo directo; no depende de `raw.odoo_apuntes`.
- Grano: **una línea de asiento** (`account.move.line`, `state='posted'`).
- **Un solo hecho** sirve ventas y cartera (en BI se filtra con DAX; no se duplican tablas).
  Cartera = líneas de CxC (`es_cxc`) con su `saldo_pendiente` (residual por línea).

## 2. Cómo se ejecuta (`etl_dw_marts.py`)
| Comando | Qué hace | Cuándo |
|---|---|---|
| `python etl_dw_marts.py --full` | Carga histórica completa (hecho + cartera + dims full). Sin truncar. | Primera población / recarga total |
| `python etl_dw_marts.py --incremental` | Solo cambios por `write_date` (hecho, cartera y dimensiones). Idempotente. | Frecuente (cada hora) |
| `python etl_dw_marts.py --rebuild [--desde AAAA-MM-DD]` | **DELETE del rango + recarga**. Por defecto **solo el año actual** (años cerrados intactos). Refleja **borrados** de Odoo. | ~2×/mes + manual |
| `python etl_dw_marts.py --dims` | Solo refresca catálogos y dimensiones (sin hechos). Rápido. | Cuando cambian clientes/productos sin factura nueva |

- **Marcas de agua** en `marts.etl_control` (una por modelo Odoo): `account.move.line`,
  `account.move`, `res.partner`, `product.product`, `res.users`. El incremental pide a Odoo solo
  `write_date > marca` y guarda el nuevo máximo.
- **Carga por lotes** (páginas de 5.000 líneas) → memoria estable con millones de filas.
- **En Railway (automático):** el cron corre **`run_dw.py`** (`railway.toml` → `0 * * * *`), que
  hace `--incremental` cada hora y `--rebuild` del año actual los días 3 y 24 a las 03h. Los comandos
  de la tabla son para ejecución manual.

## 3. Refresco de dimensiones
- **Cada corrida** refresca en full los catálogos pequeños: `dim_cuenta`, `dim_diario`,
  `dim_centro_costo` (**centros de costo nuevos** ✓) y `dim_empresa`.
- **`dim_tercero`, `dim_producto`, `dim_vendedor`** se refrescan por su propio `write_date`
  (clientes/productos/vendedores **nuevos o modificados**, aunque no tengan transacción nueva).
  En `--full`/`--rebuild` el refresco es total; en `--incremental`/`--dims`, solo cambios.
- `tipo_cliente` (de `partner_type_id` del asiento) no se pisa al refrescar el tercero.

## 4. Reglas de negocio
- **Ventas sin reversos:** una factura anulada (`payment_state='reversed'`) y su nota crédito de
  reverso se marcan `es_reverso=TRUE` y **se excluyen** de ventas (el par suma 0). Las
  **devoluciones parciales** (factura `paid`) **sí restan** vía `venta_neta`. Recalculado en cada
  corrida (`marcar_reversos`).
- **Cartera:** en el mismo hecho, líneas `es_cxc` (`account_type='asset_receivable'`) con
  `saldo_pendiente = amount_residual` (por línea) y `fecha_vencimiento_key` para aging.
  `v_cartera` = líneas `es_cxc` con saldo ≠ 0. No hay tabla de cartera aparte.
- **Calidad (`v_dq_analitica`):** líneas con algún reparto analítico ≠ 100% (error a corregir en
  Odoo) y líneas de gasto/costo (clases 5/6) sin centro de costo.
- **Correcciones (`marts.correcciones`):** overrides de datos mal registrados en Odoo, aplicados
  en el DW tras cargar (`aplicar_correcciones`), sin tocar Odoo. Formato: `tabla, pk_col, pk_val,
  campo, valor_nuevo, motivo, activo`.

## 5. Consumo en Power BI
- Conectar a PostgreSQL (variables `DB_*`), **modo Import**. Importar las 8 dimensiones +
  **`fact_movimiento_contable`** (único hecho). Relaciones estrella por los `*_id` y `fecha_key`.
  Ventas y cartera se calculan con medidas DAX sobre ese hecho (sin duplicar tablas).
- Medidas DAX (esbozo): ver [MODELO_ESTRELLA.md §9](MODELO_ESTRELLA.md). Ventas y cartera se
  calculan con DAX (no se duplican tablas).

## 6. Programación en Railway (servicio nuevo)
- Se ejecuta vía `run_dw.py` (dispatcher): cada hora corre `--incremental`; los días **3 y 24 a
  las 03:00** corre además `--rebuild` (año actual).
- **Setup** (sin tocar el cron existente): crear un **servicio nuevo** en Railway sobre este mismo
  repo con:
  - *Start Command:* `python run_dw.py`
  - *Cron Schedule:* `0 * * * *`
  - Variables de entorno: `url, db, username_odoo, password, DB_HOST, DB_PORT, DB_NAME,
    DB_USER, DB_PASSWORD`.

## 7. Puesta en marcha (orden)
1. Aplicar DDL (una vez): `sql/marts/01_star_schema.sql`, `02_vistas.sql`, `03_control.sql`,
   `04_empresa_cartera.sql`, `05_calidad_correcciones.sql`. Poblar `dim_fecha` (bloque en 01).
2. Carga inicial: `python etl_dw_marts.py --full`.
3. Conectar Power BI (sección 5).
4. Programar el servicio en Railway (sección 6).

## 8. Conciliación / verificación
- Partida doble: por `factura_id`, `SUM(debito)=SUM(credito)`.
- Ventas: `SUM(venta_neta)` en `v_ventas` (clase 4, sin reversos) vs ingresos de Odoo.
- Cartera: `SUM(saldo_pendiente)` de `v_cartera` vs CxC de Odoo.
- Calidad: `v_dq_analitica` debería tender a 0 tras corregir en Odoo.
