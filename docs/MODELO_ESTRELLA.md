# Modelo estrella — La Poción DW (tabla única de hechos contables)

Primera iteración del data warehouse en esquema estrella. Usa **una sola tabla de hechos** a
grano de línea contable (`account.move.line`, todos los `move_type`, `state='posted'`); en
Power BI se filtra "ventas" con `es_venta` / cuenta de ingreso (clase 4) y el resumen contable
se obtiene agregando por cuenta × mes. DDL en
[sql/marts/01_star_schema.sql](../sql/marts/01_star_schema.sql); vistas de apoyo en
[sql/marts/02_vistas.sql](../sql/marts/02_vistas.sql).

> **Estado:** diseño + DDL. Nada se ejecuta contra el DW sin OK explícito. Todo es aditivo en
> el esquema nuevo `marts`; no se modifica `raw.odoo_apuntes`, el cron ni archivos existentes.

## Convenciones de origen
- **[HOY]** — ya existe en `raw.odoo_apuntes` (lo trae el cron).
- **[EXTENDER]** — el campo existe en Odoo (plantilla `base_contable` sobre `account.move.line`)
  pero hay que añadirlo a la extracción siguiendo el patrón de `etl_odoo_incremental.py`, en una
  tabla/archivo NUEVOS (sin tocar el cron).
- **[AUSENTE]** — no está en `base_contable`; queda sin poblar por ahora.
- **[VERIFICAR]** — validar con un SELECT antes de confiar.
- **[PENDIENTE]** — decisión diferida (repartos analíticos).

## Jerarquía PUC (reutiliza `classes/clase_reportes_new.py:888-903`)
Por longitud de código: **N1** clase (1 díg), **N2** grupo (2 díg), **N4** cuenta (4 díg),
**N6** subcuenta (6 díg). Mapeo de `nivel_movimiento` por grupo (N2): 41=Ingreso Operativo,
42=Otros ingresos, 52=Gastos operacionales, 53=Gastos No Operacionales, 61=Costo directo de ventas.

## Distribución analítica
`analytic_distribution` llega de Odoo como JSON `{'id_cuenta_analitica': porcentaje}`; las claves
pueden ser compuestas (`'62,82,104'`) ⇒ varias cuentas/planes por línea. Modelado elegido:
**centro de costo = dimensión** (`dim_centro_costo`) y **planes comerciales** (Canal, Línea de
Producto, Tipo de Producto, País) = **columnas degeneradas** en el hecho. Cada id analítico se
resuelve a su plan/columna vía `account.analytic.account` ([EXTENDER]). Se conserva el JSON crudo
en `analytic_distribution` (JSONB) para reprocesar.
**[PENDIENTE] Repartos %:** cuando una línea reparte la distribución en varios porcentajes, por
ahora se toma la cuenta **dominante** (como hace hoy `clase_reportes_new.py:932-934`). Se revisarán
ejemplos reales para decidir si se prorratean las medidas (posible tabla bridge).

---

## 1. Mapeo fuente → destino — `fact_movimiento_contable` (grano = línea contable)

| Destino | Origen | Marca |
|---|---|---|
| `linea_id` (PK) | `account.move.line.id` | [HOY] |
| `factura_id` / `numero` | `move_id` / `move_name` | [HOY] / [EXTENDER] |
| `referencia` | `ref` | [EXTENDER] |
| `estado` / `tipo_movimiento` / `es_venta` | `move_id/state` / `move_id/move_type` | [EXTENDER] |
| `fecha_key` / `fecha_factura_key` | `date` / `invoice_date` | [HOY] → AAAAMMDD |
| `cuenta_id` (FK) | `account_id` | [HOY] |
| `tercero_id` (FK) | `partner_id` | [HOY] |
| `producto_id` (FK) | `product_id` | [EXTENDER] |
| `diario_id` (FK) | `journal_id` | [EXTENDER] |
| `vendedor_id` (FK) | `move_id/invoice_user_id` | [AUSENTE] |
| `centro_costo_id` (FK) | `analytic_distribution` → plan Centro de costos | [EXTENDER] / [PENDIENTE] |
| `canal`, `linea_producto`, `tipo_producto`, `pais_analitico` | `analytic_distribution` → plan respectivo | [EXTENDER] / [PENDIENTE] |
| `cantidad` / `precio_unitario` / `subtotal` | `quantity` / `price_unit` / `price_subtotal` | [HOY] |
| `debito` / `credito` / `saldo` | `debit` / `credit` / `balance` | [HOY] |
| `venta_neta` | `credit - debit` | [HOY] |
| `analytic_distribution` | `analytic_distribution` (JSON crudo) | [EXTENDER] |

