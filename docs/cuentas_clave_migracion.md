# Migración de CUENTAS CLAVE a `marts` (desde el Power Query del PBIX)

Contexto extraído del modelo **DASHBOARD POCION** (vía powerbi-modeling-mcp, 2026-07-23). Objetivo:
que Power BI **no lea archivos locales** de cuentas clave, sino tablas de `marts` que un job en
Railway actualice desde Google Drive. Hay 3 tablas del BI a reproducir:

- **`CUENTAS_CLAVE ANEXO`** — ventas: `Table.Combine` de 11 consultas (8 Colombia + 3 países).
  Al final agrega `ID_TIENDA = CLIENTE & NOMBRE_TIENDA` y limpia `NOMBRE_TIENDA` (Trim/Clean/Upper).
  Esquema común: `CLIENTE, FECHA, PRODUCTO, NOMBRE_TIENDA, UNIDADES` (+ según retailer: `CIUDAD,
  CANAL VENTA, VENDEDOR, VALORES, Campaña, TIENDA, NOMBRE_PRODUCTO`).
- **`INV CCLAVE ANEXO`** — inventarios: 8 consultas `INV_*`.
- **`TIENDAS_CCLAVE`** — catálogo de tiendas, derivado de las dos anteriores.

Todas las fuentes están en Drive: `carpeta_cuentas_clave/<Retailer>`, `carpeta_data/paises/<PAIS>`,
`carpeta_data/paises/INVENTARIOS`, `carpeta_cuentas_clave/inventarios`. El join maestro es
`BASE_CUENTAS_CLAVE` (ya en `marts.bi_cuentas_clave`), que trae `PRODUCTO` por códigos por retailer.

## VENTAS — 11 consultas (fuente → join → CLIENTE → fecha)
| Consulta | Carpeta (Drive) | Formato | Join a base_cuentas_clave | CLIENTE (legal) | FECHA |
|---|---|---|---|---|---|
| Farmatodo | Farmatodo | `.xlsx` (folder) | `VPN` = `vpn_farmatodo` | FARMATODO COLOMBIA SA | `Date.FromText("1 "&MES&" "&AÑO)` (cols) |
| Prosalon | Prosalon | `.txt` sep `|` | `ITEM` = `item_prosalon` | PROSALON DISTRIBUCIONES SAS | col `FECHA` |
| Pasteur | Pasteur | `.xlsx` | `PLU` = `plu_pasteur` | DISTRIBUIDORA PASTEUR S.A | col `fechaVenta` |
| Locatel | Locatel | `.csv` | `CODIGO SAP` = `locatel_cod_sap` | BRECCIA SALUD S.AS. | `1&MES&AÑO` (cols) |
| Novaventa | Novaventa | `.xlsx` | `Código` = `cod_novaventa` | NOVAVENTA S.A.S | por `Campaña` (≤18: `AddDays(1-ene, 20*camp-1)`; else `18*20-1`) |
| Surticosmeticos | Surticosmeticos | `.xlsx` (`Sell out - POCIÓN - <mes> <año>.xlsx`) + `historico_surticosmeticos.xlsx` (hoja `Tabla6`) | `EAN 13` = `SURTI_AEN` | SURTICOSMETICOS HF EU | unpivot de columnas-tienda; FECHA del archivo/histórico |
| LASKIN | LASKIN | `.xlsx` (skip 2 filas, promote headers) | `Item` = `Laskin_item` | LASKIN S.A | FECHA la agrega el helper por-archivo (del **nombre**) |
| Krika | Krika | `.xlsx` (promote headers) | `Desc. item` = `Krika` | LUCEGO SAS | FECHA del helper por-archivo (del **nombre**) |
| ecuador | paises/ECUADOR | **Python+openpyxl** (descarta filas con **fuente ROJA**; DEV → cantidades negativas; excluye vendedor/cliente ZARATE, muestras/publicidad) | `Código` = `cod_ecuador` (+ `Tipo de cliente_ecuador` para CANAL) | ZAR IMPORT ZARIMPORT S.A. | col `Fecha` |
| dominicana | paises/REPÚBLICA DOMINICANA | **Python** (localiza fila `Total`, arma header, `melt` de fechas, `ffill` clientes, segmenta por nombre) + `segmentacion_dominicana` | `Detalles.1` = `PRODUCTO - Copia.1` (con reemplazo `[7708162555347]→[PCN31]`), filtra `Detalles.3='POCION'` | DISTRIBUIDORA LEOPHARMA S.R.L. | del `melt` (mes año) |
| peru | paises/PERÚ | **Python** (`read_excel`, concat) + `kits` + `segmentacion_peru` | `Material` = `peru` (+ `Descr.Material`=`kits.KIT`) | DROGUERIA CORPORACION LIFE S.A.C. | col `Fec.Emis.Fact`; filtra `Descr.Clase Fact` ∈ {Boleta Kamill, Fact. Nacional, Factura Kamill}, `Anulado=''`, `Descr.CanalDist ≠ Interno` |

