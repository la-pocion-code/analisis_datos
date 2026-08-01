# BI `DASHBOARD POCION` — Código M para conectar cada tabla a `marts` (Postgres vía ODBC)

> **Por qué esto:** los cambios hechos por herramientas externas (MCP) al modelo **NO se guardan** en el
> `.pbix`; al guardar/reabrir, Power BI Desktop reescribe su propia versión (Power Query) y revierte todo.
> **La única forma que PERSISTE** es pegar el M en el **Editor Avanzado** de cada consulta, dentro de
> Power BI Desktop.

> **⭐ Conexión por ODBC (no el conector nativo).** El origen ya **no** es
> `PostgreSQL.Database("switchback.proxy.rlwy.net:37790","railway")` sino el **DSN ODBC `pocion_marts`**
> (driver *PostgreSQL Unicode(x64)*, `SSLmode=require`). Motivo: el Postgres de Railway presenta un cert
> con `CN=localhost` que **nunca** coincide con el hostname del proxy, así que el conector nativo (y el
> refresco en el Service) fallan con *"The remote certificate is invalid"*. `SSLmode=require` **cifra sin
> verificar hostname** y esquiva el problema. Ver `docs/bi_refresco_gateway.md` (creación del DSN local y
> en el VPS del gateway). **Verificado:** `require`/`prefer` conectan; `verify-full` falla.

## Cómo aplicar
1. **Crear el DSN `pocion_marts`** (una sola vez, ver `docs/bi_refresco_gateway.md` §ODBC). Debe existir
   con **ese nombre exacto** en la máquina que abre el `.pbix` (y en el VPS del gateway para el refresco).
2. Power BI Desktop → **Inicio → Transformar datos** (abre el Editor de Power Query).
3. Por cada consulta de abajo: selecciónala en el panel **Consultas** → **Inicio → Editor avanzado** →
   **borra todo** el contenido → pega el bloque correspondiente → **Listo**.
4. Cuando termines las 11, convierte también **el resto de consultas** (ver sección
   "Convertir el resto de consultas") y **elimina las huérfanas** (ver última sección).
5. **Cerrar y aplicar**. Si pide credenciales: tipo **Base de datos** → usuario/clave = `DB_USER`/
   `DB_PASSWORD` (del `.env`, no se pegan aquí) + **Nivel de privacidad = Organizational**.
6. **Guardar (Ctrl+S).**

> Notas:
> - Las tablas **`marts …`** (dim/fact) y el deck financiero también deben pasar a ODBC — patrón en la
>   sección "Convertir el resto de consultas". `respuestas_cartera` es Google Sheets (web), no se toca.
> - **Tablas → `Odbc.DataSource`** (navegación `railway → marts → tabla`). **Vistas (`v_*`) →
>   `Odbc.Query`**: este driver **no expone las vistas** en la navegación, pero un `SELECT` directo sí
>   funciona (por eso `Cartera`, que lee `v_cartera`, usa `Odbc.Query`).

---

## 1) LINEAS
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_lineas"]}[Data],
    Ren = Table.RenameColumns(bi, {{"producto","PRODUCTO"},{"linea","LINEA"},{"categoria","CATEGORIA"}}, MissingField.Ignore),
    Tipo = Table.TransformColumnTypes(Ren,{{"PRODUCTO", type text}, {"LINEA", type text}, {"CATEGORIA", type text}}),
    SinAudit = Table.RemoveColumns(Tipo, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 2) OFERTAS
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_ofertas"]}[Data],
    Ren = Table.RenameColumns(bi, {{"fecha","FECHA"},{"descuento","DESCUENTO"}}, MissingField.Ignore),
    Tipo = Table.TransformColumnTypes(Ren,{{"DESCUENTO", type number}}, "en-US"),
    FechaDT = Table.TransformColumnTypes(Tipo,{{"FECHA", type datetime}}),
    FechaDate = Table.TransformColumns(FechaDT, {{"FECHA", DateTime.Date, type date}}),
    SinAudit = Table.RemoveColumns(FechaDate, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 3) cliente_credito
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_cliente_credito"]}[Data],
    Ren = Table.RenameColumns(bi, {{"cliente","CLIENTE"},{"das_de_cxc","Días de CxC"},{"saldo_por_pagar","Saldo por pagar"},{"das_anticipo","Días anticipo"},{"saldo_anticipo","Saldo anticipo"}}, MissingField.Ignore),
    Tipo = Table.TransformColumnTypes(Ren,{{"Días de CxC", Int64.Type}, {"Saldo por pagar", type number}, {"Días anticipo", Int64.Type}, {"Saldo anticipo", type number}}, "en-US"),
    SinAudit = Table.RemoveColumns(Tipo, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 4) Clientes Impulso
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_clientes_impulso"]}[Data],
    Ren = Table.RenameColumns(bi, {{"nif","NIF"},{"odoo","ODOO"},{"cliente","CLIENTE"}}, MissingField.Ignore),
    Sel = Table.SelectColumns(Ren,{"NIF","ODOO","CLIENTE"}),
    Filtrar = Table.SelectRows(Sel, each not List.IsEmpty(List.RemoveMatchingItems(Record.FieldValues(_), {"", null}))),
    Tipo = Table.TransformColumnTypes(Filtrar,{{"NIF", Int64.Type}, {"ODOO", type text}, {"CLIENTE", type text}}, "en-US"),
    Dist = Table.Distinct(Tipo, {"ODOO"})
