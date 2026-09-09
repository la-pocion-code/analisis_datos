# Ajuste de «producto comercial»: cómo agosto pasa de 801 a 817 millones

**Runbook y handoff.** Este documento es la referencia para dos audiencias:

- **quien opera el DW** (repo `analisis_datos`): la secuencia exacta para aplicar el ajuste;
- **la intranet y su Claude** (repo `proyecto pocion/intranet`): qué va a cambiar en las cifras, por
  qué, y cómo explicarlo sin inventar nada.

> ## ✅ APLICADO EL 2026-09-09, PERO **NO** COMO ESTE DOCUMENTO LO PRESCRIBÍA
>
> El objetivo se cumplió —SHOPIFY agosto da **817.218.949,40**, el ancla del §1 al peso— y
> **sin tocar ni una ficha de Odoo**. Lo que se aplicó no es `categoría + disponible_pos`
> sino **`categoría` O `prefijo`**, las dos vías unidas por un `OR`, por tres motivos
> medidos que este documento no había considerado:
>
> 1. 🔴 **`disponible_pos` es TAN MUTABLE como el `default_code`.** Colgar la definición de
>    él repite el fallo que el documento existe para arreglar: desmarcar «Disponible en
>    PdV» mañana **borraría venta histórica** de informes ya publicados. Lo levantó William:
>    *«puede que el producto hoy esté desmarcado pero en el pasado sí se haya vendido…
>    esto no puede afectar los datos históricos»*.
> 2. 🔴 **Y no protegía de nada.** Medido en 2026, lo ÚNICO que el flag dejaba fuera era
>    `KIT MASCARILL SOS + BOOSTER` (48.070.864), que es producto de verdad. Lo demás que
>    excluiría —`Add On's` y `Sachet`— ya lo excluyen los dos `NOT LIKE`. Y el
>    `Descuento financiero en ventas` (−2.856 M) que el §3 dice que «hundiría las ventas»
>    vive en la categoría **`All`**: **la categoría sola ya lo excluye**.
> 3. 🔴 **La definición del §3 QUITABA venta que hoy se cuenta.** `PCNKIT16` (7.715.899) y
>    `PCNKIT39` (901.261) están en `All`, así que salían del tablero — y de ahí venían dos
>    de las tres fichas de Odoo: existían para no perder lo que ya estaba. Con el `OR` la
>    definición es **estrictamente aditiva** (`nueva ⊇ vieja` por construcción), así que
>    ningún producto puede perder su historia y el paso 1 **deja de ser necesario**.
>
> **La condición que corre hoy en `14_ventas.sql` (líneas 213 y 259):**
>
> ```sql
> AND (
>       (    p.categoria LIKE 'Inventario/Producto Terminado/%'
>        AND p.categoria NOT LIKE 'Inventario/Producto Terminado/Add On''s%'
>        AND p.categoria NOT LIKE 'Inventario/Producto Terminado/Sachet%')
>    OR (    p.codigo IS NOT NULL
>        AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%'
>          OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%'))
> )
> ```
>
> ⚠️⚠️ **Y ESTE DOCUMENTO SE EQUIVOCABA EN UNA COSA MÁS, que es la que alguien va a
> reportar: «2025 no se mueve» (§4.4) ES FALSO.** El ajuste es retroactivo a **todo** el
> histórico, y 2025 sube **más que 2026**:
>
> | año | antes | ahora | delta |
> |---|---:|---:|---:|
> | 2024 | 18.539.752.661 | 18.780.834.227 | **+241.081.566** (+1,30 %) |
> | 2025 | 81.716.031.975 | 82.418.538.975 | **+702.507.000** (+0,86 %) |
> | 2026 | 65.861.717.946 | 66.251.803.848 | +390.085.902 (+0,59 %) |
>
> ⭐ **Consecuencia contraintuitiva: la venta sube pero el CRECIMIENTO BAJA**, porque el año
> base también subió. SHOPIFY agosto pasa de **+19,1 % a +12,3 %** interanual. Quien mire
> el tablero va a ver *menos* crecimiento, no más.
>
> **Validado contra Odoo con el mismo criterio en los dos lados**
> (`intranet: manage.py conciliar_odoo_ventas`): **2026 al +0,47 %** (mejora, venía del
> +0,58 %) y **2025 al −0,32 %**, idéntico a lo que daba antes. Los dos años se movieron
> **juntos**: lo que entró es venta que Odoo también cuenta.
>
> ⚠️ El paso 3 se aplicó en **UNA transacción** con los seis ficheros, así que no hubo
> ventana sin `GRANT`. Y su §3 dice «6 MV»: son **7** — se le olvida
> `mv_ventas_presupuesto_mes`. El conjunto exacto sale del cierre transitivo de `pg_depend`,
> no de una lista escrita a mano.

