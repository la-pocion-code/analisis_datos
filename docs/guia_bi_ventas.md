# Guía — Ventas en Power BI desde el Data Warehouse

Cómo armar el reporte de ventas sobre el esquema `marts`, reemplazando el pipeline de Excel
(`ReportClassNew.pipeline_bi`). Recoge todas las reglas validadas contra el `base_ventas`.

Operación del DW: [GUIA_OPERACION.md](GUIA_OPERACION.md) · Modelo: [MODELO_ESTRELLA.md](MODELO_ESTRELLA.md)

---

## 1. Qué importar

Para ventas se importa **una sola tabla de hechos: `marts.v_ventas_explotada`**. Trae las columnas de
nivel kit **y** de componente + `origen`, así que con ella sola se presentan las **dos formas** (§2) —
sin volver a cargar el hecho contable (`fact_movimiento_contable`) ni `v_ventas_producto`.

| Objeto | Para qué |
|---|---|
| **`marts.v_ventas_explotada`** | **Único hecho de ventas.** Kits vendidos y unidades de producto. |
| `marts.dim_producto` | Producto, `categoria` de producto, `es_kit`. |
| `marts.dim_tercero` | Cliente: `nombre`, `identificacion`, `pais`, `ciudad`, `departamento`, `cliente_padre`. |
| `marts.dim_fecha` | Calendario. **Relacionar por `fecha_venta`** (ver abajo). |
| `marts.map_zona`, `map_cliente_padre`, `map_categoria` | Mapeos comerciales que NO están en Odoo. |
| `marts.v_exportaciones` | PyG de exportación por país y cliente (modelo aparte). |

Conexión PostgreSQL (variables `DB_*`), **modo Import**.
- **NO importes `fact_movimiento_contable` para ventas.** Ese hecho (4,3M filas, grano línea contable)
  es para **estados financieros** (clases 1–7). Las ventas ya vienen resueltas y filtradas en la vista;
  importar el hecho aquí duplicaría y no puede dar `fecha_venta` ni la explosión de kits.
- No hace falta importar `map_nc_factura`, `v_precio_componente` ni `dim_kit_componente`: ya vienen
  aplicados dentro de la vista.

### Relación con el calendario ⚠
Relaciona `dim_fecha[fecha]` con **`v_ventas_explotada[fecha_venta]`**, **no** con `fecha` ni
`fecha_factura`.

`fecha_venta` es la fecha de la **factura original**: hace que una nota crédito reste en el mes de la
venta que corrige. Si relacionas por la fecha contable (`fecha`/`fecha_key`) o por `fecha_factura`,
las ventas mensuales salen distintas y las notas crédito caen en el mes equivocado (ver §4.2).

---

## 2. Los kits: las DOS formas de ver las ventas (desde la MISMA tabla)

El kit se factura como **un producto con un valor único**. Ambas presentaciones salen de
`v_ventas_explotada`: **el valor siempre se suma con `venta_componente`** y solo cambia la **dimensión**.

| Presentación | Dimensión | Valor | Unidades |
|---|---|---|---|
| **Por producto** (kit repartido) | `componente_codigo` / `componente_nombre` | `SUM(venta_componente)` | `SUM(cantidad_componente)` |
| **Tal como se factura** (kit como unidad) | `producto_codigo` (el kit) | `SUM(venta_componente)` | `[Kits vendidos]` (§5) |

Como `venta_componente` es la parte de cada componente, al agrupar por el **kit** (`producto_codigo`) los
componentes se **vuelven a sumar al valor del kit** — por eso la misma medida sirve para las dos vistas
y el total **nunca se infla**. `origen` marca la fila: `INDIVIDUAL` (producto suelto) o `KIT` (componente
de un kit).

> ⚠ **TRAMPA — no sumes `venta_subtotal` ni `cantidad_neta`.** En la tabla explotada esas columnas son
> de **nivel kit** y están **repetidas** en cada fila de componente (una línea de kit = N filas). Si las
> sumas, cuentas el kit N veces. Verificado (emp 8, 2026): `SUM(venta_componente)` = **42.810M** ✅ vs
> `SUM(venta_subtotal)` = **55.753M** ❌ (+30%). Úsalas solo con `[Kits vendidos]`, que deduplica por línea.

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

`origen` distingue el tipo de fila en `v_ventas_explotada`: `INDIVIDUAL` (producto vendido suelto) o
`KIT` (componente que viene de un kit).

---

## 3. Reglas que ya vienen aplicadas en la vista

No hay que replicarlas en DAX; están dentro de `v_ventas_explotada`:

- **Ventas netas**: ingresos (clase 4) de facturas **y notas crédito**; las NC restan (`venta_componente`
  negativo). No se casa por `ref` como el Excel: el enlace NC→factura sale de la **conciliación** de
  Odoo, y por eso la NC ya viene fechada en el mes de su factura (`fecha_venta`).
- **Producto comercial**: `codigo` empieza por `PCN`/`KD`/`TNG`/`B8`.
- **`es_reverso`**: excluye **anulaciones reales** (factura + NC de reversión ≥99%). **No** excluye las
  pagadas por **factoring** ni las de **NC parcial** — esas son ventas reales.

## 4. Reglas que SÍ hay que respetar al construir los visuales

1. **Combina las dos empresas.** Ene-2026 se facturó en la **empresa 1** (HFA) y desde feb en la **8**
   (PCN). Filtrar una sola parte el año. Usa slicer de `empresa_id` solo si quieres verlas separadas.
