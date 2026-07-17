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
- **Valores:** `[marts gastos no operacionales]`; separar financiero (`[marts gasto financiero]`, código 5305).

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
3. **Depreciación + amortización:** se calcula por código (cuentas 5160/5260/5265); vive dentro de
   gastos admin/ventas (no se resta aparte salvo para EBITDA).

## Recordatorios técnicos
- Tras crear/editar columnas calculadas: **recalcular el modelo** (`Refresh → Calculate`).
- `orden_informe` y `orden_balance` se calculan **independientes de** `concepto_*` (por código) para
  poder usar *Sort by column* sin dependencia circular.
- Si se agregan conceptos nuevos, añadir su número en `orden_informe` / `orden_balance`.