Medido el **2026-09-09** contra Odoo en vivo y el DW. ⚠ El ETL corre cada 15 min: las cifras de un
mes en curso se mueven; agosto ya está cerrado y es estable.

---

## 1. El resultado: las tres cifras de SHOPIFY-agosto

Medición directa sobre el hecho, replicando cada definición (sin IVA):

| escenario | venta agosto | con IVA (factor 1,19 medido) |
|---|---:|---:|
| **hoy** — definición por prefijo del código | 801.017.715 | 953.211.070 |
| definición nueva, **Odoo sin arreglar** | 807.660.295 | 961.115.751 |
| ⭐ **definición nueva, Odoo arreglado** | **817.218.949** | **972.490.549** |

⛔ **Los 817 millones NO salen solo de cambiar el SQL.** Sin las 3 fichas de Odoo del paso 1, agosto
se queda en **807.660.295**, porque:

- `KIT MASCARILL SOS + BOOSTER` sigue **sin marcar en PdV** ⇒ no entra (2.881.662 en agosto);
- `PCNKIT16` está en categoría `All` sin PdV ⇒ **saldría** del tablero, y hoy sí está dentro
  (5.775.731 en agosto).

Efecto en **todos los canales, 2026 completo**:

| escenario | venta 2026 |
|---|---:|
| hoy | 65.791.561.395 |
| nueva, Odoo sin arreglar | 66.124.959.273 (+333.397.878) |
| ⭐ nueva, Odoo arreglado | **66.181.647.297 (+390.085.902)** |

---

## 2. La causa raíz, en cinco líneas

El **18 y 19 de agosto de 2026** alguien editó el catálogo de Odoo y **quitó el `default_code` a
9 kits** (en 2 casos lo pasó a una ficha nueva). `v_ventas_producto` identificaba el «producto
comercial» por el **prefijo de ese código** (`PCN%/KD%/TNG%/B8%`), así que **2.726 facturas de enero
a agosto dejaron de contarse hacia atrás**, sin que nadie tocara una venta: **390.085.902 sin IVA**.

**La prueba de que el código existía:** Odoo escribe el concepto de cada línea como
`[default_code] nombre` **al crearla** y no lo recalcula. En las 2.764 líneas de 2026 de esos kits el
concepto trae el código en **todas** (`[PCNKIT16]` 1.007 líneas, `[PCNKIT17]` 576, `[PCNKIT22]` 92…)
y **coincide exacto con el SKU de Shopify (7 de 7 comprobables)**.

⭐ **Nada se facturó mal ni se despachó mal.** Trazado el pedido `#157143`: `sale.order S179675` con
`delivery_status = full`, factura `FE49172` validada, albarán `MCALI/OUT/09457` en `done` el 1-ago
12:05, y de bodega salieron los 4 componentes del kit phantom (`PCN25`, `PCN01`, `PCN04`, `PCN19`).
**Lo que se rompió fue el reporte.** Detalle completo en `dashboards_intranet.md` §10.7-10.9.

---

## 3. Los pasos, en orden

### Paso 1 — ⛔ EN ODOO (bloquea todo lo demás). 3 fichas.

