# Guía — Ventas en Power BI desde el Data Warehouse

Cómo armar el reporte de ventas sobre el esquema `marts`, reemplazando el pipeline de Excel
(`ReportClassNew.pipeline_bi`). Recoge todas las reglas validadas contra el `base_ventas`.

Operación del DW: [GUIA_OPERACION.md](GUIA_OPERACION.md) · Modelo: [MODELO_ESTRELLA.md](MODELO_ESTRELLA.md)

---

## 1. Qué importar

Para ventas se importa **una sola tabla de hechos DELGADA: `marts.v_ventas_bi`**. Trae **solo IDs de
relación + columnas degeneradas + medidas**; todo lo descriptivo (nombre de cliente, ciudad, producto,
vendedor, mes…) se obtiene **cruzando con las dimensiones que el BI YA tiene cargadas** para los estados
financieros. Así la tabla de ~905k filas ocupa mucho menos y el modelo va más rápido.

> `v_ventas_bi` sale de `v_ventas_explotada` (misma vista, recortada). `v_ventas_explotada` sigue
> existiendo para consultas SQL/depuración, pero **no se importa al BI** (duplicaría columnas de las dims).

| Objeto | Para qué |
|---|---|
| **`marts.v_ventas_bi`** | **Único hecho de ventas** (delgado). Kits vendidos y unidades de producto. |
| `marts.dim_producto` | Producto, `categoria` de producto, `es_kit`. **Impórtala 2 veces** (ver §2). |
| `marts.dim_tercero` | Cliente: `nombre`, `identificacion`, `pais`, `ciudad`, `departamento`, `cliente_padre`. |
| `marts.dim_vendedor`, `marts.dim_empresa` | Vendedor y empresa. |
| `marts.dim_fecha` | Calendario. **Relacionar por `fecha_venta`** (ver abajo). |
| `marts.map_zona`, `map_cliente_padre`, `map_categoria` | Mapeos comerciales que NO están en Odoo. |
| `marts.v_exportaciones` | PyG de exportación por país y cliente (modelo aparte). |

Conexión PostgreSQL (variables `DB_*`), **modo Import**.
- **NO importes `fact_movimiento_contable` para ventas.** Ese hecho (4,3M filas, grano línea contable)
  es para **estados financieros** (clases 1–7). Las ventas ya vienen resueltas y filtradas en la vista.
- No hace falta importar `map_nc_factura`, `v_precio_componente`, `dim_kit_componente` ni
  `v_ventas_producto`/`v_ventas_explotada`: ya vienen aplicados/recortados dentro de `v_ventas_bi`.

### Relaciones (las dimensiones ya están en el modelo)
Crea estas relaciones desde `v_ventas_bi` a las dims cargadas:

| Columna de `v_ventas_bi` | Dimensión | Estado |
|---|---|---|
| **`fecha_venta`** | `dim_fecha[fecha]` | **ACTIVA** (con esta fecha las NC restan en el mes de su factura) |
| `fecha_factura` | `dim_fecha[fecha]` | **INACTIVA** (mes de emisión de la NC; para §5) |
| `componente_id` | `dim_producto[producto_id]` | **ACTIVA** (vista *por producto*) |
| `producto_id` | `dim_producto` (**2.ª copia**, ver §2) | activa de esa copia (vista *tal como se factura*) |
| `tercero_id` | `dim_tercero[tercero_id]` | activa |
| `vendedor_id` | `dim_vendedor[vendedor_id]` | activa |
| `empresa_id` | `dim_empresa[empresa_id]` | activa |

Las degeneradas `categoria`, `pais`, `equipo`, `cliente_analitico`, `origen`, `tipo_movimiento`,
`numero_factura` **no tienen dimensión**: se quedan como columnas de `v_ventas_bi` (son cortas).

### Relación con el calendario ⚠
La relación **activa** con `dim_fecha` es por **`fecha_venta`**, **no** por `fecha` ni `fecha_factura`.
`fecha_venta` es la fecha de la **factura original**: hace que una nota crédito reste en el mes de la
venta que corrige. Si relacionas por otra fecha, las ventas mensuales salen distintas y las notas
crédito caen en el mes equivocado (ver §4.2).

---

## 2. Los kits: las DOS formas de ver las ventas (desde la MISMA tabla)

