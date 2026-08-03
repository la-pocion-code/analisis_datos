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
   de filas, porque solo 952 de 910.802 líneas tienen fechas distintas—.

   ⚠ **CORREGIDO 2026-07-29**: la intranet **ya NO expone `?date_basis=`**. Por
   decisión de William («para todo se usará `fecha_venta`, que es la columna que se
   ajustó») los tableros de ventas usan **siempre `fecha_venta`**, y el endpoint
   devuelve **400** si llega ese parámetro (commit `1bb9628` de la intranet). Las
   columnas de factura se conservan en el grano para poder auditar, pero ningún
   tablero las consulta. **La contabilidad es otra base**: se atribuye por **fecha
   contable** (`fecha_key`), ver §9.

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
`mv_ventas_presupuesto_mes` 0,3 s → **~53 s en total** (medido desde una máquina
local; el `REFRESH` es trabajo del servidor, así que en Railway es parecido). Con
el ETL en **~26 s** en Railway, un tick de 15 min cierra en ~1,5 min: holgado.

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
- ~~**Cuentas clave / KAM**~~ — **hecha** el 2026-07-31: ver §12.
- **Cartera** — portar a SQL los buckets de mora que hoy calcula Power Query
  (`DIAS ATRASO`, `RANGO MORA`: Corriente/Próximo/11-30/31-60/61-90/90+).
(La hoja de **Contabilidad** ya está construida: ver §9.)

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

---

## 9. Fase 2 — hoja de CONTABILIDAD

DDL en `sql/marts/26_contabilidad_dashboards.sql`; permisos en `24_rol_intranet.sql`
(que hay que **re-ejecutar después** del 26, aunque numéricamente vaya antes).

Sustituye la hoja «Informe Contabilidad» del PBIX, que son **seis** sub-páginas:
PYG · Situación Financiera · Flujo de Efectivo · Comportamientos · Detalle · KPIs.

### 9.1 Lo que se midió antes de escribir el DDL

| Dato | Valor |
|---|---|
| Rango contable | 2023-12-31 → 2026-08-09 (**33 meses**) |
| Líneas del hecho | 4.375.278 |
| `SUM(debito) - SUM(credito)` | **−0,01** ⇒ partida doble cuadrada y **historia completa** |
| Asiento de apertura | 2023-12-31, 681 líneas (empresa 1). La empresa 8 arranca 2025-12-01 |
| Cuentas clases 1/2/3 · 4/5/6/7 · 8/9 | 960 · 938 · 48 (**las de orden, sin un solo movimiento**) |
| Cuentas sin `codigo` en clases 1-7 | **0** ⇒ no hay fallback a `nivel_movimiento` |
| Terceros con movimiento contable | 137.612 |
| Centros de costo con movimiento | 62 de 67 |
| Cuentas usadas por 1 sola empresa / por las 2 | **923 / 1** |

El cuadre de −0,01 es lo que hace legítimo calcular `saldo_acum`: sin el asiento de
apertura, todos los saldos estarían desplazados por una constante y el balance
**seguiría cuadrando** —el error sería simétrico e invisible—.

### 9.2 Objetos expuestos

| Objeto | Grano | Filas | Para qué |
|---|---|---|---|
| `v_dim_cuenta_bi` | cuenta (1.945) | — | las 14 columnas que en Power BI son DAX |
| `mv_contab_cuenta_mes` | empresa × mes × cuenta | 9.366 | **base**: la única que escanea el hecho |
| `mv_balance_mes` | empresa × mes × cuenta, clases 1/2/3, **densa** | 14.949 | situación financiera (4 niveles) |
| `mv_pyg_mes` | empresa × mes × concepto × **cuenta_codigo (N4)** | 972 | estado de resultados y KPIs |
| `mv_flujo_mes` | empresa × mes × renglón de flujo | 351 | flujo de efectivo (solo lo agregable) |
| `mv_contab_tercero_mes` | empresa × mes × tercero, **pivotada** | 250.526 | comportamientos y detalle |
| `mv_contab_centro_mes` | empresa × mes × centro (+ `plan`) | 564 | comportamiento por centro |
| `mv_contab_canal_mes` | empresa × mes × categoría × canal | 611 | comportamiento por canal |
| `bi_pyg_renglon` | renglón | 17 | catálogo y **orden decimal** de los renglones derivados |
| `bi_tasa_renta` | empresa | 2 | 39 % HFA / 35 % PCN |
| `v_lk_cuenta` | cuenta | — | lookup del PUC con la clasificación derivada |

Aplicar el DDL entero tarda **12,7 s**. El hecho contable y `dim_cuenta` **siguen
negados** al rol: verificado con `has_table_privilege`.

### 9.3 Las reglas exactas (verificadas al peso contra el informe)

PCN (empresa 8), mayo-2026 — lo que devuelve `mv_pyg_mes` coincide **exactamente**:
ingresos operacionales `6.830.236.960` · costo de ventas `2.801.095.140` · gastos
admin `404.721.946` · gastos de ventas `2.651.211.097` · D&A línea `3.132.892` · D&A
total `6.469.192` · ingresos no op. `84.808.759` · gastos no op. `80.724.548`.
Derivados: UB `4.029.141.820` · UO `970.075.885` · EBITDA `976.545.077` · UAI
`974.160.096` · provisión al 35 % `340.956.034` (diferencias de 1 peso por redondeo).

⚠ **Las dos reglas de gasto NO son simétricas** — es el error más fácil de cometer:

| Medida | Definición |
|---|---|
| gastos de administración | grupo **51 EXCLUYENDO** `cuenta_codigo` 5160/5165 |
| gastos de ventas | grupo **52 COMPLETO** (sí incluye 5260/5265) |
| D&A (renglón del informe) | 5160, 5165 |
| D&A total (addback del EBITDA) | 5160, 5165, **5260, 5265** |

Por eso `mv_pyg_mes` lleva el **N4 en el grano**: agregando solo por grupo, 5160/5165
caen dentro del 51 y EBITDA, utilidad operativa y la línea de D&A dejan de ser
calculables — y no se puede recuperar después.

Signo de presentación: `clase_codigo IN ('4','2','3') → −1`. Así ingresos y gastos
salen ambos positivos y utilidad bruta = ingresos − costo.

**Base de fecha**: los estados financieros van por **fecha contable** (`fecha_key`),
no por `fecha_factura` ni `fecha_venta`. Consecuencia: «ingresos por cliente» de esta
hoja **NO cuadra** con `venta` del tablero de Ventas — aquí no se excluyen reversos ni
notas débito, porque contablemente son movimientos reales.