Helpers de apoyo (también en Drive/BASE): `Tipo de cliente_ecuador`, `segmentacion_dominicana`,
`segmentacion_peru`, `hist_surti`, `kits`. Las FECHAS de LASKIN/Krika/Surti salen del **nombre del
archivo** (helper `Transformar archivo (N)`) → al portar hay que parsear el nombre.

## INVENTARIOS — 8 consultas `INV_*` (→ `INV CCLAVE ANEXO`)
`INV_FARMATODO, INV_PASTEUR, INV_LOCATEL, INV PROSALON, INV_LASKIN` (Colombia) +
`INV_ECUADOR, INV_PERU, INV_RD` (países). Formatos heterogéneos (FARMATODO `.xls`/read_html,
PROSALON `.txt`, melt de columnas-tienda). Esquema común aprox: `PRODUCTO, TIENDA, COD_CLIENTE,
INVENTARIO, CLIENTE` (+ `MAXIMO` en Farmatodo). *(Pendiente extraer el M de cada `INV_*`.)*

## Plan de reproducción (job en Railway, fuente Drive → `marts`)
Nuevo módulo (p. ej. `cargar_cuentas_clave.py` o funciones en `cargar_bi_datasets.py`):
1. Descargar `bi_cuentas_clave` (BASE) a un DataFrame (ya está en `marts`).
2. Una función por retailer que lee su carpeta de Drive (`DriveLoader.list_folder` + `read_*`/bytes),
   aplica el transform de la tabla de arriba y devuelve el esquema común. Los países se **portan casi
   verbatim** del Python embebido (ya lo tienen); ecuador necesita `openpyxl` sobre los **bytes**
   descargados (para el color de fuente).
3. `pd.concat` de las 11 → `ID_TIENDA`, limpiar `NOMBRE_TIENDA` → `DBLoader.cargar('bi_cuentas_clave_ventas', schema='marts', if_exists='replace')`.
4. Igual para inventarios → `bi_inventario_cclave`; derivar `bi_tiendas_cclave`.
5. **Repuntar el PBIX** (vía MCP `partition_operations Update`): cambiar el `source` M de
   `CUENTAS_CLAVE ANEXO`, `INV CCLAVE ANEXO`, `TIENDAS_CCLAVE` de `Table.Combine({...local...})` a
   `Sql.Database(<DB_HOST>, <DB_NAME>){[Schema="marts", Item="bi_..."]}[Data]` (como ya lo hacen las
   tablas `marts *`). Así el BI deja de leer local y queda listo para refresh en servicio.

## Números de control (PBIX `CUENTAS_CLAVE ANEXO`, snapshot 2026-07-22) — validar cada port contra esto
| CLIENTE | filas | unidades |
|---|---:|---:|
| FARMATODO COLOMBIA SA | 28.227 | 212.665 |
| BRECCIA SALUD S.AS. (Locatel) | 4.291 | 18.016 |
| DISTRIBUIDORA PASTEUR S.A | 7.442 | 7.463 |
| PROSALON DISTRIBUCIONES SAS | 7.659 | 7.782 |
| NOVAVENTA S.A.S | 99 | 534.468 |
| SURTICOSMETICOS HF EU | 231 | 3.514 |
| LASKIN S.A | 1.292 | 2.354 |
| LUCEGO SAS (Krika) | 637 | 11.788 |
| DISTRIBUIDORA LEOPHARMA S.R.L. (dominicana) | 3.714 | 18.851 |
| DROGUERIA CORPORACION LIFE S.A.C. (peru) | 6.923 | 8.375 |
| ZAR IMPORT ZARIMPORT S.A. (ecuador) | **no aparece** | — |