El kit se factura como **un producto con un valor único**. Ambas presentaciones salen de
`v_ventas_bi`: **el valor siempre se suma con `venta_componente`** y solo cambia la **dimensión**
(qué copia de `dim_producto` pones en el eje).

| Presentación | Eje (dimensión) | Relación | Valor | Unidades |
|---|---|---|---|---|
| **Por producto** (kit repartido) | `dim_producto[nombre_comercial/codigo/nombre]` | activa por `componente_id` | `SUM(venta_componente)` | `SUM(cantidad_componente)` |
| **Tal como se factura** (kit como unidad) | `dim_producto` **(2.ª copia)** `[nombre_comercial/codigo]` | por `producto_id` | `SUM(venta_componente)` | `[Kits vendidos]` (§5) |

### Las TRES lecturas del mismo dinero (sin IVA · con IVA · total factura)

Las tres están en **COP** y las tres se prorratean por componente con el mismo reparto.
⚠⚠ **No se suman entre sí**: son el mismo dinero leído de tres formas.

| Medida (`v_ventas_bi` / `v_ventas_explotada`) | Qué es | 2025 |
|---|---|---|
| `venta_componente` | base, **sin** impuestos — **la medida de VENTAS** | 82.418.538.975 |
| `venta_componente_con_iva` | base **+ IVA** (lo que dice la factura) | 97.611.567.272 |
| `venta_componente_total_factura` | base + IVA **− retenciones** = lo que **paga** el cliente | 95.820.878.823 |

En `v_ventas_producto` se llaman `venta_subtotal`, `venta_subtotal_con_iva` y `venta_total_factura`.
La diferencia entre las dos últimas es **exactamente la retención** (2,5 % o 3,5 % de la base).

**Cuál usar:** para reportar ventas, `venta_componente` (o la de con IVA si el informe se pide con
impuesto). `venta_componente_total_factura` sirve para **conciliar contra cartera** o contra el valor
del documento — **no** para medir ventas: la retención es un anticipo de *nuestro* impuesto de renta
que el cliente consigna por nosotros, no un menor ingreso.

El IVA no está en la línea de venta (vive en otra línea del mismo asiento), así que los factores
salen de `marts.v_impuestos_asiento`, que agrega por documento la base (clase 4), el IVA (cuentas
2408), la retención (1355) y el total a cobrar (líneas de CxC). Factor **1,19** en lo gravado y
**1,00** en exportación, excluido y exento.

⚠⚠ **La moneda.** `subtotal`, `total_con_impuesto` y `precio_unitario` vienen de Odoo **en la moneda
de la factura**, y las exportaciones se facturan en **USD** (medido: `price_subtotal` 500,00 USD
contra un `balance` de −1.851.640 COP en la misma línea). **Nunca se suman.** Todo el cálculo de las
tres medidas se hace con importes en COP del asiento, así que jamás mezcla dólares con pesos:
verificado, las tres columnas dan lo mismo en las líneas de exportación (factor 1,00).
⚠ `precio_unitario` además **incluye IVA** (58.900 con un `subtotal` de 49.496): no es un precio
neto y `precio_unitario × cantidad` no da `venta_componente`.

> **Nombre para mostrar:** usa `dim_producto[nombre_comercial]` (el nombre por el que se conoce el
> producto, ej. `PCN19` → "DUTONIC (TONICO CAPILAR)"). `nombre` es el técnico en idioma base
> (`product.product.name`, ej. "Kit anticaída…"); `nombre_comercial` es la **traducción es_CO** del
> `name` de la plantilla (`product.template.name`), que es donde vive el nombre comercial.

**Por qué dos copias de `dim_producto`:** una tabla de dimensión solo admite **una relación activa**.
La primaria (`componente_id`) da la vista *por producto* (el kit ya repartido en sus componentes). Para
poner el **kit** en el eje ("tal como se factura") importa `dim_producto` una segunda vez —renómbrala
p. ej. *Producto (kit)*— y relaciónala por **`producto_id`**. Para las filas no-kit, `producto_id` =
`componente_id` (el producto es su propio componente), así que ambas copias coinciden salvo en los kits.

Como `venta_componente` es la parte de cada componente, al agrupar por el **kit** los componentes se
**vuelven a sumar al valor del kit** — por eso la misma medida sirve para las dos vistas y el total
**nunca se infla**. `origen` marca la fila: `INDIVIDUAL` (producto suelto) o `KIT` (componente de un kit).