### 9.4 Empresa única y obligatoria

Los estados financieros **nunca se consolidan**. Cinco motivos acumulativos:

1. PUC distinto: 923 cuentas las usa una sola empresa, solo 1 las dos.
2. HFA no tiene grupo 51 — todo su gasto operativo va al 52. Consolidado, la fila
   «gastos de administración» sería la de PCN sola.
3. `seccion`/`concepto`/`nivel_movimiento` (de los reportes Odoo) solo están poblados
   para la empresa 8.
4. **Intercompañía**: en el informe, el 4.º proveedor de PCN es la propia empresa 1
   con 714 mill. Un consolidado lo duplicaría y nada aquí puede detectarlo.
5. Las tasas de renta difieren (39 % / 35 %).

`empresa_id` es la primera columna de todos los índices y la intranet rechaza una
consulta sin empresa.

⚠ **Enero-2026 no es un hueco de datos**: ese mes se facturó en la **empresa 1**
(clase 4 = 7.535.406.985) y PCN solo tuvo 33.898.521; desde febrero manda PCN. Con el
filtro de empresa única, enero se ve casi vacío para PCN y hay que explicarlo en la UI.

### 9.5 Tres cálculos del informe que están MAL y aquí se corrigen

Decisión de William (2026-07-29): corregir, aunque esas páginas dejen de cuadrar con
Power BI.

1. **Situación Financiera** mostraba el **movimiento del mes**, no el saldo, y
   arrastraba dentro las cuentas de resultado: la fila en blanco de encima de ACTIVO
   (−974.160.096,79 en mayo) es *idéntica* a `UTILIDAD ANTES DE IMPUESTOS` del PYG, y
   por eso la fila `Total` daba 0,00 — era la partida doble completa (clases 1-7), no
   un cuadre de balance. Correcto: `saldo_presentacion` (acumulado, con signo),
   filtrado a clases 1/2/3, y el resultado del ejercicio como renglón de patrimonio.
   **Verificado**: `ACTIVO 27.145.470.011 = PASIVO 13.469.553.885 + PATRIMONIO
   4.938.368.827 + resultado 8.737.547.300`, diferencia **−1 peso**.
2. **Flujo de Efectivo** estaba roto: «ACTIVIDADES DE FINANCIACIÓN» traía
   85.188.799,95, *exactamente* el mismo valor que «ACTIVIDADES DE INVERSIÓN», cuando
   las obligaciones financieras del mes eran −1.702.189.935,80. Y la columna `Total` de
   «FLUJO DE CAJA INICIAL» era el valor de un mes, no una suma. `mv_flujo_mes` expone
   solo los renglones **agregables** con el signo ya resuelto (un aumento de activo
   consume caja); los derivados y los de stock los arma la intranet.
3. **KPIs** daba DIO y DSO en **días negativos**, por dividir por un Δ en vez de por
   el saldo acumulado. Con `mv_balance_mes.saldo_acum` sale bien.

### 9.6 Refresco

`refrescar_mv_dashboards.py` separa `MVS_VENTAS` (cada tick) de `MVS_CONTAB` (**solo
el tick `:00`**, cuando `run_dw` corre en modo completo). Las contables son de grano
mensual y salen de asientos, no de facturas al minuto: 15 minutos de frescura no
aportan nada, y en los ticks ligeros las líneas nuevas llegan aún **sin `categoria`**,
así que el panel de canales mostraría un bucket `(sin categoria)` que se vacía al
cierre de cada hora.

**Duraciones medidas** (2026-07-29, refresco `CONCURRENTLY` de las siete):
`mv_contab_cuenta_mes` 4,1 s · `mv_contab_tercero_mes` 6,7 s · `mv_contab_centro_mes` 2,5 s ·
`mv_contab_canal_mes` 1,4 s · `mv_balance_mes` 0,5 s · `mv_pyg_mes` 0,2 s · `mv_flujo_mes` 0,2 s
→ **15,6 s en total**. Sumado a los ~53 s de las de ventas, el tick `:00` sigue con margen de sobra
(una corrida completa del ETL tarda ~26 s en Railway).

⚠ **El orden de `MVS_CONTAB` no es negociable**: `mv_contab_cuenta_mes` va primera y
las tres siguientes derivan de ella. Al revés servirían los datos del refresco
anterior con un `refreshed_at` nuevo — la intranet invalidaría su caché y mostraría
datos viejos como frescos, sin que nada lo delate.

### 9.7 Lo que queda fuera del v1, y por qué

- **Desperdicio de materia prima** (KPI del informe): **no es derivable** del DW. El
  hecho es contable puro y no se extraen `stock.move` ni datos de manufactura. En
  Power BI da 0,00 en los 6 meses, o sea que allí también está vacío.
- **Intensidad de anticipos a proveedores** y **anticipos de clientes / ventas**:
  faltan los códigos PUC exactos. También dan 0,00 hoy. Cuando el contador los
  indique, salen de `mv_balance_mes` sin MV nueva.
- **Consolidado entre empresas**: requiere eliminación de intercompañía explícita.

## 10. Fase 3 — hoja de VENTAS completa (2026-07-30)

DDL: `sql/marts/27_ventas_dashboards_fase2.sql`. GRANTs y los dos lookups
ampliados: `sql/marts/24_rol_intranet.sql` (**re-ejecutarlo después del 27**).

La fase 1 (§2) cubrió solo el **Resumen**. Esta fase añade las 9 sub-páginas
restantes del informe de Power BI más una nueva que allí no existe.

### 10.1 Qué faltaba y de dónde sale

| Hoja del informe | Lo que faltaba | Dónde está ahora |
|---|---|---|
| Facturación (diario) | nada | `mv_ventas_dia` (ya existía) |
| Clientes | nada | `mv_ventas_mes` + `mv_ventas_kpi_mes` + `v_lk_producto` |
| Canal | nada | `mv_ventas_mes` por `categoria` |
| **Página web** | nada — es el Resumen filtrado | `categoria = 'SHOPIFY'` |
| **Mayoristas** | la **zona** | `v_lk_tercero.zona` (nueva) |
| **Línea y categoría** | **línea** y **categoría comercial** | `v_lk_producto.linea` / `.linea_categoria` (nuevas) |
| **Kits** | unidades a nivel de kit | `mv_ventas_kit_mes` (nueva) |
| **Productos** | fecha de lanzamiento y ciclo de vida | `bi_producto_lanzamiento` + `bi_ciclo_vida` (semillas) |
| Nuevos vs recurrentes | primera compra por cliente | `mv_ventas_cliente_primera` (nueva) |
| Tasa de recompra | conteo de clientes con ≥2 facturas | `mv_ventas_recompra` (nueva) |