in
    Dist
```

## 5) TIENDAS_CCLAVE
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_tiendas_cclave"]}[Data],
    Ren = Table.RenameColumns(bi, {{"cliente","CLIENTE"},{"nombre_tienda","NOMBRE_TIENDA"},{"id_tienda","ID_TIENDA"}}, MissingField.Ignore),
    SinAudit = Table.RemoveColumns(Ren, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 6) CUENTAS_CLAVE ANEXO
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_cuentas_clave_ventas"]}[Data],
    Ren = Table.RenameColumns(bi, {{"ciudad","CIUDAD"},{"unidades","UNIDADES"},{"cliente","CLIENTE"},{"producto","PRODUCTO"},{"fecha","FECHA"},{"nombre_tienda","NOMBRE_TIENDA"},{"canal_venta","CANAL VENTA"},{"nombre_producto","NOMBRE_PRODUCTO"},{"tienda","TIENDA"},{"nombre","Nombre"},{"campaa","Campaña"},{"id_tienda","ID_TIENDA"},{"vendedor","VENDEDOR"},{"sucursal","Sucursal"},{"valores","VALORES"}}, MissingField.Ignore),
    Tipo = Table.TransformColumnTypes(Ren,{{"FECHA", type date},{"UNIDADES", Int64.Type},{"VALORES", Int64.Type},{"Campaña", Int64.Type}}, "en-US"),
    SinAudit = Table.RemoveColumns(Tipo, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 7) INV CCLAVE ANEXO
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_inventario_cclave"]}[Data],
    Ren = Table.RenameColumns(bi, {{"inventario","INVENTARIO"},{"cod_cliente","COD_CLIENTE"},{"cliente","CLIENTE"},{"producto","PRODUCTO"},{"maximo","Máximo"},{"nombre_tienda","NOMBRE_TIENDA"},{"id_tienda","ID_TIENDA"}}, MissingField.Ignore),
    Tipo = Table.TransformColumnTypes(Ren,{{"INVENTARIO", Int64.Type},{"Máximo", Int64.Type}}, "en-US"),
    SinAudit = Table.RemoveColumns(Tipo, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 8) BASE_CUENTAS_CLAVE
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_cuentas_clave"]}[Data],
    Ren = Table.RenameColumns(bi, {{"producto","PRODUCTO"},{"surti_aen","SURTI_AEN"},{"laskin_item","Laskin_item"},{"krika","Krika"}}, MissingField.Ignore),
    Tipo = Table.TransformColumnTypes(Ren,{{"PRODUCTO", type text}, {"cod_novaventa", type any}, {"plu_pasteur", Int64.Type}, {"locatel_cod_sap", type any}, {"vpn_farmatodo", type any}, {"item_prosalon", type any}}, "en-US"),
    Dup = Table.DuplicateColumn(Tipo, "PRODUCTO", "PRODUCTO - Copia"),
    Split = Table.SplitColumn(Dup, "PRODUCTO - Copia", Splitter.SplitTextByDelimiter(" ", QuoteStyle.Csv), {"PRODUCTO - Copia.1", "PRODUCTO - Copia.2", "PRODUCTO - Copia.3", "PRODUCTO - Copia.4", "PRODUCTO - Copia.5", "PRODUCTO - Copia.6"}),
    Rem = Table.RemoveColumns(Split,{"PRODUCTO - Copia.2", "PRODUCTO - Copia.3", "PRODUCTO - Copia.4", "PRODUCTO - Copia.5", "PRODUCTO - Copia.6"}, MissingField.Ignore),
    Trim = Table.TransformColumns(Rem,{{"PRODUCTO - Copia.1", Text.Trim, type text}}),
    Clean = Table.TransformColumns(Trim,{{"PRODUCTO - Copia.1", Text.Clean, type text}}),
    SinAudit = Table.RemoveColumns(Clean, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 9) PRESUPUESTO GENERAL
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    marts = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data],
    bi = marts{[Name="bi_presupuesto"]}[Data],
    cc0 = marts{[Name="bi_cliente_credito"]}[Data],
    cc = Table.RenameColumns(cc0, {{"cliente","CLIENTE"},{"das_de_cxc","Días de CxC"},{"saldo_por_pagar","Saldo por pagar"},{"das_anticipo","Días anticipo"},{"saldo_anticipo","Saldo anticipo"}}, MissingField.Ignore),
    Ren = Table.RenameColumns(bi, {{"fecha","FECHA"},{"canal","CANAL"},{"presupuesto","PRESUPUESTO"},{"ejecutiva","EJECUTIVA"},{"zona","ZONA"},{"cliente","CLIENTE"},{"presupuesto_con_iva","PRESUPUESTO CON IVA"},{"categoria_cliente","CATEGORIA CLIENTE"},{"unnamed_8","Column9"}}, MissingField.Ignore),
    Limpia = Table.RemoveColumns(Ren, {"_loaded_at","_source_file","id","unnamed_9"}, MissingField.Ignore),
    Num = Table.TransformColumns(Limpia, {
        {"PRESUPUESTO", each try Number.From(Text.From(_), "en-US") otherwise null, type number},
        {"PRESUPUESTO CON IVA", each try Number.From(Text.From(_), "en-US") otherwise null, type number},
        {"Column9", each try Number.From(Text.From(_), "en-US") otherwise null, type number}
    }, null, MissingField.Ignore),
    Fecha = Table.TransformColumns(Num, {{"FECHA", each try DateTime.Date(DateTime.From(Text.From(_), "en-US")) otherwise null, type date}}, null, MissingField.Ignore),
    Filt = Table.SelectRows(Fecha, each ([FECHA] <> null)),
    Join = Table.NestedJoin(Filt, {"CLIENTE"}, cc, {"CLIENTE"}, "cc", JoinKind.LeftOuter),
    Exp = Table.ExpandTableColumn(Join, "cc", {"Días de CxC", "Saldo por pagar", "Días anticipo", "Saldo anticipo"}, {"Días de CxC", "Saldo por pagar", "Días anticipo", "Saldo anticipo"}),
    RV = Table.ReplaceValue(Exp,null,0,Replacer.ReplaceValue,{"PRESUPUESTO"}),
    Ant = Table.AddColumn(RV, "anticipo", each (try Number.From([Saldo anticipo]) otherwise 0) * (try Number.From([PRESUPUESTO]) otherwise 0), type number),
    Con = Table.AddColumn(Ant, "concepto", each "ventas", type text),
    RV2 = Table.ReplaceValue(Con,null,0,Replacer.ReplaceValue,{"Días de CxC", "Días anticipo"}),
    Mes = Table.AddColumn(RV2, "meses_desplazamineto", each let d = (try Number.From([Días de CxC]) otherwise 0) in if d = 0 then 0 else if d <= 32 then 1 else if d <= 45 then 2 else if d >= 60 then 2 else if d <= 90 then 3 else if d <= 120 then 4 else 5, Int64.Type),
    RV3 = Table.ReplaceValue(Mes,null,1,Replacer.ReplaceValue,{"Saldo por pagar"})
in
    RV3
```