> ⚠ **TRAMPA — no sumes `cantidad_neta`.** Es de **nivel kit** y está **repetida** en cada fila de
> componente (una línea de kit = N filas); si la sumas, cuentas el kit N veces. Úsala solo con
> `[Kits vendidos]`, que deduplica por `linea_id`. Por eso `v_ventas_bi` **ya no expone `venta_subtotal`**
> (era el otro repetido): el valor SIEMPRE es `venta_componente` (verificado emp 8 2026: 42.810M con
> `venta_componente` vs 55.753M inflado si se sumara el nivel kit).

Ejemplo real (empresa 8, 2026): **29.637 kits vendidos** = **125.643 unidades de producto**, ambos por
**3.779.680.695** (`SUM(venta_componente)`).

### Cómo se reparte el valor del kit entre sus componentes
El valor se prorratea por el **precio individual de cada componente**, usando el promedio **dentro de
la categoría de cliente** de esa venta (los precios varían por canal):

```
peso(componente)   = precio_referencia(componente, categoría) × cantidad_en_el_kit
venta_componente   = venta_del_kit × peso / Σ pesos de la línea
```
El precio de referencia sale de `marts.v_precio_componente` (ventas del producto **suelto**, unidades
positivas). Cascada: precio en su categoría → promedio global del producto → si ninguno tiene precio,
todos pesan igual (reparto a partes iguales).

**Por qué no a partes iguales:** desviaba 20-25% por producto. En `PCNKIT12` (5 componentes,
158.273): PCN19 vale 40.349 suelto y PCN03 25.478; a partes iguales ambos recibirían 31.655. Con el
prorrateo por precio reciben **43.813** y **25.407** por unidad.

`origen` distingue el tipo de fila en `v_ventas_bi`: `INDIVIDUAL` (producto vendido suelto) o
`KIT` (componente que viene de un kit).

---

## 3. Reglas que ya vienen aplicadas en la vista

No hay que replicarlas en DAX; están dentro de `v_ventas_bi`:

- **Ventas netas**: ingresos (clase 4) de facturas **y notas crédito**; las NC restan (`venta_componente`
  negativo). No se casa por `ref` como el Excel: el enlace NC→factura sale de la **conciliación** de
  Odoo, y por eso la NC ya viene fechada en el mes de su factura (`fecha_venta`).
- **NC sin factura asignada NO cuentan en ventas.** Una nota crédito que no se pudo enlazar a su
  factura en la conciliación (`map_nc_factura`) queda **fuera** de `v_ventas_producto`/`v_ventas_bi`
  (no sabemos a qué venta pertenece, así que no resta). Se aíslan en **`marts.v_nc_sin_asignar`** para
  revisarlas y conciliarlas a mano. Medido 2026: 45 NC / ~−304M (de esos, ~−233M con factura de 2026).
- **Producto comercial**: `codigo` empieza por `PCN`/`KD`/`TNG`/`B8`.
- **`es_reverso`**: excluye **anulaciones reales** (factura + NC de reversión ≥99%). **No** excluye las
  pagadas por **factoring** ni las de **NC parcial** — esas son ventas reales.

## 4. Reglas que SÍ hay que respetar al construir los visuales

1. **Combina las dos empresas.** Ene-2026 se facturó en la **empresa 1** (HFA) y desde feb en la **8**
   (PCN). Filtrar una sola parte el año. Usa slicer de `empresa_id` solo si quieres verlas separadas.