### 10.2 Mediciones (2026-07-29/30, solo lectura)

```
SHOPIFY 2026 ................ 6.774.546.547 = 12,53 %   ← «Pagina Web» del informe: $6,75 mil M ✔
presupuesto por zona jun-26 .. 2.400.000.001            ← total del informe, exacto ✔
  ANTIOQUIA 756.089.709 · OCCIDENTE 730.133.981 · CENTRO 471.282.251 · COSTA Y ORIENTE 442.494.060
zona: cobertura del canal .... 100 % de MAYORISTA NV (las 4 zonas suman su total)
bi_lineas por CÓDIGO ......... 35/35 filas, 94,43 % del valor 2026
bi_lineas por NOMBRE ......... 16/35 filas, 39,90 %      ← el error fácil
kits 2024/2025/2026 .......... 26.474 / 47.407 / 32.851 unidades de kit
  las mismas desde la explotada .. 139.370 en 2026       ← INFLADO ×4,2
recompra 2026 total / SHOPIFY . 14,07 % / 12,87 %        ← informe: 13,14 % en Shopify
primera venta por producto ..... 2024-06 → 2026-06 (38 productos)
```

Cifras de **2025 (año cerrado)**, estables para fijar en tests:

```
venta 2025 ......... 82.417.391.917      unidades 2025 .... 4.044.256
por línea .......... TRADICIONAL 31.410.435.978 · TONGOLÉ 23.447.325.187 ·
                     POCION PLUS 10.686.380.776 · DUTONIC 5.698.663.004 · B8 3.108.030.225
kits 2025 .......... 47.407 unidades · 6.269.015.175 (idéntico por las dos vías)
```

⚠ **No fijar cifras de 2026 en un test**: el ETL corre cada 15 minutos y el año en
curso se mueve. Entre dos mediciones del mismo día MAYORISTA NV pasó de
18.126.483.426 a 18.135.911.362 y los kits de 32.851 a 32.904. Para tests, usar
2025 o invariantes estructurales («las 4 zonas suman el total del canal»).

### 10.3 Reglas exactas (las que es fácil equivocar)

- **`bi_lineas` se une por el CÓDIGO de los corchetes**, no por el nombre:
  `upper(btrim(substring(bl.producto FROM '\[(.*?)\]'))) = upper(btrim(p.codigo))`.
  El nombre del Excel no coincide letra a letra con `dim_producto.nombre`.
- **Los 5 productos sin línea son reales, no un fallo del join**: PCN32, PCN33,
  PCN34, PCN35 y PCN36 (CONTROL CASPA y ANTICAÍDA), $3.009 M = 5,57 % de 2026.
  **Faltan en `LINEAS Y CATEGORIAS.xlsx`** — al añadirlos, la cobertura sube sola.
- **Las unidades de kit se leen de `v_ventas_producto`**, no de `v_ventas_bi` /
  `v_ventas_explotada`: ahí un kit aparece una vez por componente con la *misma*
  `cantidad_neta`, así que sumarla infla ×4,2. El **valor** sí coincide por las dos
  vías (la explosión prorratea): verificado, 6.269.015.175 en 2025.
- **`mv_ventas_recompra` tiene columna `nivel` y sus niveles NO se suman.** Un
  `COUNT(DISTINCT factura_id)` por cliente no se rueda hacia arriba: quien compró el
  producto A una vez y el B una vez tiene «1 vez» en cada producto y «2 veces» en el
  total. Medido en 2026: los niveles `canal` suman 43.741 clientes y el nivel `total`
  da 43.557. Siempre `WHERE nivel = '…'`.
- **`mv_ventas_recompra` no lleva empresa a propósito**: un cliente que recompra lo
  hace sin importar qué sociedad facturó. Partirlo por empresa contaría dos veces al
  que compró en las dos.
- **`v_lk_tercero.zona` es la zonificación del canal mayorista** aplicada al
  departamento. Queda poblada para casi cualquier cliente colombiano (~97,8 %), así
  que sirve como corte geográfico en otros canales — pero es la regional del
  mayorista, no una propia de cada canal. `'sin zona'` (literal) = departamento
  extranjero mapeado; `NULL` = sin departamento.

### 10.4 Lo que NO cuadra con Power BI, y por qué está bien

- **Kits: −6,3 %** (106.732 unidades históricas contra 113.862). `v_ventas_producto`
  excluye `es_reverso`, las anulaciones reales. Por kit la diferencia es del 0,1 %
  (PCNKIT12: 15.137 vs 15.113). Mismo criterio ya fijado para el Resumen.
- **Recompra en Shopify: 12,87 % contra 13,14 %.** El informe va por fecha de
  factura e incluye reversos.
- **Penetración de portafolio**: en Power BI da 103 %, o sea que su denominador está
  fijo. Aquí se define como productos vendidos / portafolio comercial activo (46
  productos no-kit con código PCN/KD/TNG/B8) y por tanto no puede pasar de 100 %.

### 10.5 Datos que dependen del negocio

- **`bi_producto_lanzamiento`** viene sembrada con las **17 fechas legibles** en la
  captura del informe. Faltan los demás productos y los 39 kits. Sin fila, la
  intranet muestra «sin fecha de lanzamiento» en lugar de un ciclo de vida
  inventado. **No derivar la fecha con `MIN(fecha_venta)`**: la historia arranca en
  2024-06 y a [PCN07] (lanzado 2021-09) lo movería de «Clásico» a «Maduro»,
  cambiando su meta del 2 % al 5 % sin que nada lo delate.
- **`bi_ciclo_vida`**: las metas (20 / 5 / 2 %) se leen literalmente del informe,
  pero **los cortes en meses (18 y 36) son una inferencia** de los casos visibles
  (antigüedad 12 → Crecimiento, 25 → Maduro, 37+ → Clásico). Están en tabla para
  que el negocio los corrija sin desplegar.

### 10.6 Fuera de alcance de esta fase