## 10) NIELSEIQ
```m
let
    Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
    bi = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="bi_nielsen"]}[Data],
    Ren = Table.RenameColumns(bi, {{"markets","Markets"},{"periods","Periods"},{"categoria","CATEGORIA"},{"fabricantes","FABRICANTES"},{"marcas","MARCAS"},{"item","ITEM"},{"presentacion_unif","PRESENTACION UNIF."},{"tipo_unif","TIPO UNIF."},{"promocionno_promocion_unif","PROMOCION/NO PROMOCION UNIF."},{"peso_vol_unitario_unif","PESO VOL. UNITARIO UNIF."},{"vtas_valor","Vtas Valor"},{"vtas_unds","Vtas Unds"},{"dist_num","Dist. Num."},{"upc","UPC"},{"marca_origen","MARCA_ORIGEN"}}, MissingField.Ignore),
    Tipos = Table.TransformColumnTypes(Ren, {{"Markets", type text},{"Periods", type text},{"CATEGORIA", type text},{"FABRICANTES", type text},{"MARCAS", type text},{"ITEM", type text},{"PRESENTACION UNIF.", type text},{"TIPO UNIF.", type text},{"PROMOCION/NO PROMOCION UNIF.", type text},{"PESO VOL. UNITARIO UNIF.", Int64.Type},{"Vtas Valor", Int64.Type},{"Vtas Unds", Int64.Type},{"Dist. Num.", type number}}, "en-US"),
    P1 = Table.AddColumn(Tipos, "Periods.1", each Text.Start([Periods], 8), type text),
    Fecha = Table.AddColumn(P1, "FECHA", each try Date.FromText(Text.Trim(Text.AfterDelimiter([Periods], "fin ")), [Format="dd/MM/yy", Culture="es-CO"]) otherwise null, type date),
    SinAudit = Table.RemoveColumns(Fecha, {"_loaded_at","_source_file","id"}, MissingField.Ignore)
in
    SinAudit
```