## 2. Mapeo fuente → destino — dimensiones

| Dimensión | PK | Origen y marca |
|---|---|---|
| `dim_fecha` | `fecha_key` | Generada por SQL (`generate_series`); no depende de Odoo. |
| `dim_cuenta` | `cuenta_id` | `account_id` [HOY]; código parseado de `account_id_nombre` [VERIFICAR] o `account.account.code` [EXTENDER]. PUC por longitud. |
| `dim_tercero` | `tercero_id` | `partner_id` [HOY]; NIT (`x_studio_related_field_9er_1ipkj4lvp`) y `tipo_cliente` (`move_id/partner_type_id`) [EXTENDER]. |
| `dim_diario` | `diario_id` | `journal_id` + `account.journal` (code/name/type) [EXTENDER]. |
| `dim_producto` | `producto_id` | `product_id` + `product.product` (default_code/categ_id) [EXTENDER]. |
| `dim_vendedor` | `vendedor_id` | `move.invoice_user_id` (Salesperson). |
| `dim_centro_costo` | `centro_costo_id` | `account.analytic.account` (id, name, plan) + hoja `CC` de `base_cuentas.xlsx` (Nombre, ADM/VTAS, Origen, Tipo) — enriquecimiento pendiente. |
| `dim_empresa` | `empresa_id` | `res.company` (id, name): PCN Poción S.A.S. y Aristizabal Grisales Hector Fabio. El hecho lleva `empresa_id` (de `line.company_id`) para separar/filtrar por empresa. |

## 2b. Cartera (CxC) dentro del hecho único — **sin tabla aparte**
La cartera **no** es un hecho separado: sale del **mismo** `fact_movimiento_contable`. Las líneas
de CxC (`account_type='asset_receivable'`, cuenta clase 13) ya están en el hecho, y cada una trae
su saldo pendiente **a nivel de línea**:
- `saldo_pendiente` = `account.move.line.amount_residual`.
- `es_cxc` = `account_type='asset_receivable'` (marca las líneas de cartera).
- `fecha_vencimiento_key` = `date_maturity` (para aging).
Cartera = `SUM(saldo_pendiente)` sobre líneas `es_cxc`. Vista **`v_cartera`** (solo validación SQL):
líneas `es_cxc` con `saldo_pendiente <> 0`. DDL en `sql/marts/06_cartera_en_hecho.sql`.
Así se cumple **una sola tabla de hechos** en Power BI (ventas y cartera por DAX, sin duplicar).

---

## 3. Explicación por tabla
- **fact_movimiento_contable** — un registro por línea contable (todos los asientos posted).
  Sirve **ventas** (filtro `es_venta` + cuenta clase 4) y **resumen contable** (agregando por
  cuenta × mes) desde una sola tabla. Medidas aditivas (`debito`, `credito`, `saldo`, `cantidad`,
  `subtotal`, `venta_neta`); PK = id de la línea ⇒ recargas idempotentes por UPSERT.
- **dim_centro_costo** — centro de costo con atributos (Nombre, ADM/VTAS, Origen, Tipo).
- **dim_cuenta** — catálogo PUC con jerarquía resuelta y `nivel_movimiento` (base de balance y
  estado de resultados).
- **dim_tercero** — clientes; `tipo_cliente` = clasificación comercial; `identificacion` = NIT.
- **dim_diario / dim_producto / dim_vendedor / dim_fecha** — catálogos conformes; PK = id de Odoo
  (excepto `dim_fecha` = `fecha_key`).