- **Margen bruto** y **penetración/cobertura** son de **otro** `.pbix` (su menú es
  Ventas / Margen / Cartera). El margen necesita costo por producto, que el DW **no
  tiene**: `dim_producto` no trae `standard_price`. Solo hay margen por la vía
  contable (`mv_contab_canal_mes`, ingresos grupo 41 − costos grupo 61).
- **Cartera** sigue pendiente como hoja propia (§7).
- **Clientes Elite** y **Mayoristas2**: no hay captura de referencia.

## 11. Fase 4 — hoja de NIELSEN (2026-07-30)

DDL: `sql/marts/28_nielsen_dashboards.sql`. GRANTs: `sql/marts/24_rol_intranet.sql`
(**re-ejecutarlo después del 28**). Refresco: `MVS_NIELSEN`, solo en el tick `:00`
(el dato es **semanal**; refrescarlo cada 15 min no puede traer nada nuevo).

### 11.1 El dataset

573.013 filas · 164 semanas (**2023-05-14 → 2026-06-28**) · 4 markets · 3 categorías ·
248 marcas · 195 fabricantes · 3.601 ítems · 13 presentaciones (una vacía).

Los tres casts son **perfectos**: 0 filas mal formadas de 573.013 en `vtas_valor`,
`vtas_unds` y `dist_num`. Y `periods` parsea al **100 %** con
`to_date(split_part(periods,'fin ',2),'DD/MM/YY')` — el formato es
«1 sem 26-26 fin 28/06/26» y se toma la fecha de cierre, ignorando el número de semana
del texto (la fecha es lo que permite ordenar y agrupar sin ambigüedad).

### 11.2 Cotejo contra el informe — cuadra al segundo decimal

`TOTAL COLOMBIA FARMACIAS`, todo el histórico:

```
total ventas ......... 474.124.569.959     el informe dice «474 mil M»      ✔
total unidades ....... 18.586.406          «18,59 mill.»                    ✔
precio medio ......... 25.509,21           «$25.510,16»                     ✔
marcas / productos ... 207 / 2.589         idéntico                         ✔
presentaciones ....... 13                  idéntico (con el blanco)         ✔
categorías ........... SHAMPOO      280.703.031.368  59,20 %               ✔
                       TRATAMIENTOS 112.474.208.195  23,72 %               ✔
                       BALSAMOS      80.947.330.396  17,07 %               ✔
share histórico ...... ELVIVE 9,70 · OTRAS 7,10 · DOVE 5,81 · TIO NACHO 5,20  ✔
share de ELVIVE mes .. 10,10 / 10,23 / 10,23 / 10,25 / 9,94 / 9,15 / 8,97 /
                       9,13 / 9,75 / 8,60 / 9,68 / 10,17   columna por columna ✔
```

⚠ Estas cifras son de **todo el histórico**, así que **no se mueven con el ETL** — al
contrario que las de 2026 de Ventas. Se pueden fijar en un test.

### 11.3 Las seis trampas del dataset

1. ⚠ **LOS 4 MARKETS NO SE SUMAN.** `NEW TOTAL COLOMBIA` (1.998.446.266.413) ya
   contiene a los otros. El KPI «2.549 mil M» de la hoja *Comparar* del informe es
   **exactamente** 1.998.446.266.413 + 474.124.569.959 + 76.507.844.825, o sea el
   mercado **inflado ~27 %** por sumar universos solapados. La intranet obliga a elegir
   UN market; `bi_nielsen_market.es_universo_total` marca cuál es el total.
2. ⚠ **`Total Colombia Supermercados` no trae valor NI unidades**: 96.675 filas, el
   **100 %** de ese market. Solo sirve para distribución.
3. ⚠ **La marca propia solo está medida en 2 de los 4 markets**: FARMACIAS (desde
   **2024-12-15**) y ECOMMERCE (desde **2024-12-22**). En `NEW TOTAL COLOMBIA` no
   aparece. Los 4 se exponen igual porque la hoja también sirve para estudiar mercados
   donde todavía no se entra (decisión de William) — pero un 0 % ahí significa **«aquí
   no nos miden»**, no «aquí no vendemos», y la intranet lo distingue.
4. ⚠ **El share por mes del informe MEZCLA AÑOS.** Su modo «MES» agrupa por número de
   mes ignorando el año: POCION sale con **2,17 %** en enero cuando su enero-2026 real
   es **4,56 %** — la mitad, porque enero-2024 y enero-2025 (donde la marca no existía)
   entran en la misma columna. Es el mismo error de forma que el «7 meses contra 12» de
   Ventas. El grano por defecto de la intranet es **año-mes**.
5. ⚠ **`dist_num` es un PORCENTAJE POR ÍTEM** (0,016 a 69,47), no una fracción ni un
   share: la suma por categoría/semana/market da **1.814 %**. No se suma ni se promedia
   entre ítems. Vive solo en `mv_nielsen_item_semana`.
6. ⚠ **El UPC de Nielsen no casa con ningún código propio** (0 de 18 ítems de la
   marca). Nielsen es *sell-out* de mercado y las ventas propias son *sell-in*: la hoja
   es **autónoma** y no se cruza con `mv_ventas_*`.

### 11.4 Objetos

| Objeto | Filas | Para qué |
|---|---|---|
| `mv_nielsen_semana` | **158.979** | share, ranking y series (agregada, sin `item` ni `dist_num`) |
| `mv_nielsen_item_semana` | **573.013** | ranking de productos y `dist_num` (grano de ítem) |
| `bi_nielsen_market` | 4 | metadatos: cuál es el total, cuál solo trae distribución |
| `bi_nielsen_marca_propia` | 1 | las marcas de la casa, para no cablear 'POCION' en el código |

`bi_nielsen` **crudo sigue negado** a `intranet_ro`.

### 11.5 Lo que la intranet hace distinto del informe (autorizado)

- **Mercado de selección única, sin «Todas»**: mata el mercado inflado del 27 %.
- **La matriz de share arranca en «año y mes»**; el modo «mes» se conserva para
  estacionalidad pero **avisa** de lo que le hace a una marca nueva.
- **Aviso cuando el universo no mide la marca propia**, con la fecha desde la que sí.
- **Supermercados marcado «solo distribución»**: sus paneles de valor devuelven `null`
  con la razón, no ceros.
- **El crecimiento es 52 semanas contra las 52 anteriores** (estándar del panel) y se
  rotula. Da **25,43 %** en farmacias; el «30,27 %» del informe **no se deduce** de
  ninguna combinación de los datos — mismo caso que el «Ingreso esperado» de Ventas.
