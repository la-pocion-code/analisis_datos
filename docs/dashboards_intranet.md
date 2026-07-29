# Dashboards de la intranet — contrato de datos

Este documento es el **contrato** entre este repo (datos) y el repo de la app
(`proyecto pocion/intranet`, app `apps/dashboards`).

## ⭐ DIRECCIÓN DEL PROYECTO: la intranet reemplaza a Power BI

**Decisión tomada: los tableros dejan de vivir en Power BI y pasan a la INTRANET**,
una **app interna de la compañía**, presentando los reportes como **HTML dinámico**
(gráficos web con ECharts, filtros que consultan la BD en vivo).

Qué implica, y por qué se ha construido lo que se ha construido:

- **Power BI queda como fuente TRANSITORIA**, no como destino. El modelo
  `DASHBOARD POCION` y sus docs (`guia_bi_reporting.md`,
  `bi_conexiones_marts.md`, `bi_refresco_gateway.md`) siguen siendo válidos
  mientras dure la migración, pero **no se invierte en ampliarlos**: lo nuevo se
  hace para la intranet.
- **La lógica de negocio baja al SQL.** Todo lo que hoy es una medida DAX o un
  paso de Power Query tiene que quedar en el DW (vistas, MV, columnas del hecho),
  porque la intranet solo hace `SELECT`. Ejemplos ya portados: la exclusión de
  notas débito (era un filtro DAX), el mes de la nota crédito (`fecha_venta`), el
  cruce ventas vs presupuesto por categoría (era un join manual).
- **Por eso existen las vistas materializadas** (§1): un tablero web consulta en
  vivo en cada carga, mientras Power BI importaba y agregaba en memoria.
- **Por eso existe el rol `intranet_ro`** (§6): la app necesita un acceso de solo
  lectura, mínimo y auditable, en vez de un `.pbix` en el PC de alguien.
- **Sin dependencia de licencias ni de un PC encendido**: desaparecen el techo de
  refrescos de Power BI Pro (8/día) y el gateway. El cron del DW corre cada
  15 min y la intranet ve los datos frescos sin intermediarios.

> **Regla del proyecto**: todo lo de base de datos (DDL, vistas materializadas,
> roles, refresco) vive **aquí** y se documenta **aquí**. La intranet solo hace
> `SELECT`. Los dos repos no se mezclan.

---

## 1. Por qué existen vistas materializadas

Power BI **importa** los datos y los agrega en memoria. Un dashboard web
consulta **en vivo** cada vez que alguien abre la página. Y `marts.v_ventas_bi`
es una vista sobre `v_ventas_explotada` (window functions) sobre
`v_ventas_producto` (7 joins): se reconstruye entera —910k filas— en **cada**
consulta.

Medido en producción el **2026-07-28** (~910.423 filas):

| Consulta | Sobre las vistas | Sobre las MV | Mejora |
|---|---|---|---|
| Ventas por mes del año en curso | **6.892 ms** | 318 ms | 22× |
| Top 10 clientes del año | **8.277 ms** | 712 ms | 12× |
| Serie diaria del mes | — | 208 ms | — |
| Ventas vs presupuesto 2026 | — | 273 ms | — |
| Top 10 productos del año | — | 357 ms | — |

*(Los tiempos "después" incluyen ~200 ms de latencia de red del proxy público de
Railway desde un portátil; el trabajo real de base de datos es bastante menor.)*

Con 5–6 paneles por página, el escenario "antes" son ~40 s de CPU de base de
datos **por cada usuario que entra al tablero** — exactamente lo que tumbaría el
servidor.

---

## 2. Objetos expuestos (fase 1 — hoja de Ventas)

DDL: [`sql/marts/23_mv_dashboards.sql`](../sql/marts/23_mv_dashboards.sql).
Permisos: [`sql/marts/24_rol_intranet.sql`](../sql/marts/24_rol_intranet.sql).

### Vistas materializadas

| Objeto | Grano | Filas | Para qué |
|---|---|---|---|
| `marts.mv_ventas_dia` | fecha × empresa × cliente × vendedor × categoría × país × equipo | 176.979 | series temporales (evolución diaria, MTD, acumulados) |
| `marts.mv_ventas_mes` | mes × empresa × cliente × vendedor × **producto** × categoría × país × equipo | 851.515 | desgloses y top-N (cliente, producto, vendedor, categoría, país) |
| `marts.mv_ventas_kpi_mes` | mes × empresa × categoría | 296 | conteos **distintos**: facturas, clientes, líneas |
| `marts.mv_presupuesto_mes` | mes × cliente × canal × zona × ejecutiva × **nivel** | 347 | presupuesto comercial tipado |
| `marts.mv_ventas_presupuesto_mes` | mes × **categoría** | 360 | **ventas vs presupuesto** (cumplimiento) |