## 11) Cartera  (desde la vista `marts.v_cartera` — vista → `Odbc.Query`)
```m
let
    v = Odbc.Query("dsn=pocion_marts", "select * from marts.v_cartera"),
    Agr = Table.Group(v, {"numero"}, {
        {"CLIENTE", each List.First([tercero_nombre]), type text},
        {"TIPO CLIENTE", each List.First([tipo_cliente]), type text},
        {"fk", each List.Max([fecha_key])},
        {"fvk", each List.Max([fecha_vencimiento_key])},
        {"IMPORTE PENDIENTE", each List.Sum([saldo_pendiente]), type number}
    }),
    Ren = Table.RenameColumns(Agr, {{"numero","Número"}}, MissingField.Ignore),
    Imp = Table.TransformColumnTypes(Ren, {{"IMPORTE PENDIENTE", Int64.Type}}, "en-US"),
    FF = Table.AddColumn(Imp, "FECHA FACTURA", each let k = try Number.From([fk]) otherwise null in if k = null or k = 0 then null else #date(Number.IntegerDivide(k,10000), Number.Mod(Number.IntegerDivide(k,100),100), Number.Mod(k,100)), type date),
    FV = Table.AddColumn(FF, "FECHA VENCIMIENTO", each let k = try Number.From([fvk]) otherwise null in if k = null or k = 0 then null else #date(Number.IntegerDivide(k,10000), Number.Mod(Number.IntegerDivide(k,100),100), Number.Mod(k,100)), type date),
    DC = Table.AddColumn(FV, "DIAS CREDITO", each if [FECHA VENCIMIENTO] = null or [FECHA FACTURA] = null then null else Duration.Days([FECHA VENCIMIENTO] - [FECHA FACTURA]), Int64.Type),
    DA = Table.AddColumn(DC, "DIAS ATRASO", each if [FECHA VENCIMIENTO] = null then null else Duration.Days(Date.From(DateTime.LocalNow()) - [FECHA VENCIMIENTO]), Int64.Type),
    RM = Table.AddColumn(DA, "RANGO MORA", each let d = [DIAS ATRASO] in if d = null then "Sin clasificar" else if d < -7 then "Corriente" else if d <= 0 then "Proximo" else if d <= 10 then "Corriente" else if d <= 30 then "11_30" else if d <= 60 then "31_60" else if d <= 90 then "61_90" else "90+", type text),
    ORD = Table.AddColumn(RM, "ORDEN", each if [RANGO MORA] = "Corriente" then 1 else if [RANGO MORA] = "Proximo" then 2 else if [RANGO MORA] = "11_30" then 3 else if [RANGO MORA] = "31_60" then 4 else if [RANGO MORA] = "61_90" then 5 else if [RANGO MORA] = "90+" then 6 else 7, Int64.Type),
    RESP = Table.AddColumn(ORD, "RESPONSABLE", each null, type text),
    Final = Table.SelectColumns(RESP, {"Número","CLIENTE","FECHA FACTURA","FECHA VENCIMIENTO","IMPORTE PENDIENTE","RESPONSABLE","TIPO CLIENTE","DIAS CREDITO","DIAS ATRASO","RANGO MORA","ORDEN"}, MissingField.Ignore)
in
    Final
```

---

## Convertir el resto de consultas (tablas `marts …` dim/fact + deck financiero)
Además de las 11 de arriba, el modelo tiene otras consultas que leen de `marts` con el conector nativo
(las **dim/fact** `marts dim_*`/`fact_movimiento_contable`, el **deck financiero**, etc.). Hay que
pasarlas todas a ODBC. Es **mecánico**: cambia solo el `Origen` y su navegación; **el resto de pasos NO
se toca**.