2. **Agrupa las ventas por el calendario `dim_fecha`** (relacionado por `fecha_venta`), **no** por
   `fecha_factura`. Los atributos año/mes/mes_nombre salen de `dim_fecha`, no de la vista.

   ### Las dos fechas de `v_ventas_bi` y para qué sirve cada una
   | Columna | Qué es | Cuándo usarla |
   |---|---|---|
   | **`fecha_venta`** | Fecha de la **factura original**. Para una NC es la fecha de la factura que corrige. | **Ventas** (relación ACTIVA con `dim_fecha`) |
   | `fecha_factura` | Fecha propia del documento (la NC lleva la suya) | Informe de **NC por mes de emisión** (relación INACTIVA) |

   > La fecha **contable** del asiento vive en el hecho (`fact_movimiento_contable`), no en `v_ventas_bi`
   > (para ventas no se necesita).

   **Por qué:** una NC de marzo que corrige una factura de noviembre debe **restar en noviembre**, no en
   marzo. Ejemplo real: `NCR1858` (04-mar-2026) corrige `FEVY80693` (06-nov-2025) → resta en nov-2025.
   Medido en 2025-2026: **777 NC** caían en un mes distinto al de su factura, por **~6.584 millones**.
   El enlace se toma de la **conciliación** de Odoo (`marts.map_nc_factura`), porque la mayoría de NC no
   traen `ref` ni `reversed_entry_id`. Si una NC corrige varias facturas, su valor se **prorratea**
   (por eso `linea_id` no es único en la vista: afecta a ~76 de ~2.200 NC).
3. **`categoria` ≠ `producto_categoria`**:
   - `categoria` = categoría del **CLIENTE** (CALL CENTER, MAYORISTA NV, SHOPIFY, EXPORTACION…),
     consolidada de `partner_type_id` + analítico plan 21 + reglas de respaldo.
   - `producto_categoria` = categoría del **PRODUCTO** (viene de `dim_producto.categoria`).
4. **Zona / cliente padre**: unir con `map_zona` (depto+categoría) → `map_zona_cundinamarca`;
   cliente consolidado con `map_cliente_padre`.
5. **Exportaciones**: usar `v_exportaciones` y agrupar por **`pais_destino`** (no por `pais`), porque los
   gastos de exportación se facturan a proveedores logísticos colombianos.

---

## 5. Medidas base (DAX)

Todas sobre `v_ventas_bi`, relacionada con `dim_fecha` por **`fecha_venta`** (§1).
⭐ **El valor SIEMPRE se suma con `venta_componente`**.

```DAX
-- VALOR — sirve para las dos vistas (cambia solo la dimensión del visual):
--   por dim_producto (componente_id) → ventas por producto
--   por dim_producto "kit" (producto_id) → ventas por kit
Ventas = SUM ( v_ventas_bi[venta_componente] )

-- Unidades de PRODUCTO (kit repartido en componentes)
Unidades producto = SUM ( v_ventas_bi[cantidad_componente] )

-- Cuántos KITS se vendieron (unidad = el kit). cantidad_neta está repetida por componente,
-- así que se deduplica por línea con MAX. Filtrar a es_kit para que tenga sentido.
Kits vendidos =
    SUMX ( VALUES ( v_ventas_bi[linea_id] ),
           CALCULATE ( MAX ( v_ventas_bi[cantidad_neta] ) ) )

-- Cuánto de la venta viene de kits
Ventas desde kits = CALCULATE ( [Ventas], v_ventas_bi[origen] = "KIT" )
% desde kits = DIVIDE ( [Ventas desde kits], [Ventas] )

-- Comparativos (la inteligencia de tiempo cuelga de dim_fecha, ya relacionada por fecha_venta)
Ventas mes anterior = CALCULATE ( [Ventas], DATEADD ( dim_fecha[fecha], -1, MONTH ) )
Var % = DIVIDE ( [Ventas] - [Ventas mes anterior], [Ventas mes anterior] )

-- Devoluciones del periodo (por el mes en que se EMITIÓ la NC, no el de su factura).
-- Usa la fecha propia del documento vía la relación INACTIVA con fecha_factura.
Notas credito emitidas =
    CALCULATE ( [Ventas],
        v_ventas_bi[tipo_movimiento] = "out_refund",
        USERELATIONSHIP ( dim_fecha[fecha], v_ventas_bi[fecha_factura] ) )
```
> `[Notas credito emitidas]` requiere la **relación inactiva** entre `dim_fecha[fecha]` y
> `v_ventas_bi[fecha_factura]` (§1).

Para el **detalle por producto** pon en el eje `dim_producto` (relación por `componente_id`); para el
**catálogo tal como se vende** pon la **2.ª copia** de `dim_producto` (relación por `producto_id`). La
medida `[Ventas]` es la misma en ambos.

---

## 6. Diferencias esperadas contra el Excel (`base_ventas`)

`python validar_ventas.py` concilia mes a mes y las cuantifica. Las dos causas normales:

1. **Notas crédito.** El Excel ya viene **neto**, pero su cruce solo resta la NC cuyo `ref` casa con
   una factura-producto; **las que no casan se descartan** (no quedan en ningún mes). El DW resta
   todas → queda más bajo y es el correcto. Ej. jun-2026: el DW resta 213,9M (`RFEX2` 200,8M…) que el
   Excel no restó.
2. **Mes de la nota crédito.** El DW atribuye la NC al mes de **su factura** (`fecha_venta`); el Excel
   no lo hace de forma consistente. Por eso al conciliar por `fecha_venta` los meses con muchas NC
   cruzadas (mar/abr-2026) **divergen más** del Excel: no es un error del DW, es que el Excel omite
   esas NC. Si quieres una comparación "manzana con manzana" contra el Excel, agrupa por
   `fecha_factura`; para el **número correcto de ventas**, usa `fecha_venta`.
   > ⚠ **`fecha_factura` sirve para comparar, NO para reportar.** Reproduce el error del Excel.
3. **Timing**: el CSV es una foto; el DW sigue cargando **cada 15 min**.
4. **Notas débito.** Ya **no cuentan como venta**, salvo las que **anulan una nota crédito** (y esas van
   al mes de la factura que reviven). No hay nada que decidir en el visual: el SQL ya aplica la regla.
   Ver §6.5.

### Caso trabajado: el salto mar/abr-2026 (por qué `fecha_venta` es el correcto)

Síntoma original: medir por `fecha_venta` en vez de `fecha_factura` movía **−573M** en marzo y
**+653M** en abril. Toda esa diferencia era **UNA** nota crédito:

| | fecha | importe | |
|---|---|---|---|
| `FE7301` | 09-mar-2026 | **+662,2M** | factura NOVAVENTA |
| `RINV254` | 28-abr-2026 | **−662,2M** | su **anulación total** (mismas 18 líneas) |

- Por **`fecha_venta`**: las dos caían en marzo y **neteaban a 0**. ✅ Correcto: esa venta no existió.
- Por **`fecha_factura`**: marzo se quedaba una venta **fantasma** de +662M y abril un crédito fantasma
  de −662M. ❌ Ese es el número equivocado, aunque coincida con el Excel.
- El Excel falla porque casa NC↔factura por `ref`+producto y **descarta las que no casan**; `RINV254`
  tiene `referencia` NULL → el Excel la tira y deja los 662M fantasma en marzo.

**Hoy el par ya no aparece en ventas**: `marcar_reversos_puente` detecta las anulaciones totales que
Odoo dejó sin `reversed_entry_id` (usando el puente NC) y las marca `es_reverso`, así que salen de la
vista en vez de solo netear. Marzo pasó de bruto 8.252,9M a **7.307,8M** con el **neto intacto**
(7.297,0 → 7.298,6M). Igual con `FE9565`/`FE9570`/`FE9576` (mar-2026), anuladas por
`RINV/2026/0101/0100/0098`: el Excel las cuenta por su valor completo, el DW ya no.

⇒ Por eso marzo-2026 queda **por debajo del Excel** y no es un error: son facturas **anuladas** que el
Excel sigue contando (`FE7301` 662,2M + `FE9576`/`FE9570` 278M ≈ 941M), compensadas en parte por
`NDY1` (+612,9M, §6.5) → residuo **−339M (−4,0%)**.

Auditalo con **`python diagnosticar_fecha_venta.py`** (integridad de la columna, efecto mes a mes, NC
responsables con su `metodo_enlace`, anulaciones no marcadas y notas débito).

### 6.5 Notas débito: solo cuentan si anulan una nota crédito

**Ventas = facturas − devoluciones.** Una nota débito (diario `NDY`/`NDEXP`) es un cargo extra, así que
**no es venta**… salvo cuando **anula una nota crédito**: si la devolución se anuló, no hubo devolución
y hay que reponer ese valor **en el mes de la factura original**.

Cadena **ND → NC → FACTURA** (los tres documentos por 696.586.553):

| doc | fecha | tipo | `ref` |
|---|---|---|---|
| `FE7281` | 09-mar-2026 | factura | `OC 4503324138` |
| `RINV/2026/0062` | 09-mar-2026 | nota crédito | `Reversión de: FE7281, CORRECCION DE FACTURA` |
| `NDY1` | 24-abr-2026 | **nota débito** | `RINV/2026/0062, anulación nota crédito` |