**Estado del port (`cargar_cuentas_clave.py`):**
- ✅ **VENTAS 7/8 retailers reproducidos EXACTO** desde Drive vs control (farmatodo, prosalon, pasteur,
  locatel, novaventa −2 uds, dominicana, peru). Cargado en **`marts.bi_cuentas_clave_ventas`**
  (58.355 filas, 7 clientes; tipos numéricos). `python cargar_cuentas_clave.py cargar`.
- 🟡 **ecuador**: reproduce 26.332 / 67.776 uds, pero NO figura en el ANEXO actual (¿excluido? ¿otro
  CLIENTE?) y `SEGMENTO DE CLIENTES.xlsx` en Drive es un **acceso directo ROTO** → CANAL VENTA nulo hasta
  re-subir el archivo. Se carga con `cargar ecuador` cuando se decida.
- ✅ **VENTAS 10/10 retailers** (Surti/LASKIN/Krika añadidos; su FECHA es **columna del archivo**, no
  del nombre — hojas `BD`/`Ventas`/`Hoja1`). Todos EXACTO vs control. `marts.bi_cuentas_clave_ventas` =
  **60.515 filas** (esquema ampliado a las 15 columnas del ANEXO para repuntar sin romper).
- ✅ **INVENTARIOS 8/8** (INVENTARIO exacto vs control; peru difiere en filas por filas en cero,
  inmaterial). `marts.bi_inventario_cclave` = **4.303 filas**. `marts.bi_tiendas_cclave` = **1.155**.
  Comandos: `python cargar_cuentas_clave.py inventarios|tiendas|val_inv`.
- ✅ **REPUNTE del PBIX aplicado** (MCP `partition_operations Update`): `CUENTAS_CLAVE ANEXO`,
  `INV CCLAVE ANEXO`, `TIENDAS_CCLAVE` leen de `marts.bi_*` con las columnas renombradas a mayúscula
  (`cliente`→`CLIENTE`, `maximo`→`Máximo`, `campaa`→`Campaña`…). El M persistió.
  ⚠ **Actualización (conexión):** el origen ya **no** es el conector nativo
  `PostgreSQL.Database("switchback.proxy.rlwy.net:37790","railway")` sino el **DSN ODBC `pocion_marts`**
  (`Odbc.DataSource(...){[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_..."]}[Data]`), para que
  el refresco funcione en el Service (el cert de Railway es `CN=localhost` y rompía *verify-full*). Ver
  `docs/bi_refresco_gateway.md` y `docs/bi_conexiones_marts.md`.
- ⚠ **Refresh bloqueado por un problema PRE-EXISTENTE del modelo:** al commitear el refresh, Power BI
  falla con *"Cannot find table 'base pyg'"* (referencia rota a la tabla `base pyg` =
  `base_consolidada.csv`, que decidimos NO migrar) + un error de clave. Es ajeno a cuentas clave y ya
  existía. **Para cargar los datos:** arreglar/eliminar la tabla `base pyg` (o migrarla también a
  `marts.bi_base_pyg` y repuntarla) y luego **refrescar en Power BI Desktop**.
- Validación post-refresh (DAX): `SUMMARIZECOLUMNS(CLIENTE, COUNTROWS, SUM(UNIDADES/INVENTARIO))` debe
  dar los números de control de arriba.

## ⚠ Riesgos / notas
- Son **19 ETLs a medida** y frágiles al formato mensual (headers, fecha-en-nombre, color de fuente).
  Portarlos y validarlos es un trabajo dedicado; conviene hacerlo retailer por retailer con validación
  (comparar filas/uds contra la tabla actual del PBIX) antes de repuntar.
- La reproducción **duplica** lógica que hoy vive en Power Query; una vez repuntado a `marts`, esas
  consultas del PBIX quedan obsoletas (se pueden borrar tras validar).