| ficha | qué hacer | en juego (2026) |
|---|---|---:|
| `PCNKIT16` Kit Control grasa y crecimiento | mover a categoría **`Inventario/Producto Terminado/Kits`** + marcar **Disponible en PdV** | 7.715.899 |
| `PCNKIT39` Kit Mascarilla S.O.S | lo mismo | 901.261 |
| `KIT MASCARILL SOS + BOOSTER` (archivada) | marcar **Disponible en PdV** | 48.070.864 |

⚠ **Que estén archivadas no impide marcarlas, y no hay que desarchivar nada**: la definición nueva
no mira `active` (decisión de negocio: el histórico se conserva).

**Comprobar antes de seguir:**

```sql
-- Las 3 tienen que salir con categoría PT/... y disponible_pos = true
SELECT codigo, nombre, categoria, disponible_pos
FROM marts.dim_producto
WHERE codigo IN ('PCNKIT16','PCNKIT39') OR nombre = 'KIT MASCARILL SOS + BOOSTER';
```

⚠ Antes de la consulta hay que traer el cambio de Odoo al DW:
**`python etl_dw_marts.py --backfill-productos`** (segundos; `--dims` **no** sirve, va por
`write_date`).

### Paso 2 — cambiar la definición en el SQL

En **`sql/marts/14_ventas.sql`**, en la línea 213 y **también en la 259** (que repite la condición
para `v_nc_sin_asignar`), sustituir el bloque del prefijo por:

```sql
AND p.categoria LIKE 'Inventario/Producto Terminado/%'
AND p.categoria NOT LIKE 'Inventario/Producto Terminado/Add On''s%'
AND p.categoria NOT LIKE 'Inventario/Producto Terminado/Sachet%'
AND p.disponible_pos IS TRUE
```

⛔ **El flag solo NO sirve, y las dos exclusiones no son adorno:** `Descuento financiero en ventas`
tiene `available_in_pos = true` y vale **−2.856.014.089** en 2026. Definir «comercial» solo por el
flag hundiría las ventas. Y los dos `NOT LIKE` cubren el error humano: hay 2 add-ons mal etiquetados
(`ADD12`, `ADD01`) que hoy no venden, pero uno con venta sí entraría.
⛔ **Nada de `active`.** Filtrarlo borraría la venta histórica de cualquier producto que se archive
después, y un informe de un mes cerrado cambiaría de cifra solo con el tiempo.

### Paso 3 — recrear lo que cuelga de la vista

Recrear `v_ventas_producto` obliga a soltar **6 MV y 3 vistas** y volver a aplicar, **en este orden**:

```
14_ventas.sql → 15b_kits.sql → 21_ventas_bi.sql → 23_mv_dashboards.sql
→ 27_ventas_dashboards_fase2.sql → 24_rol_intranet.sql
```

⛔ **El 24 va al final y no es opcional:** los `GRANT` a `intranet_ro` se pierden al recrear las MV,
y sin él la intranet responde *relation does not exist*.
Medido: **~85 s** en total. Después, refrescar: `python refrescar_mv_dashboards.py`.

---

## 4. Verificación

1. ⛔ **Nada que se venda desaparece.** Los 9 kits archivados con venta **y** `PCNKIT16`/`PCNKIT39`
   aparecen todos. Es la regla del negocio: *si se está vendiendo, se debe mostrar*.
2. ⛔ **`Descuento financiero en ventas` NO entra.** Es lo que protege las ventas de un −2.856 M.
3. **Agosto llega a la cifra objetivo:** `mv_ventas_mes` para SHOPIFY y `periodo_factura_aaaamm =
   202608` da **817.218.949** sin IVA / **~972.490.549** con IVA. Si sale 807.660.295, el paso 1 se
   quedó a medias.
4. **2025 no se mueve.** Ningún producto archivado pierde su histórico.
5. **Infra:** las 6 MV con `ok = true` en `bi_mv_refresh`, `intranet_ro` leyéndolas y el hecho,
   `dim_*` y `v_ventas_bi` **negados** (`has_table_privilege`).
6. **Auditor:** `python diagnosticar_producto_comercial.py` antes y después; el salto tiene que ser
   el de la tabla del §1 y no otro.