`NDY1` sumaba **+612,9M en ABRIL**; ahora suma en **MARZO** (`fecha_venta` = 09-mar-2026). Efecto:
marzo 7.297,0 → **7.887,2M** y abril 9.639,8 → **8.823,8M**. Era la segunda mitad del salto mar/abr.

- El puente es `marts.map_nd_factura` (`sql/marts/25_nd_factura.sql` + `enlazar_notas_debito`).
- `es_nota_debito` sigue en la vista, pero ahora marca **solo las ND que sí son venta**.
- Las que quedan fuera están en **`marts.v_notas_debito_excluidas`** con el documento que referencian:
  cargos extra (`NDY4` 49,2M "FE7281, Ajuste por precio"), ND sin `ref`, y ND que anulan una NC que no
  se pudo enlazar a ninguna factura (esas tampoco restaban → la exclusión es simétrica).

#### ⚠ La simetría se rompía por una puerta lateral (arreglado 2026-08-01)

La NC quedaba fuera, sí — pero **su efecto colateral sobrevivía**: `marcar_reversos` la seguía
sumando para decidir si su factura estaba anulada.

**Caso `FVX1`** (12-jun-2024, DISTRIBUIDORA LEOPHARMA, **+159.225.366**, empresa HFA):

| doc | fecha | importe clase 4 | |
|---|---|---:|---|
| `FVX1` | 12-jun-2024 | +159.225.366 | la factura |
| `RFEX/2025/0001` · `RFEX/2025/0002` | 14-ene-2025 | −174.115.446 c/u | reversiones (`reversed_entry_id` = FVX1) |
| `NDEXP1` · `NDEXP2` | 14-ene-2025 | +174.115.446 c/u | **las cancelan al peso** — sin `ref`, fuera de ventas |
| `RFEX/2025/0003` | 31-ene-2025 | −49.441.252 | crédito comercial real (1,88 USD/und contra 6,35) |

Los cuatro documentos de enero **netean cero** y la factura nunca se anuló. Pero `ncr` sumaba
−348.230.892 contra +159.225.366 → **cobertura 2,187 ≥ 0,99** → `es_reverso=TRUE`. Resultado: de
todo el bloque, lo único que entraba en ventas era el negativo de `RFEX/2025/0003`, repartido por
conciliación **94,6 % a junio-2024**. Junio en exportación daba **−46.788.256** en vez de
**+111.049.235**, y `conciliar_odoo_ventas --anio 2024` fallaba (junio −12,06 %, anual −1,52 %).

**El arreglo** (`_SQL_REVERSOS`, CTE `nc_muerta`): una NC cancelada por una ND **sin `ref`** no
cuenta en `ncr`, y se marca `es_reverso` igualmente — así sale de ventas junto con su ND, que es
lo que la simetría pedía desde el principio.

- ⚠ **Solo se aplica a las ND SIN `ref`.** Con `ref` manda el enlace documental; emparejar a
  ciegas por tercero+fecha+importe discrepa de él en **4 de 15** casos comprobables. De las 45 ND
  del almacén solo 3 no traen `ref`: `NDEXP1`, `NDEXP2` y `NDY1`.
- ⚠ **NO poner cota superior a la cobertura.** Hay 8 facturas con la reversión **duplicada** en
  Odoo (cobertura ~2,0, sin ND que la cancele) que hoy se excluyen con sus dos NC y netean 0, que
  es lo correcto. Con una banda `[0,99 , 1,01]` dejarían de excluirse y restarían de más.
- **Efecto medido** (ensayo en transacción revertida, con control): cambian **exactamente 2
  documentos**, `FVX1` y `FEVY35821`, y **+158.984.550** en ventas (jun-2024 +157.837.491,
  abr-2025 +1.147.059). 2024 pasa a cuadrar: junio **−2,78 %**, anual **−0,69 %**.
- ⚠ **Punto ciego de `diagnosticar_fecha_venta.py` §4**: solo busca *anulaciones que `es_reverso`
  NO detecta* (falsos negativos). Esto era un **falso positivo** y por eso lo daba por limpio.
  Pendiente: añadirle la comprobación inversa.