2. **Agrupa las ventas por `fecha_venta`** (o `anio_venta`/`mes_venta`), **no** por `fecha_factura`.

   ### Las tres fechas y para qué sirve cada una
   | Columna | Qué es | Cuándo usarla |
   |---|---|---|
   | **`fecha_venta`** | Fecha de la **factura original**. Para una NC es la fecha de la factura que corrige. | **Ventas** (la venta neta real) |
   | `fecha_factura` | Fecha propia del documento (la NC lleva la suya) | Informe de **notas crédito por mes** |
   | `fecha` | Fecha **contable** del asiento | Conciliación contable / PyG |

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

Todas asumen la relación con `dim_fecha` por **`fecha_venta`** (§1).

Todas sobre `v_ventas_explotada`, relacionada con `dim_fecha` por **`fecha_venta`** (§1).
⭐ **El valor SIEMPRE se suma con `venta_componente`** (nunca `venta_subtotal`).

```DAX
-- VALOR — sirve para las dos vistas (cambia solo la dimensión del visual):
--   por componente_codigo → ventas por producto · por producto_codigo → ventas por kit
Ventas = SUM ( v_ventas_explotada[venta_componente] )

-- Unidades de PRODUCTO (kit repartido en componentes)
Unidades producto = SUM ( v_ventas_explotada[cantidad_componente] )

-- Cuántos KITS se vendieron (unidad = el kit). cantidad_neta está repetida por componente,
-- así que se deduplica por línea con MAX. Filtrar a es_kit para que tenga sentido.
Kits vendidos =
    SUMX ( VALUES ( v_ventas_explotada[linea_id] ),
           CALCULATE ( MAX ( v_ventas_explotada[cantidad_neta] ) ) )

-- Cuánto de la venta viene de kits
Ventas desde kits = CALCULATE ( [Ventas], v_ventas_explotada[origen] = "KIT" )
% desde kits = DIVIDE ( [Ventas desde kits], [Ventas] )

-- Comparativos (la inteligencia de tiempo cuelga de dim_fecha, ya relacionada por fecha_venta)
Ventas mes anterior = CALCULATE ( [Ventas], DATEADD ( dim_fecha[fecha], -1, MONTH ) )
Var % = DIVIDE ( [Ventas] - [Ventas mes anterior], [Ventas mes anterior] )

-- Devoluciones del periodo (por el mes en que se EMITIÓ la NC, no el de su factura).
-- Usa la fecha propia del documento vía una relación INACTIVA con fecha_factura.
Notas credito emitidas =
    CALCULATE ( [Ventas],
        v_ventas_explotada[tipo_movimiento] = "out_refund",
        USERELATIONSHIP ( dim_fecha[fecha], v_ventas_explotada[fecha_factura] ) )
```
> `[Notas credito emitidas]` requiere una **relación inactiva** entre `dim_fecha[fecha]` y
> `v_ventas_explotada[fecha_factura]`. Créala inactiva para no alterar las ventas.

Para el **detalle por producto** pon `componente_codigo` en el eje; para el **catálogo tal como se
vende** pon `producto_codigo` (el kit). La medida `[Ventas]` es la misma en ambos.

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
3. **Timing**: el CSV es una foto; el DW sigue cargando cada hora.

Ejemplo de control: `FE9565`/`FE9570`/`FE9576` (mar-2026) están 100% anuladas por
`RINV/2026/0101/0100/0098` → en el DW la factura suma y la NC resta (**neto 0**); en el Excel salen por
su valor completo.

---

## 7. Checklist antes de publicar

- [ ] ¿El valor lo sumas con **`venta_componente`** y NO con `venta_subtotal`? (evita el +30% de kits)
- [ ] ¿El calendario está relacionado por **`fecha_venta`** (no por `fecha` ni `fecha_factura`)?
- [ ] ¿Importaste **solo `v_ventas_explotada`** (no el hecho contable) para ventas?
- [ ] ¿Están las **dos empresas** incluidas (ojo enero, que se facturó en la empresa 1)?
- [ ] ¿Usaste `categoria` (cliente) y no `producto_categoria` para el canal?
- [ ] Si comparas contra el Excel: ¿agrupaste por `fecha_factura` (§6.2) en vez de `fecha_venta`?
- [ ] ¿Filtraste kits con `dim_producto.es_kit` (son 39 kits reales, no los 139 productos fabricados)?

---

## 8. Resumen de columnas clave (todas en `v_ventas_explotada`)

| Columna | Qué es | Uso |
|---|---|---|
| **`venta_componente`** ⭐ | Valor asignado a cada componente/producto | **El valor** (suma siempre esta) |
| `cantidad_componente` | Unidades del producto (kit repartido) | Unidades por producto |
| `componente_codigo` / `_nombre` | Producto individual | Eje para la vista **por producto** |
| `producto_codigo` / `producto` | Producto vendido (el **kit** si es kit) | Eje para la vista **tal como se factura** |
| `origen` | `INDIVIDUAL` o `KIT` | Distinguir/segmentar |
| `fecha_venta` ⭐ | Fecha de la factura original | Relación con `dim_fecha`; medir ventas |
| `fecha_factura` | Fecha propia del documento | Informe de NC por mes (relación inactiva) |
| `categoria` | Categoría del **cliente** (canal) | Segmentar por canal |
| `producto_categoria` | Categoría del **producto** | Segmentar por producto |
| ⚠ `venta_subtotal` / `cantidad_neta` | Nivel **kit**, **REPETIDAS** por componente | Solo vía `[Kits vendidos]`; **no** sumar directo |

> `es_kit` está en `dim_producto` (39 kits reales). `linea_id` **no es único** en la vista (kits
> explotados + NC prorrateadas): úsalo solo para deduplicar en medidas, no como clave de relación.