- **El share «entre competidores» y el share DEL MERCADO se muestran los dos**, más el
  peso del grupo. Medido: POCION es el 31,02 % de su grupo pero el **2,34 %** del
  mercado, y el grupo entero pesa el 7,55 %. Sin ese contexto el 31 % engaña.

### 11.6 Cambio de fuente de la LÍNEA de producto (2026-07-30)

⚠ **`v_lk_producto.linea` ya NO sale de `bi_lineas`** (el Excel «LINEAS Y CATEGORIAS»)
sino del **árbol de categorías de Odoo** (`dim_producto.categoria`). Decisión de
William: la fuente de verdad es Odoo. Y medido, además es mejor:

```
                          bi_lineas (Excel)   dim_producto.categoria (Odoo)
cobertura del valor ....      94,43 %              100,000 %  en 2024/25/26
productos sin línea ....        5                     0
líneas .................       12                    10
```

Los 5 que el Excel no tenía sí están en Odoo: **PCN32/33/36 → «Línea Control Caspa»**
(una línea entera que al Excel le falta) y **PCN34/35 → «Anti Caída»**. Las cifras
coinciden: Reparación da **31.410.481.087** en 2025 contra los 31.410.435.978 de
TRADICIONAL. La única diferencia de granularidad es que Odoo agrupa en «Especializada»
lo que el Excel parte en BITE ME + LANZAMIENTO + BOOSTER + PERFUME (suman lo mismo).

La normalización quita el prefijo `Inventario/Producto Terminado( Importado)?/` y luego
el `Línea `/`Linea ` inicial, porque el árbol de Odoo es inconsistente. Verificado: no
hay colisiones.

⚠ **Desapareció el eje «categoría de producto»** (SHAMPOO, MASCARILLA, TRATAMIENTO,
CREMA DE PEINAR, PERFUME, VIAJERO): solo existía en el Excel. El árbol de Odoo tiene
**tres niveles** y no llega a ese detalle, y el ETL solo lee `id, default_code, name,
categ_id` de `product.product`. **No se deduce del nombre del producto** — un renombre
le cambiaría la categoría sola. Vuelve el día que el negocio añada un nivel al árbol de
Odoo o etiquetas de producto.

⚠ **`bi_lineas` ya no la mira ningún objeto concedido a la intranet.** No se borra (el
ETL la carga y el `.pbix` la usa), pero antes de volver a engancharla, leer esto.

---

## 12. Fase 5 — hoja de CUENTAS CLAVE (2026-07-31)

Sell-in contra sell-out por retailer. DDL en `sql/marts/29_cuentas_clave_dashboards.sql`;
`GRANT` en `24_rol_intranet.sql` (re-ejecutarlo **después** del 29).

El ETL ya estaba hecho y validado (`cargar_cuentas_clave.py`, ver
`docs/cuentas_clave_migracion.md`): esta fase **no lo toca**. Lo único que faltaba era la
capa curada y el permiso — los tres volcados son `VARCHAR(512)` y estaban negados al rol.

### 12.1 El dataset

| Origen | Filas | Clientes | Notas |
|---|---:|---:|---|
| `bi_cuentas_clave_ventas` | 60.515 | 10 | 2024-12-01 → 2026-06-30 · 35 productos |
| `bi_inventario_cclave` | 4.303 | 8 | **una sola foto** (`_loaded_at` 2026-07-23) |
| `bi_tiendas_cclave` | 1.155 | 11 | catálogo de tiendas |
| `bi_cuentas_clave` (BASE) | 57 | — | mapeo de códigos por retailer |

Calidad medida antes de escribir el DDL: **0 filas mal formadas** en los casts de
`unidades`, `inventario` y `maximo`; **0 huecos** en fecha, producto, tienda e `id_tienda`.

### 12.2 Empalmes verificados

| Empalme | Resultado |
|---|---|
| `producto` → `dim_producto.codigo` | **35 de 35** extrayendo el código de `[COD] NOMBRE` |
| `cliente` → `tercero_id` **por semilla** | **11 de 11** — ⚠ por nombre era una trampa, ver 12.4.9 |
| `id_tienda` ventas → catálogo | **1.128 de 1.128** (normalizado) |
| `id_tienda` inventario → catálogo | **315 de 315** (normalizado; en crudo 155) |

⚠ La normalización (`UPPER` + colapsar espacios) **no es cosmética**: el volcado de
inventario conserva la caja original («Locatel Calle 100») y ventas y el catálogo la traen
en mayúsculas. Sin normalizar, el inventario pierde **la mitad** de sus tiendas y ningún
error lo delata.

### 12.3 Cotejo contra el informe

El sell-out por cliente cuadra con los números de control del `.pbix` (snapshot
2026-07-22): **9 de 10 exactos** y NOVAVENTA con −2 uds, el desvío ya documentado del ETL.

    FARMATODO 212.665 · BRECCIA 18.016 · LEOPHARMA 18.851 · LUCEGO 11.788
    LIFE 8.375 · PROSALON 7.782 · PASTEUR 7.463 · SURTI 3.514 · LASKIN 2.354
    NOVAVENTA 534.466 (control 534.468)

### 12.4 Las trampas (el detalle está en la cabecera del 29_*.sql)

1. **El sell-out NO trae pesos.** `valores` viene vacía en los 10 retailers, así que la MV
   **no la expone** y la hoja entera se mide en unidades — el sell-in también.
2. **Cada retailer cierra en un mes distinto** (NOVAVENTA 2025-10 … LEOPHARMA 2026-06).
   Cualquier ratio tiene que recortar los dos lados a la ventana con sell-out **de ese
   cliente**: sin recortar, 2026 da un 6 % falso. Recortado: total **79,0 %**, y por
   retailer entre 44,7 % y 125,3 %. Es el bug del `.pbix`, donde el sell-out ignora el
   filtro temporal.

2b. ⚠⚠ **Un ratio > 100 % NO es necesariamente un error**, y esto corrige lo que parecía
   obvio. LUCEGO da **125,3 %** con la ventana bien recortada porque **compró 33.694 uds
   antes** de que su reporte de sell-out existiera (202505-202510, reporte desde 202511):
   está vendiendo inventario acumulado, y eso es un dato útil. Lo que sí es un caso aparte
   es el **sell-in ≤ 0** (LUCEGO 202601 = −175 netos por devoluciones): ahí el ratio no
   existe y hay que decirlo. El ratio se lee como *sell-through de la ventana*, y el
   sell-out empieza cuando empieza el REPORTE del retailer, no la relación comercial.