- **Planes comerciales** (canal, línea producto, tipo producto, país) — columnas degeneradas en
  el hecho; pueden promoverse a dimensiones si llegan a necesitar atributos.

### Claves: surrogate vs id natural
Las dimensiones usan el **id natural de Odoo** como PK (coherente con el UPSERT por `id` del
`DBLoader`), sin capa de surrogate keys en esta iteración. `dim_fecha` usa `fecha_key` (AAAAMMDD).
El hecho es idempotente por `linea_id`.

---

## 4. Criterio de conciliación
1. **Partida doble** — por cada `factura_id`: `SUM(debito) = SUM(credito)`.
2. **Ventas ↔ `raw.odoo_apuntes`** — `SUM(venta_neta)` con `es_venta AND cuenta clase 4`
   (vista `v_ventas`) = misma agregación sobre `raw.odoo_apuntes` filtrando `codigo LIKE '4%'`.
3. **Balance de comprobación** — `SUM(debito) - SUM(credito)` por cuenta × mes
   (vista `v_balance_comprobacion`) reproduce el reporte de Odoo.
4. **Integridad referencial** — toda FK no nula existe en su dimensión; `centro_costo_id` sin huérfanos.

---

## 5. Proceso de carga — `etl_dw_marts.py` (implementado)

Script que puebla `marts` desde Odoo (XML-RPC), **por lotes** de 5.000 líneas para no agotar
memoria. Reutiliza el patrón del antiguo `etl_odoo_incremental.py` (hoy archivado) y `DBLoader`.
En Railway lo dispara el cron `run_dw.py` (horario). Modos:

| Modo | Comando | Qué hace | Cuándo |
|---|---|---|---|
| Inicial | `python etl_dw_marts.py --full` | Carga histórica completa (sin truncar) | Primera población |
| Incremental | `python etl_dw_marts.py --incremental` | Solo `write_date > marca_de_agua` (UPSERT idempotente) | Frecuente (p.ej. cada hora) |
| Recreación | `python etl_dw_marts.py --rebuild [--desde AAAA-MM-DD]` | **DELETE del rango + recarga** → refleja **borrados**. Por defecto **solo el año actual** (años cerrados intactos); `--desde` elige otra fecha | ~2×/mes + manual |

Cada corrida procesa **un solo hecho** `fact_movimiento_contable` (líneas contables), que sirve
ventas y cartera. Marca de agua: `account.move.line` (+ dims por su propio `write_date`).

- **Marca de agua:** tabla `marts.etl_control` (`modelo`, `ultimo_write`). El incremental lee de ahí
  y guarda el `MAX(write_date)` procesado. Ver `sql/marts/03_control.sql`.
- **Fidelidad ante borrados:** `write_date` no detecta eliminaciones/anulaciones en Odoo; por eso
  `--rebuild` (recreación total) se corre ~2 veces al mes (≈1 semana antes de fin de mes y unos días
  tras iniciar el mes) y bajo demanda, para mantener el marts fiel a Odoo.
- **Programación sugerida (cron):** `0 3 24 * *` y `0 3 3 * *` → `--rebuild`; `0 * * * *` → `--incremental`.
  Dónde corre (Railway vs Task Scheduler de Windows): a decidir; no se modifica `railway.toml`.
- **dim_centro_costo:** se puebla con `account.analytic.account` de los planes de centro de costo
  (root_plan_id ∈ {25 "Centro de costos", 3 "La Poción"}); enriquecer con la hoja `CC` de
  `base_cuentas.xlsx` queda pendiente (`adm_vtas`/`origen`/`tipo` en NULL por ahora).

### Notas de implementación (aprendizajes)
- Los ids/medidas se convierten a **tipos nativos de Python** antes del INSERT (psycopg2 no adapta
  escalares `numpy` → causaba un `bigint out of range` espurio).