---

## 5. ⚠ Lo que la intranet y su Claude tienen que saber

**Las cifras de ventas SUBEN, y no es un error de carga.** Cuando esto se aplique:

- **agosto de Shopify: 953.211.070 → ~972.490.549 con IVA** (+19,3 M);
- **2026 completo, todos los canales: +390.085.902 sin IVA**;
- el salto afecta a **enero–agosto de 2026**, no solo a agosto.

**Cómo explicarlo si alguien pregunta por qué cambió:**

> «Se recuperó venta que estaba facturada, cobrada y entregada pero no se mostraba: a 9 kits les
> habían quitado el código interno en Odoo el 18 de agosto, y el tablero identificaba el producto
> comercial por ese código. Ahora se identifica por la categoría de producto terminado y la casilla
> de disponible en punto de venta, que no dependen de un campo que alguien pueda vaciar.»

**Lo que NO hay que hacer, y por qué:**

| ⛔ no hacer | por qué |
|---|---|
| Restar las devoluciones de `venta` | `venta` **ya es neta**. `mv_ventas_devoluciones_mes` es informativa (§10.10) |
| Comparar la venta del tablero con el `Total` del panel de Shopify | el `Total` lleva **flete**, que Odoo no factura (~34,2 M/mes). Ver §10.7 |
| Promediar la columna de tasa de devolución | una tasa no se promedia: agregar y luego dividir |
| Sumar `mv_ventas_producto` y `mv_ventas_explotada` | es el mismo dinero visto de dos formas |
| Volver a colgar el producto comercial del `default_code` | es un campo **mutable**: al vaciarlo se reescriben informes ya publicados. Es exactamente lo que causó esto |
| 🔴 Colgar el producto comercial de **UN SOLO** campo, sea cual sea | la lección que este documento no había sacado de su propio diagnóstico: `disponible_pos` es igual de mutable que `default_code`. Con **dos vías unidas por OR** hay que romper las dos para perder historia |
| Comparar el **total** de un año como ancla absoluta | septiembre está abierto y el ETL carga cada 15 min: el absoluto deriva solo. Ancló un falso rojo de +17.240.050 que eran ventas nuevas. Lo que no deriva es el **delta** entre definiciones |

**Y una regla de catálogo que hay que respetar en Odoo, no en el código:**

- **No vaciar el `default_code` de un producto con histórico de ventas.** Si hay que reemplazar la
  ficha, la nueva nace **con su categoría y su PdV** y la vieja se archiva **conservando el código**.
- Toda ficha nueva de kit nace en `Inventario/Producto Terminado/<Línea>` **con Disponible en PdV
  marcado**. Si nace en `All` sin PdV, el tablero no la ve.

---

## 6. Estado

| | |
|---|---|
| Columna `dim_producto.disponible_pos` | ✅ creada y poblada (`36_producto_comercial.sql`, `--backfill-productos`) |
| MV de devoluciones | ✅ creada, concedida y refrescándose (`37_devoluciones_dashboards.sql`, §10.10) |
| MV de devoluciones — **refrescándose de verdad** | ✅ desde el 2026-09-09: su DDL no estaba en `main`, así que el cron (que corre `main`) no la refrescaba y llevaba **1.286 min** parada. Mergeado en `6d242e3` |
| Paso 1 — las 3 fichas de Odoo | ✅ **YA NO HACE FALTA**: con `categoría OR prefijo` la definición es aditiva y no pierde `PCNKIT16`/`PCNKIT39`. Las 3 fichas siguen como estaban (0 de 3) **y da igual** |
| Paso 2 y 3 — el cambio de definición | ✅ **APLICADO el 2026-09-09**, en una transacción, con las anclas verificadas dentro y `COMMIT` solo al cuadrar |
| Tool del MCP de devoluciones | ✅ hecha (`intranet@fdae42d`), y en producción vía `14ab2a2` |
| `bi_nielsen_market.tiene_distribucion` | ✅ commiteada: no estaba en **ningún** commit, solo en el árbol de trabajo |