Medidas en las tres primeras: `venta` = `SUM(venta_componente)`,
`unidades` = `SUM(cantidad_componente)`.

### ⭐ Filtrar por categoría cruzando presupuesto y ventas

La categoría del presupuesto es **`canal`**, ⚠ **NO `categoria_cliente`** (esa columna es el **NIVEL**
del cliente — `DIAMOND`/`SILVER`/`GOLD` — y viene vacía en 302 de las 347 filas; se conserva con ese
nombre para no romper nada, pero no es una categoría).

`mv_presupuesto_mes` expone ahora **`categoria`** = `canal` normalizado con `marts.map_categoria`, o sea
**el mismo vocabulario que `mv_ventas_*.categoria`**. Con eso los dos lados son directamente
comparables y el filtro por categoría es único para todo el tablero.

`marts.mv_ventas_presupuesto_mes` ya trae el cruce hecho:
`periodo_aaaamm, anio, mes, categoria, venta, presupuesto, presupuesto_con_iva, cumplimiento_pct, falta`.
Es un **`FULL OUTER JOIN`**: una categoría con presupuesto y sin ventas (o al revés) **sigue
apareciendo** — es justo lo que el negocio necesita ver. `cumplimiento_pct` y `falta` son `NULL` cuando
no hay presupuesto (p. ej. 2024-2025, o la categoría `Proveedores`).

⚠ Tres asimetrías al leerla: el presupuesto es **solo 2026**; **no tiene empresa** ⇒ `venta` suma las
DOS (HFA + PCN) y no se puede filtrar por empresa aquí; y `venta` está atribuida por **`fecha_venta`**
⇒ esta MV **no** admite `?date_basis=factura`.

### Vistas de lookup (nombres descriptivos)

| Objeto | Contenido |
|---|---|
| `marts.v_lk_tercero` | `tercero_id, nombre, tipo_cliente, ciudad, departamento, pais, cliente_padre` |
| `marts.v_lk_producto` | `producto_id, codigo, nombre, nombre_comercial, etiqueta, categoria, es_kit` |
| `marts.v_lk_vendedor` | `vendedor_id, nombre` |
| `marts.v_lk_empresa` | `empresa_id, nombre` |

`v_lk_tercero` **excluye a propósito** `identificacion` (NIT), `telefono`,
`email` y `etiqueta`: un tablero de ventas no necesita datos personales de
208.802 terceros. `v_lk_producto.etiqueta` = `nombre_comercial` si existe, si no
el nombre técnico — es lo que se pinta en los gráficos.

### Bitácora

`marts.bi_mv_refresh` (`mv_name`, `refreshed_at`, `filas`, `duracion_ms`, `ok`,
`error`). La intranet la lee para dos cosas: **invalidar su caché Redis**
(`MAX(refreshed_at)` como versión) y mostrar *"datos actualizados hace X"*.

---

## 3. Reglas de uso (importantes)

1. **El valor siempre se suma con `venta`** (deriva de `venta_componente`).
   ⚠ **Nunca** usar `cantidad_neta` del origen: es de nivel **KIT** y se repite
   en cada fila de componente. Inflaría ~30% (42.810 M correcto vs 55.753 M
   inflado, empresa 8, 2026). En las MV ya no está expuesta: usar `unidades`.
2. **`facturas` NO es aditivo.** Es un `COUNT(DISTINCT factura_id)` al grano
   exacto de cada MV; sumarlo al agrupar más grueso da un número inflado. Para
   conteos por mes usar `mv_ventas_kpi_mes`, que los calcula a su propio grano.