- Columnas de texto libre (`referencia`, `numero`, `canal`, `linea_producto`, `tipo_producto`,
  `pais_analitico`) son `TEXT` (algunos `ref` superan 255 chars).
- Los campos de cabecera (`move_type`, `invoice_user_id`, `partner_type_id`) se leen de
  `account.move` aparte y se unen por `move_id` (XML-RPC no permite campos punteados en `fields`).

## 6. Hallazgos validados contra datos reales (2026-07-01)
- **[RESUELTO]** El código PUC **sí** viene embebido en `account_id_nombre` como prefijo antes
  del primer espacio (p.ej. `413538001 Venta de Cosmeticos gravado 19%`). El parseo
  `split(' ', 1)` → `(codigo, nombre)` es válido y la jerarquía N1/N2/N4 funciona.
- **[RESUELTO] Códigos PUC duplicados:** coexistían dos longitudes de código para la misma
  cuenta (p.ej. `413538001` de 9 díg y `41353801` de 8 díg, `cuenta_id` 4729 vs 7237) — migración
  del plan de cuentas en Odoo. Canonicalizado (§10, no destructivo).
- **[CONFIRMADO]** `raw.odoo_apuntes` trae **todas** las líneas del asiento de venta (CxC 13xx,
  retención 135x, IVA 240x, ingreso 413x, devolución 4175), no solo el ingreso ⇒ filtrar
  **clase 4** aísla las líneas de producto/ingreso y `venta_neta = credit − debit` captura las
  devoluciones (4175).

## 7. Ventas: exclusión de reversos totales (devoluciones)
Regla verificada con datos reales:
- **Reverso total (error):** la factura queda con `payment_state='reversed'`. Se marca
  `es_reverso=TRUE` en el hecho (para la factura y para su nota crédito de reverso), y **se excluye
  de ventas** (`v_ventas` y las medidas DAX). Así los pares de error suman 0.
- **Devolución parcial (real):** la factura sigue `paid`/`partial`; la nota crédito **resta** vía
  `venta_neta = credit − debit`. NO se excluye (se conserva el descuento del producto devuelto).
- Campos en el hecho: `estado_pago` (payment_state), `reversed_factura_id` (reversed_entry_id),
  `es_reverso`. Se recalcula en cada corrida (`marcar_reversos`).
- *Nota:* en cargas por rango, un reverso que cruza años (NC en año actual, factura en año cerrado)
  puede quedar parcialmente fuera del rango; la `--full`/`--rebuild` completa lo resuelve.

## 8. Calidad de datos y correcciones
- **`v_dq_analitica`** — anomalías a corregir en Odoo:
  - `PCT_DISTINTO_100`: algún reparto analítico con un valor ≠ 100% (cada plan debe ir al 100%;
    una línea puede tener varias entradas —una por plan— cada una al 100%).
  - `SIN_CENTRO_COSTO`: líneas de gasto/costo (clases 5/6) sin centro de costo. *Regla inicial;
    afinar qué cuentas realmente exigen CC.*
- **`marts.correcciones`** — overrides de datos mal registrados en Odoo aplicados en el DW
  (`aplicar_correcciones` tras cargar), sin tocar Odoo. Reproducible y auditable.

## 9. Consumo en Power BI (qué y cómo)
- **Conexión:** Power BI Desktop → *Obtener datos → PostgreSQL* (host/puerto/BD de Railway,
  variables `DB_*`), **modo Import**. Refresco programado con gateway en Power BI Service.
- **Tablas a importar:** las 8 dimensiones + **`fact_movimiento_contable`** (único hecho).
  (Las vistas `v_ventas`/`v_cartera`/`v_dq_analitica` quedan para validación SQL; con DAX no se importan.)
- **Relaciones estrella:** cada `*_id` del hecho → su dimensión; `fecha_key`→`dim_fecha`
  (y `fecha_factura_key`→`dim_fecha` como relación inactiva rol-playing).
