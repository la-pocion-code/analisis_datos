# Guía — Reporting Financiero en Power BI (Board Deck)

Cómo recrear cada hoja del board deck (`Pocion_BoardDeck_Mayo2026`) en el modelo
`DASHBOARD POCION`, sobre el esquema estrella `marts …`. Aplica a **ambas empresas**
(1 = HFA Aristizábal, 8 = PCN Poción) vía slicer de empresa.

## Estructura de datos (resumen)
- **Hecho:** `marts fact_movimiento_contable` — grano: línea contable. Columnas clave: `saldo`
  (débito−crédito), `venta_neta` (crédito−débito), `debito`, `credito`, `tipo_movimiento`,
  `es_venta`, `es_reverso`, `producto_id`, `tercero_id`, `canal`, `empresa_id`, `fecha`.
- **Dimensiones:** `marts dim_cuenta` (PUC + columnas calculadas nuevas), `marts dim_tercero`
  (clientes/proveedores), `marts dim_empresa`, `marts dim_producto`, y `TABLA_CALENDARIO` (fecha).
- **Columnas calculadas en `dim_cuenta`** (creadas para el reporting):
  - `concepto_contable` — renglón P&G/Balance (por grupo/clase). Ordena por `orden_informe`.
  - `concepto_balance` — renglón detallado del balance. Ordena por `orden_balance`.
  - `categoria_gasto` — categoría de gasto admin/ventas (Servicios, Personal, Honorarios…).
- **Medidas:** todas en la tabla `_medidas_odoo` (prefijo `marts `). El P&G dinámico es
  `[odoo real pyg]`; el balance dinámico `[marts real dinamico esfinanciera]`.

### Filtros estándar de toda página
- **Empresa:** slicer sobre `marts dim_empresa[nombre]`.
- **Periodo:** slicer de año/mes sobre `TABLA_CALENDARIO`.
- El **P&G y flujos** son por periodo (mes/acumulado). El **Balance** es acumulado a la fecha
  (las medidas `marts valor balance` ya acumulan hasta el último día visible).

---

## Definiciones del P&G — gastos y depreciación (validado vs Odoo 2026-07-15)

El P&G replica el **reporte de Odoo id 38 "Estado de Resultados Mensual"** (es_CO). Estructura del
reporte: `51\(5160,5165)` = admin, `52` = ventas, `5160 + 5165` = línea de depreciación/amortización
aparte, `53` = no operacionales (incluye financieros 5305), `61` = costo de ventas.

**REGLA:** las medidas de gasto/depreciación se clasifican **por código PUC**
(`dim_cuenta[grupo_codigo]` + `[cuenta_codigo]`), **NO** por `dim_cuenta[nivel_movimiento]`.
Motivo: `nivel_movimiento`/`concepto`/`seccion` (derivados del reporte 38) **solo están poblados
para empresa 8 (PCN)**; las cuentas de empresa 1 (HFA) usan otro PUC y quedan sin clasificar, así
que basar las medidas en `nivel_movimiento` deja HFA en blanco. Por código funciona en ambas.
(Dato: HFA no tiene cuentas de grupo 51 — todo su gasto operativo va en grupo 52; su "admin" sale
en blanco y es correcto.)

Definiciones vigentes (tabla `_medidas_odoo`):

| Medida | Definición (filtro sobre `SUM(fact[saldo])`) |
|---|---|
| `marts gastos admin` | `grupo_codigo="51"` y `cuenta_codigo ∉ {5160,5165}` (excluye depreciación/amortización) |
| `marts gastos ventas` | `grupo_codigo="52"` (incluye dep/amort de ventas 5260/5265, igual que el reporte) |
| `marts depreciacion + amortizacion` | `cuenta_codigo ∈ {5160,5165}` — **línea del reporte** (solo admin) |
| `marts D&A total` | `cuenta_codigo ∈ {5160,5165,5260,5265}` — toda la D&A, para el **addback de EBITDA** |
| `marts gastos no operacionales` | `concepto_contable="GASTOS NO OPERACIONALES"` (grupo 53, **incluye** 5305) |