3. **⚠ HAY DOS FECHAS Y EL INFORME USA `fecha_factura`.** Esto es lo más fácil de
   equivocar de toda la hoja.

   | Atribución | Columnas en las MV | Significado |
   |---|---|---|
   | **`fecha_factura`** (por defecto) | `fecha_factura`, `periodo_factura_aaaamm`, `anio_factura`, `mes_factura` | mes de emisión del documento |
   | `fecha_venta` | `fecha`, `periodo_aaaamm`, `anio`, `mes` | la NC resta en el mes de **su factura original** (`19_nc_factura.sql`) |

   `docs/guia_bi_ventas.md` dice que la relación activa con el calendario es
   `fecha_venta`, pero **en el modelo real de Power BI la activa es
   `fecha_factura`** (la de `fecha_venta` está marcada inactiva). O sea: todo lo
   que el negocio ve hoy está atribuido por **fecha de factura**.

   Comprobado contra el informe el 2026-07-28 — por `fecha_factura` los meses de
   2026 cuadran **al peso**:

   | Mes | Informe Power BI | Por `fecha_factura` | Por `fecha_venta` |
   |---|---|---|---|
   | ene | 7.943.994.410 | **7.943.994.410** ✓ | 7.892.709.068 |
   | feb | 7.939.146.002 | **7.939.146.002** ✓ | 7.936.936.571 |
   | mar | 7.870.065.104 | **7.870.065.104** ✓ | 7.297.035.216 |
   | abr | 8.986.842.524 | **8.986.842.524** ✓ | 9.639.825.116 |
   | may | 7.163.682.804 | **7.163.682.804** ✓ | 7.199.544.615 |
   | jun | 7.162.634.966 | **7.162.634.966** ✓ | 7.173.102.550 |

   La diferencia llega al **7 % en un mes** (marzo) aunque el total anual sea casi
   igual. Por eso el grano de las MV incluye **las dos** fechas —cuesta +0,04 %
   de filas, porque solo 952 de 910.802 líneas tienen fechas distintas— y la
   intranet expone `?date_basis=factura|venta` (por defecto `factura`).

   Si alguna fecha llegara nula, las columnas de factura hacen
   `COALESCE(fecha_factura, fecha_venta)`: así una línea nunca desaparece —con su
   valor— en silencio. Hoy no hay nulos en ninguna de las dos.
4. **Centinelas en vez de NULL.** Los ids nulos van a `-1` y los textos a
   `'(sin categoria)'`, `'(sin pais)'`, `'(sin equipo)'`. Es requisito del índice
   único que exige `REFRESH ... CONCURRENTLY` (en un índice único los NULL se
   consideran distintos entre sí, así que no garantizarían unicidad). Un `-1`
   simplemente no casa en el `LEFT JOIN` con el lookup ⇒ la intranet lo pinta
   como "(sin …)".
5. **`linea_id` no es único** en el origen (NC prorrateadas + kits explotados).
   En `mv_ventas_kpi_mes`, `COUNT(DISTINCT linea_id)` sí da las líneas reales de
   factura (para "promedio de ítems por factura").

## 4. Limitaciones de los datos de origen

Afectan a lo que la hoja de Ventas puede mostrar y hay que tenerlas presentes al
diseñar los gráficos:

- **Presupuesto solo de 2026** (`2026-01-01` … `2026-12-02`) ⇒ no se puede
  comparar presupuesto de años anteriores.
- **El presupuesto no tiene columna de empresa** ⇒ no se puede separar por
  empresa (HFA / PCN Poción). Al comparar contra ventas hay que sumar las dos
  empresas o documentar el supuesto.
- 10 de 348 filas de presupuesto no traen importe; 1 no trae fecha (se excluye).
- `bi_presupuesto.unnamed_8` está **100% vacía** y `unnamed_9` no se usa (igual
  que en el modelo de Power BI) ⇒ no se exponen.
- **Las ventas empiezan el 2024-06-01** ⇒ el YoY 2026 vs 2025 es completo, el
  2025 vs 2024 es parcial.
- Todas las tablas `marts.bi_*` son **`VARCHAR(512)`** (el auto-DDL de
  `DBLoader` deriva los tipos del Excel y todo llega como texto). Las MV las
  tipan una sola vez. Casts verificados contra los datos reales: 0 valores que no
  parseen. **Si cambia el Excel de origen, revalidar** antes de refrescar.

## 5. Refresco

Módulo: [`refrescar_mv_dashboards.py`](../refrescar_mv_dashboards.py).

Lo llama **`run_dw.py`** al final de **cada corrida del cron, que ahora es cada 15
minutos** (`*/15 * * * *`), después del incremental **y** del rebuild para que
recoja ambos. Envuelto en `try/except`: si un tablero no se puede refrescar, el ETL
igual termina bien y el fallo queda registrado en `marts.bi_mv_refresh`.

⚠ Consecuencia para la caché de la intranet: `MAX(refreshed_at)` cambia **4× más
seguido**, así que la caché se invalida cada 15 min en vez de cada hora.

⚠ Frescura desigual dentro de la hora: en los ticks `:15/:30/:45` el ETL corre en
modo **ligero** (solo dimensiones + hecho nuevo). Las líneas cargadas en esos ticks
llegan **sin `categoria`, sin `es_reverso` y sin el puente NC/ND resuelto** hasta el
tick `:00`. En la práctica: una factura de los últimos ≤45 min puede aparecer en
`'(sin categoria)'`, y una nota crédito recién emitida puede restar en su propio mes
hasta el cierre de la hora.