**Regla según el tipo de objeto:**

- **TABLA** (dim/fact, `bi_*`): reemplaza
  ```m
  Origen = PostgreSQL.Database("switchback.proxy.rlwy.net:37790","railway"),
  X = Origen{[Schema="marts",Item="NOMBRE_TABLA"]}[Data],
  ```
  por
  ```m
  Origen = Odbc.DataSource("dsn=pocion_marts", [HierarchicalNavigation=true]),
  X = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data]{[Name="NOMBRE_TABLA"]}[Data],
  ```

- **VISTA** (`v_ventas_producto`, `v_ventas_explotada`, `v_cartera`, `v_exportaciones`…): este driver
  **no lista las vistas** en la navegación → usa `Odbc.Query` con SQL directo:
  ```m
  X = Odbc.Query("dsn=pocion_marts", "select * from marts.NOMBRE_VISTA"),
  ```

> Regla rápida: **empieza por `v_` → `Odbc.Query`**; cualquier otra (dim/fact/bi) → `Odbc.DataSource`.
> Si una tabla lee varias veces del mismo origen, extrae un paso `marts = Origen{[Name="railway"]}[Data]{[Name="marts"]}[Data],` y navega desde ahí (como en el bloque 9).

## Eliminar las consultas huérfanas (esto quita el error al Aplicar)
En el panel **Consultas** del editor, **eliminar** (clic derecho → Eliminar) todas estas — ya no se usan
y siguen apuntando a `G:\` (por eso falla el "Aplicar"). Tip: si están dentro de una **carpeta/grupo**
(p. ej. "Transformar archivo" o una carpeta por retailer), puedes eliminar la carpeta completa.

- **Retailers:** `Farmatodo, Surticosmeticos, Locatel, Novaventa, Pasteur, Prosalon, hist_surti, LASKIN, Krika, ecuador, dominicana, peru`
- **Inventarios:** `INV_PASTEUR, INV_LOCATEL, INV_LASKIN, INV PROSALON, INV_FARMATODO, INV_ECUADOR, INV_PERU, INV_RD`
- **Apoyo local:** `Tipo de cliente_ecuador, segmentacion_dominicana, segmentacion_peru, kits, Departamentos`
- **Grupos "Combinar archivos":** todos los `Parámetro N`, `Archivo de ejemplo (N)`, `Transformar archivo (N)`, `Transformar archivo de ejemplo (N)`.

**NO eliminar:** las 11 tablas de arriba, las `marts …`, `respuestas_cartera`, ni
`concepto_cont_odoo`, `concepto_cont_extra`, `map_filas_balance`.

## Al terminar
**Cerrar y aplicar** → ya no debe salir error → **Guardar (Ctrl+S)**. Como ahora los cambios viven en el
editor de Power Query, **sí persisten** al reabrir.

---

## Si el mensaje PERSISTE tras aplicar (el modelo ya está limpio)
Verificado por MCP: las 11 tablas cargan de `marts` (todas Ready), sin huérfanas y sin claves duplicadas.
Si aún ves un mensaje, es de Power BI Desktop (no de datos). Revisa en este orden:

1. **Formula.Firewall** (menciona "references other queries or steps"): ya se corrigió el bloque 9
   (PRESUPUESTO GENERAL ahora lee `bi_cliente_credito` **dentro** de la consulta; no referencia la consulta
   `cliente_credito`). Vuelve a pegar el bloque 9 actualizado.
   - Atajo: **Archivo → Opciones → Privacidad → "Omitir siempre los niveles de privacidad"** → Actualizar.
2. **Credenciales / origen de datos** ("Editar credenciales", "especifique cómo conectarse"):
   Archivo → **Configuración del origen de datos** → el origen **`dsn=pocion_marts`** (ODBC) →
   **Editar permisos** → credenciales tipo **Base de datos** (`DB_USER`/`DB_PASSWORD`) + **Nivel de
   privacidad = Organizational** → Actualizar. Si no aparece el DSN, es que no existe con ese nombre en
   esta máquina: créalo (ver `docs/bi_refresco_gateway.md` §ODBC).
3. **Error en un visual** (no banner): el visual usa una columna que cambió/desapareció
   (p. ej. INV `SKU/SKU_mod/BODEGA/ECOMMERCE`, que se eliminaron). Reemplaza el campo en el visual.
4. Si no es ninguno: copia el **texto exacto** del mensaje y dónde sale (banner / diálogo de actualizar /
   visual) para el diagnóstico final.