Fórmulas derivadas (coherentes con lo anterior):
- **Utilidad operativa** = (ingresos − costos) − gastos admin − gastos ventas − **línea D&A** (la dep
  admin se restó aparte al sacarla de gastos admin; el total no cambia).
- **EBITDA** = utilidad operativa + `marts D&A total` (readiciona TODA la D&A: admin 5160/5165 +
  ventas 5260/5265).
- **UAI** (`marts UT antes impuesto`) = UO − `gastos no operacionales` (grupo 53) + `ingresos no
  operacionales`. ⚠ **No** restar `marts gasto financiero` (5305) por separado: el grupo 53 ya lo
  incluye; hacerlo lo contaba dos veces (bug corregido).

**Validado (PCN empresa 8, ene–jun 2026):** gastos admin, gastos ventas, línea depreciación, EBITDA
y UAI cuadran al centavo con el reporte de Odoo.

### Líneas de subtotal, impuesto y utilidad neta (agregadas 2026-07-16)

El P&G (`[odoo real pyg]`) y su gemelo para % (`[marts real dinamico]`) resuelven cada renglón por
`SELECTEDVALUE(dim_cuenta[concepto_contable])` en un `SWITCH`. Renglones agregados:

| Renglón (concepto_contable) | Medida / valor |
|---|---|
| `GASTOS OP. Y DE VENTAS` | `[Gastos op. y de ventas]` = admin + ventas |
| `TOTAL OTROS INGRESOS` | `[marts total otros ingresos]` = `[marts ingresos no operacionales]` (grupo 42) |
| `TOTAL GASTOS NO OPERACIONALES` | `[marts total gastos no operacionales]` = grupo 53 completo |
| `IMPUESTO DE RENTA Y COMPLEMENTARIOS` | `[marts impuesto renta]` (impuesto REAL contabilizado, grupo 54) |
| `PROVISIÓN DEL IMPUESTO DE RENTA` | `[marts provision impuesto renta]` (estimación, ver abajo) |
| `UTILIDAD (PÉRDIDA) DEL PERIODO` | `[marts resultado del ejercicio]` = UAI − impuesto real |
| `UTILIDAD/NETA` | `[marts utilidad neta]` = UAI − provisión estimada |

Se muestran **los dos** impuestos (real grupo 54 + provisión estimada) y sus dos utilidades netas: el
impuesto de renta se paga **anual**, así que el grupo 54 suele estar vacío en meses intermedios y la
provisión estimada da la lectura mensual.

**Filas virtuales:** los renglones de subtotal/estimación no son cuentas de Odoo; son filas
"virtuales" (código en blanco) que se anexan a `dim_cuenta` vía la consulta M `concepto_cont_odoo`
(subtotales base) y `concepto_cont_extra` (`PROVISIÓN DEL IMPUESTO DE RENTA`, `UTILIDAD/NETA`), unidas
en la partición con `Table.Combine`. Su posición la da `dim_cuenta[orden_informe]` (columna calculada,
**tipo Decimal**): 5.1 Gastos op. y de ventas · 9.1 Total otros ingresos · 10.1 Total gastos no op. ·
12.5 Provisión · 15.5 Utilidad/Neta.

**Provisión del impuesto de renta — por empresa, con tope en 0:**
```
marts provision impuesto renta =
SUMX(VALUES('marts dim_empresa'[empresa_id]),
     VAR _uai = [marts UT antes impuesto]
     VAR _tasa = SWITCH('marts dim_empresa'[empresa_id], 1, 0.39, 8, 0.35, 0.35)
     RETURN IF(_uai > 0, _uai * _tasa, 0))
```
- **HFA (empresa 1) = 39%**, **PCN (empresa 8) = 35%** de la utilidad antes de impuestos.
- Si la UAI ≤ 0 → provisión = 0 (no hay provisión sobre pérdidas).
- El `SUMX` por empresa hace que el consolidado sume cada empresa con SU tasa (no mezcla).

**Guard de meses sin actividad:** `[odoo real pyg]` y `[marts real dinamico]` envuelven el `SWITCH`
en `IF(<ingreso operacional del periodo> > 0, SWITCH(...), BLANK())`. Sin esto, `provisión`
(`IF(_uai>0,...,0)`) y `utilidad neta` (`BLANK − 0 = 0`) devolvían **0** en meses sin datos y hacían
aparecer columnas de meses vacíos. Con el guard, en meses con ingreso operacional ≤ 0 **todas** las
filas quedan BLANK y la columna del mes desaparece.