3. **`id_tienda` = CLIENTE ‖ NOMBRE_TIENDA** con cajas distintas por origen (ver 12.2).
4. **El inventario es una FOTO**, no una serie: `foto_at` dice de cuándo.
5. **`vendedor` viene vacío al 100 %** y `sucursal` casi; no se exponen.
6. **96 filas con unidades negativas** son devoluciones y se conservan.
7. **`maximo` SOLO lo entrega FARMATODO** (2.006 filas de 4.287). Los otros 7 mandan el
   texto `'0'`, que no es vacío — una cuenta de «celdas con dato» lo daba por poblado.
   `llenado_pct` lleva `NULLIF(SUM(maximo), 0)`: para ellos es NULL, que significa «no
   sabemos cuánto cabe» y no «el anaquel está vacío».
8. **Dos tiendas de LASKIN venden y no están en el catálogo.** La cobertura parte del
   catálogo, así que no pasa del 100 %, pero el catálogo no es exhaustivo. LASKIN es
   además el de peor cobertura: 15 de 27.
9. ⚠⚠ **El cliente NO se empalma por nombre.** Hay **cuatro «FARMATODO»** en
   `dim_tercero`, y el que casa por nombre EXACTO con el sell-out («FARMATODO COLOMBIA
   SA», sin puntos, id 388191) es un duplicado **con cero ventas**; el que factura es
   «FARMATODO COLOMBIA S.A» (id 268476). El empalme por nombre dejaba al mayor retailer
   del panel —212.665 uds de sell-out— con sell-in 0 y sin ratio, **sin dar ningún
   error**. Con el tercero correcto sale al 78,5 %, en línea con el resto. Por eso el
   empalme va por la semilla **`bi_cclave_cliente`**, verificada uno a uno: los otros 9
   coincidían con su nombre, pero eso era suerte, no un contrato.

### 12.5 Objetos expuestos

    mv_cclave_venta_mes    sell-out: cliente x mes x producto x tienda (35.081 filas)
    mv_cclave_inventario   inventario en tienda, con llenado_pct (4.287)
    mv_cclave_tienda       catálogo de tiendas: el DENOMINADOR de la cobertura (1.155)
    bi_cclave_cliente      semilla: retailer -> tercero_id (11 filas, verificadas)
    bi_cclave_ciclo        semilla: dias de reposicion por retailer — nace VACIA

Las cuatro con `GRANT SELECT` a `intranet_ro`. Los cuatro volcados crudos **siguen
negados** (verificado). Refresco en el tick `:00` (`MVS_CCLAVE`).

⚠ `mv_cclave_tienda` es una MV propia y no un `SELECT DISTINCT` sobre las ventas a
propósito: si el denominador de la cobertura fueran las tiendas que vendieron, la
cobertura sería siempre 100 % y el panel no diría nada.

### 12.6 Dato de negocio que falta

**`bi_cclave_ciclo` nace vacía.** El «Stock Sugerido» del informe usa una tabla
`DIAS_INVENTARIO` (ciclo 8/15/80 días) **cableada con `Table.FromRows` dentro del `.pbix`**:
no existe en ninguna base. Hasta que el negocio la llene, la intranet devuelve «no
calculable» con la razón. Un ciclo inventado daría un stock sugerido con aspecto de dato.

Y **ZAR IMPORT (Ecuador) tiene inventario (51.210 uds) y 2 tiendas, pero cero sell-out**:
el ETL lo deja fuera a la espera de un acceso directo roto en Drive
(`SEGMENTO DE CLIENTES.xlsx`). Su cobertura es 0 % y eso es un dato, no un hueco.

### 12.7 Fuera de alcance

- **Cartera** ya no está pendiente: se construyó en la fase 6 (§13).
- El `.pbix` no se toca (decisión de William: Power BI es transitorio).

---

## 13. Fase 6 — hoja de CARTERA (2026-08-01)

DDL en `sql/marts/30_cartera_dashboards.sql`. Expone `marts.v_cartera` —que la intranet
**no puede leer**— como una sola vista materializada.

### 13.1 Objeto expuesto

| Objeto | Grano | Filas | Para qué |
|---|---|---|---|
| `mv_cartera_saldo` | línea de CxC (`linea_id`) | 6.023 | saldos, vencimiento, responsable |
| `bi_cartera_tipo_credito` | tipo de cliente | 14 | qué es cartera de crédito y qué no |
| `bi_cartera_responsable` | cliente / tipo / default | 69 | quién cobra |

`GRANT` en `24_rol_intranet.sql`, que hay que **re-ejecutar después** del 30.

### 13.2 Por qué se concede la MV y no la vista

⚠⚠ **`v_cartera` expone `identificacion`, o sea el NIT del tercero**, y por eso está
negada a `intranet_ro`. La MV **no propaga esa columna**. No añadirla: la hoja no la
necesita y sería dato personal saliendo a una app web.

### 13.3 Lo que se midió (2026-08-01)

```
v_cartera .......... 6.018 filas · 595 terceros · 836 documentos · 9.135.510.346
  entry ............ 5.738 filas · solo   142 con vencimiento
  out_invoice ......   240 filas ·        240 con vencimiento (100 %)
  out_refund .......    40 filas ·         40 con vencimiento (100 %)
saldo negativo ..... 2.945 filas · −4.922.033.134 · 475 terceros
empresas ........... HFA 4.011 / 639.925.131  ·  PCN 2.007 / 8.495.585.215
```

Los cuatro bloques de la hoja **suman exactamente el total** (verificado en la prueba del
DDL): anticipos −4.922.033.134 + cartera con mora 6.763.624.037 + crédito sin vencimiento
2.857.493.532 + fuera de crédito 4.437.171.641 = **9.136.256.076**.

### 13.4 Las decisiones que hay que respetar

1. ⚠⚠ **Solo las facturas admiten mora.** `fecha_vencimiento_key` es
   `account.move.line.date_maturity`, y Odoo solo la calcula cuando hay término de pago.
   Los `entry` (recibos, reclasificaciones, ajustes) no lo tienen: 142 de 5.738. La MV
   publica `admite_mora` y la hoja separa los dos mundos. El pipeline viejo lo resolvía con
   `Numero.str.startswith('F')`, que además se comía los anticipos sin decirlo.