- **Frente abierto, sin tocar**: 6 facturas donde **una sola NC excede** la que dice reversar
  (`FEVY24922` 5,06× · `FEVY1755` 3,67× · `FVE2642` 3,49× · `FEVY32543` 2,04× · `FEVY1769` 3,18× ·
  `FEVY2750` 1,51×). Probablemente esa NC cubre varias facturas y Odoo nombra una: se anula esa y
  se excluye la NC entera, con lo que ~42,9M de crédito desaparecen e **inflan** las ventas.
  Hipótesis sin confirmar — cruzar con contabilidad antes de tocar nada.
- 2026: **21 de 44** ND son venta.
- ⚠ La visión **contable** (`v_ventas`, `v_balance_comprobacion`, `v_exportaciones`) **sí** las lleva:
  ahí una nota débito es ingreso. La diferencia contra "ventas comerciales" es esperada.

---

## 7. Checklist antes de publicar

- [ ] ¿El valor lo sumas con **`venta_componente`**? (nunca `cantidad_neta`, que es de nivel kit)
- [ ] ¿La relación **activa** con el calendario es por **`fecha_venta`** (y `fecha_factura` inactiva)?
- [ ] ¿Importaste **solo `v_ventas_bi`** (no el hecho contable ni `v_ventas_explotada`) para ventas?
- [ ] ¿Importaste **`dim_producto` dos veces** (componente_id activa + producto_id para el kit)?
- [ ] ¿Están las **dos empresas** incluidas (ojo enero, que se facturó en la empresa 1)?
- [ ] ¿Usaste `categoria` (cliente, en la vista) y no `producto_categoria` (en `dim_producto`)?
- [ ] Si **comparas** contra el Excel: ¿agrupaste por `fecha_factura` (§6.2)? Para **reportar**,
      siempre `fecha_venta` — `fecha_factura` reproduce el error del Excel.
- [ ] Las **notas débito** ya las filtra el SQL (§6.5): solo entran las que anulan una NC, y en el mes de
      la factura. No hace falta filtrarlas en DAX.
- [ ] ¿Filtraste kits con `dim_producto.es_kit` (son 39 kits reales, no los 139 productos fabricados)?

---

## 8. Resumen de columnas de `v_ventas_bi`

| Columna | Qué es | Uso |
|---|---|---|
| **`venta_componente`** ⭐ | Valor asignado a cada componente/producto | **El valor** (suma siempre esta) |
| `cantidad_componente` | Unidades del producto (kit repartido) | Unidades por producto |
| ⚠ `cantidad_neta` | Nivel **kit**, **REPETIDA** por componente | Solo vía `[Kits vendidos]`; **no** sumar directo |
| `componente_id` | Producto individual (FK) | Relación activa → `dim_producto` (vista por producto) |
| `producto_id` | Producto vendido (el **kit** si es kit) (FK) | Relación → `dim_producto` 2.ª copia (tal como se factura) |
| `origen` | `INDIVIDUAL` o `KIT` | Distinguir/segmentar |
| `fecha_venta` ⭐ | Fecha de la factura original | Relación ACTIVA con `dim_fecha`; medir ventas |
| `fecha_factura` | Fecha propia del documento | Informe de NC por mes (relación inactiva) |
| `tercero_id` / `vendedor_id` / `empresa_id` | FKs | Relación → `dim_tercero`/`dim_vendedor`/`dim_empresa` |
| `categoria` | Categoría del **cliente** (canal) | Segmentar por canal (degenerada, en la vista) |
| `pais` / `equipo` / `cliente_analitico` | Atributos de la línea sin dim propia | Segmentar (degeneradas) |
| `numero_factura` / `factura_id` / `tipo_movimiento` | Identidad del documento | Drill / filtrar out_invoice vs out_refund |
| `es_nota_debito` | TRUE = nota débito que **anula una NC** (las demás ya no están en la vista) | Aislarlas si hace falta (§6.5) |

> Lo **descriptivo** (nombre de cliente, ciudad, producto, `producto_categoria`, vendedor, mes…) ya no
> está en la vista: viene de las **dimensiones** por relación. En `dim_producto`: `nombre_comercial`
> (nombre para mostrar, ej. DUTONIC — traducción es_CO de `product.template.name`), `nombre` (técnico base),
> `codigo`, `categoria`, `es_kit` (39 kits reales). `linea_id` **no es único** en la vista (kits
> explotados + NC prorrateadas): úsalo solo para deduplicar en medidas, no como clave de relación.