---

## Hoja 1 — Estado de Resultados (P&G)
- **Visual:** matriz.
- **Filas:** `dim_cuenta[concepto_contable]` (ordenado por `orden_informe`). Filtrar filas P&G.
- **Valores:**
  - Mayo ($): `[odoo real pyg]` (mes seleccionado).
  - Mayo %V: `[marts % sobre ingresos]`.
  - Abril ($): `[marts valor mes anterior]`.
  - Acumulado 2026: `[marts real dinamico YTD]`.
- **Tarjetas KPI:** `[marts ER margen bruto %]`, `[marts ER gastos ventas %]`,
  `[marts ER resultado operacional %]`, `[marts ER margen neto %]`, `[marts ER EBITDA %]`.
- **Validado (PCN Mayo):** Ingresos 6.830M ✓, Costo 2.801M ✓, Utilidad Bruta 4.029M / 59,0% ✓.

## Hoja 2 — Comparación 2025 vs 2026
- **Visual:** matriz / barras.
- **Filas:** `concepto_contable`.
- **Valores:** 2026 `[odoo real pyg]`; 2025 `[marts Real Año Anterior]`; Var% `[marts var YoY %]`.

## Hoja 3 — Canales de Ventas
- **Visual:** dona / barras.
- **Leyenda/eje:** `fact[canal]` (Mayoristas, Catálogo, Farmacia, Distribuidores, Cliente Final…).
- **Valor:** `[marts ventas comerciales]` (ventas de producto netas de devoluciones).

## Hoja 4 — Top 10 Clientes
- **Visual:** barras.
- **Eje:** `dim_tercero[nombre]` con filtro Top N = 10 por el valor.
- **Valor:** `[marts ventas comerciales]` (o `[marts ingresos operacioneles]` para visión contable).

## Hoja 5 — Margen Cuentas Clave
- **Visual:** matriz.
- **Filas:** `dim_tercero[nombre]` (clientes clave).
- **Valores:** Ingresos `[marts ingresos operacioneles]`; Utilidad Bruta `[marts utilidad bruta cliente]`;
  Margen `[marts margen bruto %]`. (Costo atribuido por `tercero_id` directo; no se abren gastos op.)

## Hoja 6 — Gastos Administrativos y de Ventas por categoría
- **Visual:** matriz.
- **Filas:** `dim_cuenta[categoria_gasto]` (Servicios, Gastos de personal, Honorarios…).
- **Columnas:** meses (`TABLA_CALENDARIO[mes_nombre]`).
- **Valores:** `[marts gastos admin] + [marts gastos ventas]` (o una medida `gastos op` combinada),
  acumulado `YTD`, %part, y variación con `[marts var abs mes]` / `[marts var % mes]`.
- **Nota:** la categoría sale del 4º-5º dígito PUC (5135=Servicios, 5105=Personal, 5160=Depreciación…).
  ⚠ `[marts gastos admin]` **excluye** 5160/5165 (van en la línea de depreciación aparte); para ver
  la depreciación/amortización usar `[marts depreciacion + amortizacion]` o la categoría `Depreciaciones`/
  `Amortizaciones`.

## Hoja 7 — Detalle Top Proveedores
- **Visual:** matriz.
- **Filas:** `dim_tercero[nombre]`.
- **Filtro:** cuentas de gasto (grupo 51/52) — usar `concepto_contable IN {gastos admin, ventas}`.
- **Valores:** gasto por mes + total + %part.

## Hoja 8 — Otros Ingresos No Operacionales
- **Visual:** matriz.
- **Filas:** `dim_cuenta[nombre]` filtrando `concepto_contable="INGRESOS NO OPERACIONALES"` (grupo 42).
- **Valores:** `[marts ingresos no operacionales]` por mes + acumulado + %.