2. ⚠⚠ **`tipo_cliente` no es `categoria`.** La vista expone el valor **crudo** de Odoo;
   `categoria` ya está normalizada por `map_categoria` y nunca es nula. Filtrar por una o
   por otra da conjuntos distintos. El pipeline de cartera siempre usó el crudo.
3. ⚠ **No todo lo que hay en cartera es cartera de crédito**, y lo que sobra no se
   descarta: son 4.437 MM. `CLIENTE` (contado, 2.260 MM), `Proveedores` (1.020 MM),
   `(sin tipo)` (−3.223 MM, casi todo asientos sin cliente) y hasta un
   `Wrote Judge.me web review` que es basura literal de Odoo. La semilla los registra
   **con su motivo** para que la hoja pueda explicarlos.
4. ⚠ **Los negativos son anticipos y van aparte, sin netear** (decisión de William). Un
   anticipo no cancela una factura vencida; netearlos escondería mora real.
5. ⚠ **El aging NO se materializa.** `dias_atraso` depende de HOY: congelarlo en el
   refresco haría que, pasada la medianoche, la hoja mostrara la mora de ayer. La MV trae
   `fecha_vencimiento` y `dias_credito` (estables) y el corte lo hace la intranet contra
   `CURRENT_DATE`.
6. ⚠ **El responsable no existía en el almacén.** El `.pbix` repuntó su tabla `Cartera` a
   `v_cartera` y perdió la columna: hoy es `Table.AddColumn(ORD, "RESPONSABLE", each null)`.
   Se modela en `bi_cartera_responsable`, con precedencia **cliente > tipo > default**, y
   arranca desde el último volcado real del pipeline (2026-07-23): **DIANA RIOS, DANIELA
   DURAN y SHELLSY VELASCO**. Resuelto: 337 filas con dueño por 8.542 MM, y 5.686 sin
   dueño por 593 MM (casi todo asientos sin tercero).
   ⚠⚠ **Ese `INSERT` de arranque solo corre si la tabla está VACÍA** (2026-08-03). El
   `ON CONFLICT DO NOTHING` parecía suficiente y no lo era: los índices únicos son
   **parciales y por nivel**, así que una fila del volcado a nivel *cliente* no colisiona
   con una del Excel a nivel *tipo de cliente* y se inserta al lado — y con la precedencia
   `tercero_id > cliente > tipo_cliente`, **gana la del volcado**. Re-ejecutar el fichero
   (que la propia cabecera manda hacer al tocar la MV) revertía los responsables al estado
   del 2026-07-23, en verde y sin un error. La fuente de verdad es la hoja `Responsables`
   y quien la carga es `cargar_cartera_responsables.py`.
7. ⚠⚠ **Una nota débito se disfraza de factura y nace vencida** (2026-08-03). Odoo las
   emite con `move_type = 'out_invoice'`, idéntico a una factura, y **sin término de
   pago**: `date_maturity = date`, cero días de crédito. Pasan `admite_mora` y entran
   directas en «61-90» o peor. Medido: `NDY4` (NOVAVENTA, **55.569.759**, «FE7281, Ajuste
   por precio») era el **48 % de la mora de DIANA RIOS** y el **98 % de su rango 61-90**.
   · Lo único que las distingue es el **diario** (`dim_diario.codigo IN ('NDY','NDEXP')`,
     por código y no por nombre — misma regla que `14_ventas.sql` y `25_nd_factura.sql`),
     y `v_cartera` **no propagaba `diario_id`**. Ahora publica `es_nota_debito` y
     `documento_origen`, y la MV los arrastra.
   · ⚠ `v_cartera` está definida **dos veces** (`06_cartera_en_hecho.sql` y
     `07_widen_text.sql`, que corre después y **gana**). Las dos tienen que coincidir.
   · **Sí son deuda**: la hoja las saca de la cola de cobro, no de la vista. La MV solo
     las marca; quien decide es la intranet.

### 13.5 Rangos de mora

Se conservan los cortes del informe, incluidas sus rarezas: `Corriente` es **menos de −7
días y también 1…10**, `Proximo` es −7…0 (el día 0 cae aquí, no en Corriente), y luego
`11_30`, `31_60`, `61_90`, `90+`. No es un error de transcripción; cambiarlos haría que la
hoja no cuadrara con el correo que cartera lleva años recibiendo.

### 13.6 Refresco

`MVS_CARTERA` en `refrescar_mv_dashboards.py`, en el tick `:00`. Sale del hecho contable
—que sí se actualiza cada 15 minutos— pero la cartera se gestiona por días y una factura no
cambia de rango de mora en un cuarto de hora.

### 13.7 Fuera de alcance

- Los **días de crédito pactado** del exterior seguían cableados en el notebook
  (`DROGUERIA CORPORACION LIFE` 120, `C&L SOLUTIONS` 120, `ZAR IMPORT` 100,
  `DISTRIBUIDORA LEOPHARMA` 120). No se portan todavía: hoy esas facturas usan el
  `date_maturity` de Odoo, que es lo que la contabilidad tiene registrado.
- `bi_cliente_credito` (días de CxC y saldo de anticipo por cliente) sigue **sin portar**:
  alimenta la proyección de flujo de caja, que es de la hoja de Contabilidad (§9).
- Las 12 líneas con `estado_pago = 'paid'` y saldo distinto de cero **se conservan**: son
  facturas de exportación con residuos de redondeo de divisa (la mayor, 323.844).

---

## 14. Fase 7 — hoja de MARKETING (2026-08-03)

DDL en `sql/marts/31_marketing_dashboards.sql`. Alimenta `/dashboards/marketing` de la
intranet (Resumen · Plataformas · Embudo · Diario), que porta el artefacto que marketing
tenía en Cowork y que guardaba todo en el `localStorage` de un navegador.

### 14.1 Objetos expuestos

| Objeto | Grano | Filas hoy | Para qué |
|---|---|---|---|
| `mv_marketing_gasto_dia` | fecha × país × plataforma | 0 | gasto YA convertido, compras y ROAS auto |
| `mv_marketing_web_dia` | fecha × país | 0 | venta, pedidos, sesiones e impresiones |
| `mv_marketing_atribucion_dia` | fecha × país × canal × fuente | 0 | venta atribuida por canal |
| `bi_marketing_pais` | país | 3 | catálogo: moneda, zona horaria, ids de Shopify/GA4/GSC |
| `bi_marketing_cuenta` | país × plataforma | 8 | **qué plataformas tiene cada país** |
| `bi_trm_dia` | fecha | 73 | tasa de cambio diaria (NO se concede) |