- **Medidas DAX (esbozo):**
  - `Ventas netas = CALCULATE( SUM(fact[venta_neta]), fact[es_venta]=TRUE(),
    dim_cuenta[clase_codigo]="4", fact[es_reverso]=FALSE() )`.
  - `Cartera pendiente = CALCULATE( SUM(fact[saldo_pendiente]), fact[es_cxc]=TRUE() )`
    (aging por `fecha_vencimiento_key`; filtro de fecha del usuario, def. ≥ 2025-01-01).

## 9b. Refresco de dimensiones
- Catálogos pequeños (`dim_cuenta`, `dim_diario`, `dim_centro_costo`, `dim_empresa`) se refrescan
  **full en cada corrida** → centros de costo/empresas nuevos quedan al día.
- `dim_tercero`, `dim_producto`, `dim_vendedor` se refrescan por su **propio `write_date`**
  (`refrescar_dimensiones`): clientes/productos/vendedores nuevos o modificados se capturan aunque
  no tengan transacción nueva. `--dims` refresca solo dimensiones. Ver
  [GUIA_OPERACION.md](GUIA_OPERACION.md).

## 10. Canonicalización de códigos PUC duplicados — APLICADO (2026-07-08)

**Problema.** En Odoo coexisten **dos códigos para la MISMA cuenta** (variantes 8 vs 9 díg de la
migración del plan de cuentas). Sin unificar, los reportes agregados por cuenta se **parten** en dos.

**Cómo se detecta el duplicado genuino.** No basta el nombre ni la subcuenta por separado:
- *Por nombre solo* → NO: nombres genéricos ("OTROS", "COMISIONES") se repiten en grupos distintos
  (51 admón vs 52 ventas) y son cuentas **legítimamente diferentes**.
- *Por subcuenta (6 díg) sola* → NO: dentro de una subcuenta hay auxiliares reales distintas
  (`110505` = Caja Cali/Yumbo/Nequi/Epayco; `143504` = líneas de producto).
- **Regla aplicada = misma subcuenta (6 díg) `left(codigo,6)` + mismo nombre normalizado
  (`upper(trim(nombre))`).** Solo entonces son el mismo asiento con dos códigos.

**Canónico.** Dentro de cada grupo, la **variante MÁS usada en el hecho** (desempate: código más
corto, luego menor `cuenta_id`). La otra es el código legacy de la migración.

**No destructivo.** El hecho **conserva el `cuenta_id` real de Odoo** (fiel). Se añadieron a
`dim_cuenta` tres columnas, y para cuentas sin duplicado el canónico = sí mismas:
- `cuenta_canonica_id` (BIGINT) · `codigo_canonico` (VARCHAR) · `nombre_canonico` (TEXT).

**Alcance (datos reales).** 401 grupos duplicados; **423 `cuenta_id` colapsan** a su canónico;
`COUNT(DISTINCT codigo)` 1891 → `codigo_canonico` 1515; 0 cuentas sin canónico. Aplica a **todas
las clases** (incluida cartera, clase 1). Ej.: `130505001`+`13050501` → `130505001`
("CLIENTES NACIONALES"); `413538001`+`41353801` → `413538001`.

**Dónde vive.** `sql/marts/11_puc_canonico.sql` (idempotente) y `etl_dw_marts.py::canonicalizar_puc`,
que corre al cierre de cada carga (tras `aplicar_correcciones`, requiere el hecho ya cargado).

**Uso en Power BI.** Para reportes unificados por cuenta, **agrupar/relacionar por
`codigo_canonico` / `cuenta_canonica_id`** (no por `codigo`/`cuenta_id`). Para el detalle exacto de
Odoo, seguir usando `codigo`/`cuenta_id`.

## 11. Pendientes de decisión
- **[PENDIENTE]** Reglas exactas de `SIN_CENTRO_COSTO` (qué cuentas/clases exigen centro de costo).
- **[VERIFICAR]** Enriquecer `dim_centro_costo` con la hoja `CC` de `base_cuentas.xlsx`.
- **[INFO]** Repartos analíticos: siempre 100% por plan; un valor ≠ 100% es error (lo detecta `v_dq_analitica`).