## Hoja 9 — Gastos Financieros y Otros Gastos
- **Visual:** matriz.
- **Filas:** `dim_cuenta[nombre]` filtrando `concepto_contable="GASTOS NO OPERACIONALES"` (grupo 53).
- **Valores:** `[marts gastos no operacionales]` (grupo 53 completo, **ya incluye** el financiero 5305).
  `[marts gasto financiero]` (código 5305) es solo un desglose informativo — ⚠ NO sumarlo aparte al
  total de no operacionales ni restarlo por separado en la UAI (se contaría dos veces).

## Hoja 10 — Estado de Situación Financiera (Balance)
- **Visual:** matriz.
- **Filas:** `dim_cuenta[concepto_balance]` (ordenado por `orden_balance`).
- **Columnas:** meses.
- **Valor:** `[marts valor balance]` (acumulado a la fecha, con signo por naturaleza; "Resultado del
  ejercicio" = utilidad neta del periodo P&L clases 4–7).
- **Validado (PCN Mayo):** Efectivo 3.567M ✓, CxC 10.437M ✓, Inventarios 7.584M ✓, CxC accionistas
  2.034M ✓, PPE 227M ✓, Capital+Superávit 4.219M ✓, Total Pasivos 13.585M ✓.

## Hoja 11 — Análisis Horizontal (mes vs mes)
- **Visual:** matriz P&L + Balance.
- **Valores:** actual `[odoo real pyg]` / `[marts real dinamico esfinanciera]`; anterior
  `[marts valor mes anterior]` / `[marts balance mes anterior]`; Var Abs `[marts var abs mes]` /
  `[marts balance var abs]`; Var % `[marts var % mes]` / `[marts balance var %]`.

## Hoja 12 — Estado de Flujo de Efectivo
- **Visual:** matriz.
- **Filas:** `concepto_contable` (líneas de flujo, orden 60–88).
- **Valor:** `[marts real dinamico Flujos de Efectivo]` por mes.

---

## Puntos a reconciliar con contabilidad (⚠)
1. **Gastos de ventas / Otros Activos / Resultado del ejercicio:** hay ~202M de diferencia entre el
   modelo (clasificación PUC por grupo) y el deck. El deck parece reclasificar manualmente ~202M
   (probable anticipo/diferido tratado como activo, no gasto). Revisar qué cuentas de grupo 52 deben
   ir a activo.
2. **Ventas comerciales vs Ingresos operacionales:** difieren ~79M (mayo PCN) = **notas débito** (~76M,
   diarios `NDEXP`/`NDY`) + descuentos financieros y NC sin producto (~3M), todos excluidos a propósito de
   la visión comercial. `marts ventas comerciales` solo cuenta líneas de producto (out_invoice/out_refund)
   y **excluye los diarios cuyo nombre empieza por "Nota Debito"**.
   ⚠ **Actualizado:** esa exclusión **ya la hace el SQL** (`v_ventas_producto` filtra los diarios
   `NDY`/`NDEXP`, salvo las ND que anulan una nota crédito, que cuentan en el mes de la factura que
   reviven — ver `docs/guia_bi_ventas.md` §6.5). El filtro DAX queda **redundante** (inofensivo, pero ya
   no es el que manda). La visión contable (`v_balance_comprobacion`, ingresos operacionales) **sí** las
   sigue llevando: la diferencia entre ambas visiones es esperada y es exactamente esto.
3. **Depreciación + amortización (CORREGIDO 2026-07-15):** la línea del reporte = **5160/5165 (admin)**,
   `[marts depreciacion + amortizacion]`, mostrada **aparte** de gastos admin (que ya la excluye), tal
   como el reporte de Odoo id 38. La dep/amort de ventas (5260/5265) queda dentro de gastos ventas
   (grupo 52). Para EBITDA se readiciona TODA la D&A con `[marts D&A total]` (5160/5165/5260/5265).
   Antes la medida usaba `{5160,5260,5265}` (omitía 5165 e incluía ventas) → causaba diferencias con
   Odoo en meses anteriores. Ver sección "Definiciones del P&G".

## Recordatorios técnicos
- Tras crear/editar columnas calculadas: **recalcular el modelo** (`Refresh → Calculate`).
- `orden_informe` y `orden_balance` se calculan **independientes de** `concepto_*` (por código) para
  poder usar *Sort by column* sin dependencia circular.
- Si se agregan conceptos nuevos, añadir su número en `orden_informe` / `orden_balance`.