`GRANT` en `24_rol_intranet.sql`, que hay que **re-ejecutar después** del 31.

Las tres MV nacen **vacías**: solo la TRM tiene fuente funcionando. Ver §14.7.

### 14.2 Por qué la conversión de moneda vive en la MV y no en el loader

⚠⚠ El artefacto tenía la TRM en **una casilla de texto con `4000` por defecto**, global y
sin fecha. Las cuentas de Meta y Google de **Ecuador facturan en COP** mientras Ecuador
reporta en USD, así que todo su gasto y su ROAS colgaban de ese número.

Medido el 2026-08-03: **la TRM real es 3.144,14**, no 4.000. Un gasto de 1.000.000 COP se
reportaba como US$250 cuando son US$318 — o sea que el artefacto **subestimaba la
inversión de Ecuador un 21 % e inflaba su ROAS un 27 %**.

Por eso el loader guarda **solo la moneda nativa** y la conversión la hace
`mv_marketing_gasto_dia` contra `bi_trm_dia`, con la tasa vigente de cada día. Corregir
una TRM re-convierte el histórico en el siguiente refresco, en vez de dejar un número malo
petrificado. La MV publica `trm_usada` para que la cifra sea auditable.

⚠ La tasa se busca con la **vigente más reciente ≤ fecha**, no con la del día exacto: la
serie de datos.gov.co publica vigencias (la del viernes rige hasta el domingo) y con un
join directo el gasto del sábado saldría sin convertir.

### 14.3 Lo que se midió (2026-08-03)

```
TRM cargada ................. 73 dias (2026-06-01 -> 2026-08-02)
TRM vigente hoy ............. 3.144,14 COP/USD   (el artefacto usaba 4.000)
paises activos .............. 3   CO (COP) · EC (USD) · RD (USD)
cuentas de publicidad ....... 8   CO 3 · EC 3 · RD 2  (RD no tiene TikTok)
hechos ...................... 0   las 4 fuentes esperan credenciales
```

### 14.4 Las decisiones que hay que respetar

1. ⚠⚠ **`NULL` no es cero.** `sesiones`, `usuarios`, `impresiones`, `clics` y
   `posicion_media` son anulables a propósito: GA4 y Search Console solo entregan desde
   que se les concedió acceso. El artefacto mostraba «0 sesiones sobre una meta de
   18.000» —semáforo rojo permanente sobre un dato inexistente—. Si el loader escribe 0,
   la hoja vuelve a mentir.
2. ⚠ **Qué plataformas tiene un país sale de `bi_marketing_cuenta`**, no de las filas con
   gasto. RD no tiene TikTok: en el artefacto eso estaba cableado en TRES sitios del
   JavaScript. **La ausencia de fila ES el dato.**
3. ⚠ **Shopify, GA4 y Search Console NO son «plataformas»**: sus identificadores van en
   columnas de `bi_marketing_pais`. Como filas de `bi_marketing_cuenta`, la intranet los
   pintaría como una cuarta tarjeta de publicidad con su ROAS.
4. ⚠ **El día en curso no se carga.** Las cuatro fuentes lo entregan incompleto. La
   intranet cuenta con ello: su cálculo del ritmo divide entre **días con dato**.
5. ⚠ **`canal` tiene que coincidir EXACTAMENTE** con `bi_marketing_cuenta.plataforma`
   (`Meta`, `Google`, `TikTok`): la intranet cruza por igualdad de cadena, y un
   `facebook` aquí contra un `Meta` allí deja el ROAS last-click en null sin avisar.
6. ⚠ **Las compras auto-reportadas se solapan y no se suman.** El mismo pedido lo
   reclaman Meta y Google; su suma supera los pedidos reales de Shopify. Se guardan tal
   cual y la intranet las publica con su aviso. El «ROAS prorrateado» del artefacto —que
   repartía toda la venta usando esas conversiones— **no se portó**.

### 14.5 Las tres capas

```
bi_marketing_pais / bi_marketing_cuenta   config, se teclea en el SQL
bi_trm_dia                                 la llena cargar_marketing.py
bi_marketing_*_dia                         aterrizaje: lo que dijo cada API
mv_marketing_*_dia                         lo que lee la intranet
```

No es ceremonia: es lo que permite recargar la TRM y que el gasto convertido se corrija
solo, y lo que deja tipar de verdad en vez de heredar el `VARCHAR(512)` de
`DBLoader.cargar` (el problema que las hojas 28 y 29 tuvieron que arreglar después).

⚠ El aterrizaje **NO se concede** a la intranet: está en la moneda de la cuenta, y leerlo
directamente daría un ROAS ~4.000 veces mayor sin que nada lo delatara.

### 14.6 Refresco y carga

`MVS_MARKETING` en `refrescar_mv_dashboards.py`, tick `:00`. Las tres son independientes
entre sí: no hay orden que respetar.

`cargar_marketing.py` se engancha en el **paso 2b** de `run_dw.py`, entre el cierre y el
refresco de MV. ⚠ Va antes del refresco: al revés, `mv_marketing_gasto_dia` convertiría el
gasto de hoy con la TRM de ayer. ⚠ El import es **perezoso**: `cargar_marketing` necesita
`requests` y las librerías de Google, y un `ImportError` en la cabecera dejaría caído todo
el ETL de Odoo.

### 14.7 Fuera de alcance

⚠⚠ **Solo la TRM funciona.** No hay ninguna credencial de Supermetrics, GA4, Search
Console ni Shopify en este repo — cero rastros en el `.env`. El `google_credentials.json`
que existe es una cuenta de servicio con **un único scope, `drive.readonly`**; sirve como
identidad (mismo `client_email` que dar de alta en GA4 y Search Console) pero los scopes
se piden en código y las APIs hay que habilitarlas en el proyecto `loginlapocion`.

Los cuatro conectores de `cargar_marketing.py` están **escritos y aislados pero sin
probar**: cada uno comprueba su credencial y, si falta, avisa y devuelve vacío. La hoja de
la intranet responde 200 y dice «sin dato» con la razón, que es su comportamiento honesto.

Lo que hace falta para encenderlos, paso a paso, está en el repo de la intranet:
`docs/dashboards/marketing-contrato.md` §0 Fase A. El bloqueante duro es confirmar que hay
**plan de API de Supermetrics**: el conector de Cowork es interactivo y no sirve para un cron.