```bash
python refrescar_mv_dashboards.py                    # todas
python refrescar_mv_dashboards.py --mv mv_ventas_dia # una sola
python refrescar_mv_dashboards.py --no-concurrente   # si alguna nunca se pobló
```

Duraciones medidas (2026-07-29): `mv_presupuesto_mes` 0,2 s ·
`mv_ventas_kpi_mes` 11,4 s · `mv_ventas_dia` 15,7 s · `mv_ventas_mes` 23,4 s ·
`mv_ventas_presupuesto_mes` 0,3 s → **~53 s en total**. Con una corrida ligera de
~55 s, un tick de 15 min cierra en <2 min: holgado.

⚠ `mv_ventas_presupuesto_mes` se refresca **siempre al final**: lee de
`mv_ventas_mes` y de `mv_presupuesto_mes`, así que si se adelanta mostraría el cruce
contra datos viejos.

Dos detalles que importan:

1. `REFRESH MATERIALIZED VIEW CONCURRENTLY` **no puede correr dentro de una
   transacción** → la conexión va en `autocommit`. `CONCURRENTLY` es lo que
   permite que la intranet siga leyendo mientras se refresca; sin él la vista
   queda bloqueada y los tableros se congelan.
2. `CONCURRENTLY` exige que la vista **ya esté poblada** y tenga índice único. Si
   falla por eso, el módulo reintenta automáticamente sin `CONCURRENTLY`.

## 6. Conexión desde la intranet

Rol **`intranet_ro`** (`LOGIN`, sin privilegios por defecto). Verificado el
2026-07-28: lee los 9 objetos de arriba y recibe `permission denied` en
`fact_movimiento_contable`, `dim_cuenta`, `dim_tercero`, `v_ventas_bi`,
`v_cartera`, `bi_nielsen`, y en cualquier intento de `CREATE`, `INSERT` o
`REFRESH`.

La contraseña **no está en el repo**. Se asigna aparte:

```sql
ALTER ROLE intranet_ro PASSWORD '<generada>';
```

y se pone en la variable del servicio de la intranet en Railway:

```
MARTS_DATABASE_URL=postgresql://intranet_ro:<pass>@<host>:<puerto>/railway?sslmode=require
```

## 7. Pendiente / fases siguientes

Cada hoja nueva añade sus MV aquí y su `GRANT` en `24_rol_intranet.sql`
(**nunca** sobre las tablas base):

- **Nielsen** — MV sobre `bi_nielsen` (573k filas, todo `VARCHAR`: hay que tipar
  `vtas_valor`, `vtas_unds`, `dist_num` y parsear la fecha desde `periods`).
- **Cuentas clave / KAM** — MV sobre `bi_cuentas_clave_ventas`,
  `bi_inventario_cclave`, `bi_tiendas_cclave`.
- **Cartera** — portar a SQL los buckets de mora que hoy calcula Power Query
  (`DIAS ATRASO`, `RANGO MORA`: Corriente/Próximo/11-30/31-60/61-90/90+).
- **Contabilidad** — la más grande. Antes hay que portar a SQL las **columnas
  calculadas DAX** de `dim_cuenta` (`concepto_contable`, `concepto_balance`,
  `categoria_gasto`, `flujo_actividad`, `orden_*`): son `CASE` sobre código PUC
  (`clase_codigo`/`grupo_codigo`/`cuenta_codigo`). Después `mv_pyg_mes` y
  `mv_balance_mes` sobre `v_balance_comprobacion`, y el cruce
  `bi_presupuesto` × `bi_cliente_credito` (días de CxC → `meses_desplazamiento`,
  `anticipo`) que alimenta la proyección de flujo de caja.

Reutilizable: `reportes-api/reports.py` ya tiene SQL de `estado_resultados`,
`balance` acumulado, `top_clientes` y `ventas_por_categoria`.

## 8. Nota

El one-liner de `docs/GUIA_OPERACION.md` §2.3 para aplicar DDL está
**desactualizado**: `DBLoader.get_connection()` es un *context manager*, así que
`DBLoader().get_connection().cursor()` falla con
`'_GeneratorContextManager' object has no attribute 'cursor'`. Hay que usar
`with`:

```python
python -c "import sys; sys.path.insert(0,'.'); from classes.db_loader import DBLoader; \
sql=open('sql/marts/23_mv_dashboards.sql',encoding='utf-8').read(); \
c=DBLoader().get_connection(); \
conn=c.__enter__(); cur=conn.cursor(); cur.execute(sql); conn.commit(); c.__exit__(None,None,None); \
print('aplicado')"
```
