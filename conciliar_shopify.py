"""
conciliar_shopify.py — Concilia el export de pedidos de SHOPIFY contra la venta que el DW tiene en
Odoo, descompone la diferencia concepto por concepto y **exporta a CSV la lista de facturas** de
cada situacion. SOLO LECTURA (no toca el ETL ni la BD).

La llave: `Name` del CSV de Shopify (`#157126`) <-> `account.move.ref` de Odoo, que en el DW vive
en `marts.fact_movimiento_contable.referencia`. Verificado: casan 5.385 de 5.424 pedidos de agosto.

⭐⭐ EL FLETE QUE PAGA EL CLIENTE **NO SE FACTURA** (medido 2026-09-07, agosto de 2026).
La prueba no es el importe de la venta, es la **CxC**: la linea de cuentas por cobrar de los 5.389
documentos del mes suma **975.354.259**, o sea **base + IVA y nada mas**, mientras el cliente le
pago a Shopify **1.017.897.929**. Los **34,2 M de diferencia son flete cobrado al cliente que no
esta en ninguna factura** — ni como venta ni en otra cuenta.

  ⚠ Esto NO es un fallo del DW: el DW refleja Odoo con exactitud. **Es una pregunta contable
  abierta** — si el cliente paga el flete, ¿debe ir en la factura? Este script la plantea con
  numeros; **no la responde**, y no debe cerrarse desde aqui.

⚠⚠ TRES CIFRAS DISTINTAS QUE TODO EL MUNDO LLAMA «LA VENTA DE SHOPIFY» (agosto de 2026):
    1.017.897.929  Shopify `Total` .......... lo que el cliente PAGO (lleva flete)
      975.421.259  facturas del mes ......... lo que se FACTURO ← la que cita el gerente
      953.211.070  `mv_ventas_mes` con IVA .. venta de PRODUCTO COMERCIAL ← la del tablero
  Las tres son correctas y **miden cosas distintas**. Comparar dos cualesquiera de frente produce
  un «hueco» que no existe. El bloque **LA ESCALERA COMPLETA** las une paso a paso y cierra AL PESO
  contra `mv_ventas_mes`.

⭐ EL REPARTO DE LOS 22,2 M ENTRE 975 Y 953, cada parte con su dueño:
    19.279.469  (87 %)  kits ARCHIVADOS sin codigo   -> TIENE RAZON EL GERENTE: es venta real
     4.136.920  (19 %)  notas credito por retracto   -> TIENE RAZON EL TABLERO: son devoluciones
      -798.200          facturas de pedidos de julio -> borde de mes
      -408.000          lineas anuladas (es_reverso) -> anulaciones
  Ninguno de los dos esta inflando: el gerente mira lo FACTURADO BRUTO y el tablero la VENTA NETA
  de producto comercial. Pero la parte grande SI es un problema real, y esta en Odoo.

⚠⚠⚠ LA CAUSA RAIZ, medida el 2026-09-08 y **NO es lo que parecia**: **LE QUITARON EL CODIGO AL
  PRODUCTO EN ODOO, Y EL PASADO SE REESCRIBIO.**
  Odoo escribe el concepto de la linea como `[default_code] nombre` **al crearla**, y ese texto es
  una foto que no se recalcula. Leidas las **2.764 lineas de 2026** de los 9 kits, el concepto trae
  el codigo en TODAS y **coincide EXACTO con el SKU de Shopify (7 de 7 comprobables)**:
    `[PCNKIT16]` 1007 lineas · `[PCNKIT17]` 576 · `[PCNKIT30]` 360 · `[PCNKIT39]` 333 ·
    `[PCNKIT3]` 232 · `[PCNKIT23]` 114 · `[PCNKIT22]` 92 · `[PCNKIT6]` 50
  ⇒ **Los productos SI tenian codigo el dia de la venta, y era el mismo de Shopify.** Alguien se lo
  QUITO el 18-19 de agosto (en 2 casos lo paso a una ficha nueva). Y como `v_ventas_producto`
  identificaba el producto comercial por el **PREFIJO DEL CODIGO**, esas **2.752 facturas de enero a
  agosto dejaron de contarse HACIA ATRAS** sin que nadie tocara una venta: **390.085.901 sin IVA /
  464.202.220 con IVA**, 2.786 unidades. Los «huecos» de la numeracion de Odoo (3, 6, 7, 17, 22, 23,
  30) **no son huecos: son esos codigos retirados.** Verlo: `--salida-kits-sku --odoo`.
  ⭐ **NO se facturo mal ni se despacho mal.** Trazado el pedido `#157143` de punta a punta:
  `sale.order S179675` con `delivery_status = full`, factura `FE49172` posted, albaran
  `MCALI/OUT/09457` en `done` el 1-ago 12:05, y de bodega salieron los 4 componentes del kit phantom
  (`[PCN25]`, `[PCN01]`, `[PCN04]`, `[PCN19]`). Todo correcto: lo que se rompio fue el REPORTE.
  ⛔ **LA REGLA QUE HAY QUE RESPETAR: no vaciar el `default_code` de un producto con historico de
  ventas.** Cambia informes ya publicados. Si hay que reemplazar la ficha, la nueva nace con su
  categoria y su PdV, y la vieja se archiva CONSERVANDO el codigo.
  ⭐ La leccion para el DW: una definicion de venta **no puede colgar de un campo mutable**. Es lo
  que resuelve definir el producto comercial por **categoria + `Disponible en PdV`**
  (ver `36_producto_comercial.sql`), que no mira el codigo.
  ⚠ Prueba de que es el mismo dinero: el `precio_shopify` coincide AL PESO con el `con_iva` de Odoo
  (177.600 = 177.600). Detalle factura a factura y cliente: `--salida-kits-detalle`.
  ⚠ El filtro de producto comercial excluye BIEN descuentos, asesorias, arriendos e intereses: lo
  unico mal excluido son estos kits.

⭐⭐ Y EL FLUJO YA ESTA BIEN: el problema es HISTORICO. Kits sin codigo por quincena: jul-1a 152
  facturas · jul-2a 113 · ago-1a 92 · **ago-2a 12 · septiembre NINGUNA**. Del 19 de agosto en
  adelante los kits de Shopify caen en fichas con codigo, en `PT/Kits` y con PdV (`PCNKIT12`,
  `PCNKIT13`, `PCNKIT37`, `TNGKIT`, `B8KIT`...), que el tablero SI cuenta.
  ⚠⚠ **Nada que reclamarle a Shopify: sus SKU eran correctos.**
  ⚠ Unica anomalia viva: `PCNKIT16` (ficha nueva, categoria `All` y sin PdV) seguia facturando el
  6-sep; si se aplica la definicion nueva sin completar su ficha, esa venta desaparece.

Dos trampas del CSV de Shopify, medidas:
  ⚠ La columna `Taxes` **NO es el IVA del 19 %** (7.168,91 en un pedido de 214.500). No sirve para
    el cruce. Aqui el IVA se lee del ASIENTO (base clase 4 + cuenta 2408), no de Shopify.
  ⚠ El CSV se filtra por `Created at`, asi que el borde de mes es ASIMETRICO: un pedido de julio
    facturado en agosto queda fuera del CSV, y uno de agosto facturado en septiembre aun no esta.

⚠ El factor de IVA **no se cablea**. En Shopify sale 1,19000 exacto (a consumidor final todo es
  gravado), pero se MIDE del asiento: en cuanto entre un producto excluido, un 1,19 fijo mentiria.

⛔ TODAS las conexiones fijan `statement_timeout` (--timeout, 120 s por defecto). No es decorativo:
  el 2026-09-07 una consulta de este mismo analisis se quedo **3 h 11 m** viva sobre
  `v_ventas_producto`, reteniendo un lock sobre `dim_cuenta`; detras se encolo el `ALTER TABLE` del
  cron y, tras el, 8 sesiones mas. **El cron del DW y la intranet estuvieron parados 2,5 horas.**
  Con timeout, una consulta que se pasa muere sola.

Uso:  python conciliar_shopify.py --csv D:\\Downloads\\orders_export_1.csv
      python conciliar_shopify.py --csv ... --salida detalle.csv --salida-kits kits.csv
      python conciliar_shopify.py --csv ... --salida-kits-detalle facturas.csv \\
                                            --salida-shopify-kits recorte.csv
      python conciliar_shopify.py --csv ... --salida-kits-sku kit_vs_sku.csv
      python conciliar_shopify.py --csv ... --mes 2026-09
"""
import os
import sys
import re
import logging
import difflib
import warnings
import argparse
import unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")

import pandas as pd
import psycopg2
from dotenv import load_dotenv

logging.getLogger().setLevel(logging.ERROR)

# Campos del CSV a nivel PEDIDO. Shopify exporta una fila por linea de pedido y solo pone estos
# en la PRIMERA fila del pedido, asi que se agregan con `first` (que salta los nulos).
NUM = ["Subtotal", "Taxes", "Total", "Discount Amount", "Shipping"]
OBLIGATORIAS = NUM + ["Name", "Financial Status", "Created at"]

COLS_CSV = ["situacion", "pedido", "factura", "fecha_factura", "estado_shopify", "creado_shopify",
            "total_shopify", "envio_shopify", "descuento_shopify", "base_odoo", "iva_odoo",
            "con_iva_odoo", "cxc_odoo", "diferencia", "nota"]

NOTAS = {
    "CUADRA": "Odoo factura exactamente lo que Shopify cobro",
    "FLETE_NO_FACTURADO": "la diferencia es el flete que el cliente pago y Odoo no facturo",
    "NO_PAGADO": "pedido expirado en Shopify: nunca se cobro, Odoo no lo factura",
    "FACTURADO_OTRO_MES": "pagado en este mes y facturado en otro (borde de mes)",
    "PAGADO_SIN_FACTURA": "PAGADO en Shopify y SIN factura en Odoo en ninguna fecha",
    "FACTURA_DE_OTRO_MES": "factura de este mes de un pedido creado en otro (borde de mes)",
    "NOTA_CREDITO": "devolucion o retracto; no lleva # de Shopify",
    "SIN_REFERENCIA": "documento de Shopify sin # en la referencia",
    "REVISAR": "la diferencia NO es 0 ni el flete: caso nuevo, hay que mirarlo",
}


def _fmt(v):
    return f"{v:>18,.0f}".replace(",", ".")


def conectar(timeout_s):
    """Conexion con statement_timeout. Ver el aviso ⛔ de la cabecera: sin esto, una consulta
    pesada puede quedarse horas reteniendo locks y parar el cron del DW."""
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), connect_timeout=15,
        options=f"-c statement_timeout={int(timeout_s * 1000)}")


def leer_shopify(ruta):
    sh = pd.read_csv(ruta, dtype=str, low_memory=False)
    faltan = [c for c in OBLIGATORIAS if c not in sh.columns]
    if faltan:
        # Abortar en vez de seguir con columnas ausentes: un cruce al que le falta el flete o el
        # estado da un numero creible y falso, que es peor que no dar ninguno.
        sys.exit(f"ERROR: al CSV le faltan columnas: {faltan}")
    for c in NUM:
        sh[c] = pd.to_numeric(sh[c], errors="coerce")
    ped = (sh.dropna(subset=["Total"]).groupby("Name", as_index=False)
             .agg(total=("Total", "first"), subtotal=("Subtotal", "first"),
                  envio=("Shipping", "first"), descuento=("Discount Amount", "first"),
                  estado=("Financial Status", "first"), creado=("Created at", "first")))
    ped = ped.rename(columns={"Name": "name"})
    ped["envio"] = ped["envio"].fillna(0)
    ped["descuento"] = ped["descuento"].fillna(0)
    ped["mes"] = ped["creado"].str.slice(0, 7)
    # Se devuelve tambien el CSV CRUDO: los CSV de detalle necesitan las lineas de pedido
    # (`Lineitem sku`/`name`/`quantity`/`price`), que la agregacion por pedido pierde.
    return ped, sh


def leer_dw(conn, mes):
    """Venta de Shopify del mes por DOCUMENTO, con el IVA y la CxC leidos del asiento.

    Se agrega primero por `factura_id` porque una factura son MUCHAS lineas del hecho y la
    `referencia` se repite en todas: sumar sin agrupar por documento multiplicaria el valor.

    ⭐ `cxc` es la clave del analisis del flete: es lo que Odoo le cobra al cliente. Que sea igual
    a base+IVA y menor que el `Total` de Shopify es la prueba de que el flete no se facturo.
    """
    df = pd.read_sql("""
      WITH doc AS (
        SELECT f.factura_id,
               max(f.referencia) FILTER (WHERE f.referencia LIKE '#%%')      AS name,
               max(f.numero)                                                 AS factura,
               max(f.tipo_movimiento)                                        AS tipo,
               max(f.fecha_factura)::text                                    AS fecha_factura,
               max(f.referencia)                                             AS referencia,
               sum(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)          AS base,
               sum(CASE WHEN c.codigo LIKE '2408%%' THEN f.credito - f.debito ELSE 0 END) AS iva,
               sum(f.debito - f.credito) FILTER (WHERE f.es_cxc)             AS cxc
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        WHERE f.categoria = 'SHOPIFY'
          AND f.es_venta
          AND f.fecha_factura >= DATE %(ini)s
          AND f.fecha_factura <  (DATE %(ini)s + INTERVAL '1 month')
        GROUP BY 1)
      SELECT *, base + iva AS con_iva FROM doc
    """, conn, params={"ini": f"{mes}-01"})
    for c in ("base", "iva", "cxc", "con_iva"):
        df[c] = pd.to_numeric(df[c]).fillna(0)
    return df


def buscar_facturas(conn, refs):
    """Busca esos pedidos en el hecho SIN filtro de fecha. Sirve para no llamar 'hueco' a lo que
    solo es borde de mes: en agosto de 2026 los 5 pedidos que parecian sin factura estaban
    facturados el 1-2 de septiembre."""
    if not refs:
        return pd.DataFrame(columns=["name", "factura", "fecha_factura", "base"])
    df = pd.read_sql("""
      SELECT f.referencia AS name, max(f.numero) AS factura,
             max(f.fecha_factura)::text AS fecha_factura, sum(f.venta_neta) AS base
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
      WHERE f.referencia = ANY(%(refs)s) AND f.es_venta AND c.clase_codigo = '4'
      GROUP BY 1
    """, conn, params={"refs": list(refs)})
    df["base"] = pd.to_numeric(df["base"]).fillna(0)
    return df


def leer_kits(conn, desde):
    """Los KITS que ningun tablero cuenta: `es_kit` pero sin `default_code` en Odoo, asi que
    `v_ventas_producto` los descarta por no cumplir el prefijo PCN/KD/TNG/B8.

    ⚠ El filtro es `es_kit AND codigo IS NULL`, NO 'todo lo que el prefijo excluye': ahi dentro
    tambien caen descuentos, asesorias, arriendos e intereses, que NO son venta de producto y
    estan bien excluidos.

    ⚠ Lleva los MISMOS filtros que `v_ventas_producto` —`clase_codigo = '4'` y **`es_reverso IS NOT
    TRUE`**— o las dos cifras del repo no coinciden. Sin el de reversos entraba una linea de kit
    ANULADA de −181.092 (la NC `RINV0670`) y este bloque decia 16.020.142 donde el puente de
    `diagnosticar_producto_comercial.py` dice **16.201.235**. La cifra correcta es la del puente: una
    linea anulada no la cuenta el tablero de ninguna manera, asi que no es parte de lo que se pierde
    por no tener codigo."""
    df = pd.read_sql("""
      SELECT date_trunc('month', f.fecha_factura)::date AS mes, f.categoria, p.nombre AS kit,
             count(*) AS lineas, sum(f.venta_neta) AS base_sin_iva
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta   c ON c.cuenta_id   = f.cuenta_id
      JOIN marts.dim_producto p ON p.producto_id = f.producto_id
      WHERE f.es_venta AND c.clase_codigo = '4' AND f.es_reverso IS NOT TRUE
        AND f.fecha_factura >= DATE %(desde)s
        AND p.es_kit AND p.codigo IS NULL
      GROUP BY 1, 2, 3 ORDER BY 1, 5 DESC
    """, conn, params={"desde": desde})
    df["base_sin_iva"] = pd.to_numeric(df["base_sin_iva"]).fillna(0)
    return df


def leer_kits_detalle(conn, mes):
    """FACTURA a FACTURA los kits sin codigo: numero de factura, cliente, unidades y valor.

    Es el soporte para sentarse con el gerente de Shopify y con contabilidad: no basta con el total,
    hay que poder senalar la factura y el cliente.

    ⚠ Mismos filtros que `v_ventas_producto` (clase 4 + es_venta + sin reversos). El IVA sale del
    factor del PROPIO documento, no de un 1,19 cableado.
    """
    return pd.read_sql("""
      WITH factor AS (
        SELECT f.factura_id,
               sum(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)          AS base,
               sum(CASE WHEN c.codigo LIKE '2408%%' THEN f.credito - f.debito ELSE 0 END) AS iva
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        WHERE f.categoria = 'SHOPIFY' AND f.es_venta
          AND f.fecha_factura >= DATE %(ini)s
          AND f.fecha_factura <  (DATE %(ini)s + INTERVAL '1 month')
        GROUP BY 1)
      SELECT f.referencia                              AS pedido,
             max(f.numero)                             AS factura,
             max(f.fecha_factura)::text                AS fecha_factura,
             p.nombre                                  AS kit_odoo,
             coalesce(p.codigo, '(SIN CODIGO)')        AS sku_odoo,
             max(t.nombre)                             AS cliente,
             sum(f.cantidad)                           AS unidades,
             sum(f.venta_neta)                         AS base,
             sum(f.venta_neta * CASE WHEN fa.base <> 0 THEN fa.iva / fa.base ELSE 0 END) AS iva
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta   c  ON c.cuenta_id   = f.cuenta_id
      JOIN marts.dim_producto p  ON p.producto_id = f.producto_id
      LEFT JOIN marts.dim_tercero t ON t.tercero_id = f.tercero_id
      LEFT JOIN factor fa ON fa.factura_id = f.factura_id
      WHERE f.categoria = 'SHOPIFY' AND f.es_venta AND c.clase_codigo = '4'
        AND f.es_reverso IS NOT TRUE
        AND p.es_kit AND p.codigo IS NULL
        AND f.fecha_factura >= DATE %(ini)s
        AND f.fecha_factura <  (DATE %(ini)s + INTERVAL '1 month')
      GROUP BY f.referencia, p.nombre, p.codigo
    """, conn, params={"ini": f"{mes}-01"})


def leer_anuladas(conn, mes):
    """Valor con IVA de las lineas ANULADAS (`es_reverso`) del mes.

    Estan DENTRO de lo facturado (son clase 4 y `es_venta`) pero FUERA del tablero, porque
    `v_ventas_producto` las excluye. Sin este escalon la escalera no cierra: en agosto de 2026 son
    -408.000 y dejaban un residuo de exactamente esa cifra.
    """
    df = pd.read_sql("""
      WITH factor AS (
        SELECT f.factura_id,
               sum(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)          AS base,
               sum(CASE WHEN c.codigo LIKE '2408%%' THEN f.credito - f.debito ELSE 0 END) AS iva
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        WHERE f.categoria = 'SHOPIFY' AND f.es_venta
          AND f.fecha_factura >= DATE %(ini)s
          AND f.fecha_factura <  (DATE %(ini)s + INTERVAL '1 month')
        GROUP BY 1)
      SELECT count(*) AS lineas,
             sum(f.venta_neta * (1 + CASE WHEN fa.base <> 0 THEN fa.iva / fa.base ELSE 0 END))
               AS con_iva
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
      LEFT JOIN factor fa ON fa.factura_id = f.factura_id
      WHERE f.categoria = 'SHOPIFY' AND f.es_venta AND c.clase_codigo = '4'
        AND f.es_reverso IS TRUE
        AND f.fecha_factura >= DATE %(ini)s
        AND f.fecha_factura <  (DATE %(ini)s + INTERVAL '1 month')
    """, conn, params={"ini": f"{mes}-01"})
    return int(df["lineas"].iloc[0] or 0), float(pd.to_numeric(df["con_iva"]).fillna(0).iloc[0])


def leer_kits_sku(conn, desde):
    """Una fila por KIT SIN CODIGO con su rastro completo: primera y ultima venta, facturas,
    unidades, valor y canales. Es el inventario del historico invisible.

    ⭐ `ultima_venta` es la columna que cierra el diagnostico: los 9 dejan de recibir facturas entre
    el 31-jul y el 18-ago de 2026 porque alguien limpio el catalogo de Odoo esos dias. Desde el 19
    de agosto los kits de Shopify caen en fichas CON codigo (`PCNKIT12`, `PCNKIT13`, `TNGKIT`...)
    que el tablero SI cuenta => el problema esta resuelto en el flujo y lo que queda es historico.
    """
    return pd.read_sql("""
      WITH factor AS (
        SELECT f.factura_id,
               sum(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)           AS base,
               sum(CASE WHEN c.codigo LIKE '2408%%' THEN f.credito - f.debito ELSE 0 END)  AS iva
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        WHERE f.es_venta AND f.fecha_factura >= DATE %(desde)s
        GROUP BY 1)
      SELECT p.nombre                                   AS kit_odoo,
             min(f.fecha_factura)::text                 AS primera_venta,
             max(f.fecha_factura)::text                 AS ultima_venta,
             count(DISTINCT f.factura_id)               AS facturas,
             sum(f.cantidad)                            AS unidades,
             sum(f.venta_neta)                          AS base,
             sum(f.venta_neta * CASE WHEN fa.base <> 0 THEN fa.iva / fa.base ELSE 0 END) AS iva,
             string_agg(DISTINCT f.categoria, ', ')     AS canales
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta   c  ON c.cuenta_id   = f.cuenta_id
      JOIN marts.dim_producto p  ON p.producto_id = f.producto_id
      LEFT JOIN factor fa ON fa.factura_id = f.factura_id
      WHERE f.es_venta AND c.clase_codigo = '4' AND f.es_reverso IS NOT TRUE
        AND p.es_kit AND p.codigo IS NULL
        AND f.fecha_factura >= DATE %(desde)s
      GROUP BY p.nombre ORDER BY 6 DESC
    """, conn, params={"desde": desde})


def catalogo_kits_con_codigo(conn):
    """Los kits que SI tienen `default_code` en el DW (incluye los archivados, pero como esos no
    tienen codigo, en practica son los ACTIVOS). Sirve para dos cosas:
      · resolver si un SKU de Shopify existe en nuestro catalogo,
      · y buscarle el nombre mas parecido, que es lo que demuestra que NO es un typo.
    ⚠ Se resuelve contra el DW, no contra Odoo: `dim_producto` trae el catalogo completo con
    `active_test: False`, asi que no hace falta abrir una conexion mas."""
    df = pd.read_sql("""
      SELECT codigo, nombre FROM marts.dim_producto
      WHERE codigo IS NOT NULL AND codigo ILIKE '%%KIT%%'
    """, conn)
    return dict(zip(df["nombre"], df["codigo"]))


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def codigo_historico_en_facturas(nombres_kit, desde):
    """Lee de ODOO el codigo que el producto tenia **AL FACTURAR**, no el que tiene hoy.

    ⭐⭐ ES LA PRUEBA DE LA CAUSA RAIZ. Odoo escribe el concepto de la linea como
    `[default_code] nombre` **en el momento de crearla**, y ese texto no se recalcula: es una foto.
    Medido el 2026-09-08 sobre las 2.764 lineas de 2026 de los 9 kits archivados, el concepto trae
    el codigo en TODAS (`[PCNKIT16]`, `[PCNKIT17]`, `[PCNKIT22]`…) y coincide EXACTO con el SKU que
    manda Shopify.

    ⇒ Los productos SI tenian codigo el dia de la venta. Alguien se lo QUITO el 18-19 de agosto, y
    como el tablero identificaba el producto comercial por el PREFIJO DEL CODIGO, 2.752 facturas de
    enero a agosto dejaron de contarse **hacia atras** sin que nadie tocara una venta. Los «huecos»
    de la numeracion de Odoo (3, 6, 7, 17, 22, 23, 30) son exactamente esos codigos retirados.

    ⚠ El concepto es TEXTO LIBRE: se hace voto mayoritario por producto y se devuelve tambien
    **cuantas lineas** respaldan cada codigo, para mostrar la evidencia en vez de presentarlo como
    un campo estructurado.
    ⚠ Necesita Odoo porque el DW no guarda el concepto de la linea. Va detras de `--odoo`.
    """
    from etl_dw_marts import conectar_odoo, Odoo, CTX_ALL  # import perezoso: solo con --odoo
    db, uid, pw, models = conectar_odoo()
    od = Odoo(db, uid, pw, models)
    prods = od.search_read("product.product",
                           [["name", "in", list(nombres_kit)], ["default_code", "=", False]],
                           ["id", "name"], limit=0, context=CTX_ALL)
    if not prods:
        return {}
    por_id = {p["id"]: p["name"] for p in prods}
    lineas = od.search_read("account.move.line",
                            [["product_id", "in", list(por_id)], ["date", ">=", desde]],
                            ["product_id", "name"], limit=0, context=CTX_ALL)
    votos = {}
    for ln in lineas:
        pid = ln["product_id"][0] if ln["product_id"] else None
        cod = re.match(r"^\[([^\]]+)\]", str(ln.get("name") or ""))
        if pid is None or not cod:
            continue
        votos.setdefault(por_id[pid], {}).setdefault(cod.group(1), 0)
        votos[por_id[pid]][cod.group(1)] += 1
    return {k: max(v.items(), key=lambda kv: kv[1]) for k, v in votos.items()}


def sku_shopify_de_los_kits(sh, det):
    """Mapa `kit_odoo` -> (SKU, nombre) de Shopify, por VOTO MAYORITARIO sobre los pedidos.

    ⭐ El hallazgo que esto documenta: **Shopify SÍ manda el SKU** de estos kits (0 vacíos en 3.019
    líneas de kit), y es un código que EXISTE en Odoo (`PCNKIT16`, `PCNKIT39`, `PCNKIT17`…). O sea
    que la factura está cayendo en un registro de producto ARCHIVADO y sin código en vez de en el
    bueno: el problema es el mapeo de producto Shopify↔Odoo.

    ⚠ NO se empareja por nombre normalizado: no basta. `KIT MASCARILL SOS + BOOSTER` (Odoo) contra
    `Kit Mascarilla S.O.S + Booster` (Shopify) no coinciden ni normalizando (MASCARILL/MASCARILLA).
    Se resuelve por PEDIDO: dentro del pedido se mira qué línea lleva un SKU de kit, y se vota.
    ⚠ Y se filtra a SKU de kit a propósito: un pedido con el kit trae también sus otras líneas
    (BOOSTER `PCN30`, Sport `PCN31`…), y atribuírselas al kit inventaría filas.
    """
    li = sh[sh["Lineitem sku"].notna() & sh["Lineitem name"].notna()]
    li_kit = li[li["Lineitem sku"].str.upper().str.contains("KIT", na=False)]
    j = det[["kit_odoo", "pedido"]].merge(
        li_kit[["Name", "Lineitem sku", "Lineitem name"]], left_on="pedido", right_on="Name")
    if j.empty:
        return {}
    votos = (j.groupby(["kit_odoo", "Lineitem sku", "Lineitem name"]).size()
              .reset_index(name="votos").sort_values("votos", ascending=False))
    return {k: (g.iloc[0]["Lineitem sku"], g.iloc[0]["Lineitem name"])
            for k, g in votos.groupby("kit_odoo")}


def clasificar(ped, dw, extra):
    """Devuelve una fila por pedido/documento con su `situacion`. Toda fila de las dos fuentes
    tiene que salir exactamente una vez: es lo que hace que el CSV cuadre con el puente."""
    por_name = dw.dropna(subset=["name"]).groupby("name", as_index=False).agg(
        factura=("factura", "min"), fecha_factura=("fecha_factura", "min"),
        base=("base", "sum"), iva=("iva", "sum"), con_iva=("con_iva", "sum"), cxc=("cxc", "sum"))
    filas = []

    com = ped.merge(por_name, on="name", how="inner")
    com["dif"] = com["total"] - com["con_iva"]
    for r in com.itertuples():
        if abs(r.dif) < 1:
            sit = "CUADRA"
        elif abs(r.dif - r.envio) < 1:
            sit = "FLETE_NO_FACTURADO"
        else:
            sit = "REVISAR"
        filas.append(dict(situacion=sit, pedido=r.name, factura=r.factura,
                          fecha_factura=r.fecha_factura, estado_shopify=r.estado,
                          creado_shopify=r.creado, total_shopify=r.total, envio_shopify=r.envio,
                          descuento_shopify=r.descuento, base_odoo=r.base, iva_odoo=r.iva,
                          con_iva_odoo=r.con_iva, cxc_odoo=r.cxc, diferencia=r.dif))

    solo_sh = ped[~ped["name"].isin(set(por_name["name"]))]
    otras = extra.set_index("name") if len(extra) else None
    for r in solo_sh.itertuples():
        if r.estado != "paid":
            sit, fac, ff = "NO_PAGADO", None, None
        elif otras is not None and r.name in otras.index:
            sit = "FACTURADO_OTRO_MES"
            fac, ff = otras.at[r.name, "factura"], otras.at[r.name, "fecha_factura"]
        else:
            sit, fac, ff = "PAGADO_SIN_FACTURA", None, None
        filas.append(dict(situacion=sit, pedido=r.name, factura=fac, fecha_factura=ff,
                          estado_shopify=r.estado, creado_shopify=r.creado, total_shopify=r.total,
                          envio_shopify=r.envio, descuento_shopify=r.descuento,
                          base_odoo=0, iva_odoo=0, con_iva_odoo=0, cxc_odoo=0,
                          diferencia=r.total))

    solo_od = dw[dw["name"].isna() | ~dw["name"].isin(set(ped["name"]))]
    for r in solo_od.itertuples():
        if r.tipo == "out_refund":
            sit = "NOTA_CREDITO"
        elif r.name is None:
            sit = "SIN_REFERENCIA"
        else:
            sit = "FACTURA_DE_OTRO_MES"
        filas.append(dict(situacion=sit, pedido=r.name or r.referencia, factura=r.factura,
                          fecha_factura=r.fecha_factura, estado_shopify=None, creado_shopify=None,
                          total_shopify=0, envio_shopify=0, descuento_shopify=0,
                          base_odoo=r.base, iva_odoo=r.iva, con_iva_odoo=r.con_iva, cxc_odoo=r.cxc,
                          diferencia=-r.con_iva))

    det = pd.DataFrame(filas)
    det["nota"] = det["situacion"].map(NOTAS)
    return det[COLS_CSV]


def leer_mv(conn, mes):
    """Lo que muestra la intranet, por `periodo_factura_aaaamm` para comparar por la MISMA fecha
    que el resto del informe (el puente va por `fecha_factura`)."""
    df = pd.read_sql("""
      SELECT sum(venta) AS venta, sum(venta_con_iva) AS con_iva
      FROM marts.mv_ventas_mes
      WHERE categoria = 'SHOPIFY' AND periodo_factura_aaaamm = %(p)s
    """, conn, params={"p": int(mes.replace("-", ""))})
    return float(pd.to_numeric(df["con_iva"]).fillna(0).iloc[0])


def main(csv, mes=None, salida=None, salida_kits=None, salida_kits_detalle=None,
         salida_shopify_kits=None, salida_kits_sku=None, desde=None, usar_odoo=False,
         timeout=120):
    ped, sh_raw = leer_shopify(csv)
    if mes is None:
        mes = ped["mes"].mode().iloc[0]
        fuera = int((ped["mes"] != mes).sum())
        if fuera:
            print(f"⚠ {fuera} pedidos del CSV son de otro mes distinto de {mes}; se conservan "
                  f"(el borde de mes es parte de lo que se mide).")
    print(f"\n{'=' * 94}")
    print(f"CONCILIACION SHOPIFY <-> ODOO/DW   ·   mes {mes}   ·   {csv}")
    print("=" * 94)

    conn = conectar(timeout)
    try:
        dw = leer_dw(conn, mes)
        por_name = set(dw["name"].dropna())
        pendientes = ped.loc[(~ped["name"].isin(por_name)) & (ped["estado"] == "paid"), "name"]
        extra = buscar_facturas(conn, list(pendientes))
        kits = leer_kits(conn, f"{mes[:4]}-01-01")
        kd = leer_kits_detalle(conn, mes)
        n_anul, anul = leer_anuladas(conn, mes)
        ks = leer_kits_sku(conn, desde or f"{mes[:4]}-01-01")
        cat_kits = catalogo_kits_con_codigo(conn)
        mv_con_iva = leer_mv(conn, mes)
    finally:
        conn.close()
    for col in ("base", "iva", "unidades"):
        if col in kd:
            kd[col] = pd.to_numeric(kd[col]).fillna(0)

    base_mes, iva_mes = dw["base"].sum(), dw["iva"].sum()
    con_iva_mes, cxc_mes = base_mes + iva_mes, dw["cxc"].sum()
    factor = con_iva_mes / base_mes if base_mes else float("nan")
    print(f"\nDW: {len(dw):,} documentos · base {_fmt(base_mes)} · IVA {_fmt(iva_mes)} "
          f"· con IVA {_fmt(con_iva_mes)}")
    aviso = ("   (todo gravado al 19 %)" if abs(factor - 1.19) < 1e-4
             else "   ⚠ no es 1,19: hay producto no gravado en la mezcla")
    print(f"    factor de IVA MEDIDO del asiento: {factor:.5f}{aviso}")

    det = clasificar(ped, dw, extra)
    res = (det.groupby("situacion")
              .agg(pedidos=("pedido", "size"), shopify=("total_shopify", "sum"),
                   odoo=("con_iva_odoo", "sum"), flete=("envio_shopify", "sum"))
              .reindex([s for s in NOTAS if s in set(det["situacion"])]))

    print(f"\n{'-' * 94}\nEL PUENTE  (una fila por situacion; el CSV trae el detalle)\n{'-' * 94}")
    print(f"  {'situacion':<24}{'pedidos':>9}{'Shopify pago':>19}{'Odoo facturo':>19}"
          f"{'flete':>15}")
    for s, r in res.iterrows():
        print(f"  {s:<24}{int(r.pedidos):>9,}{_fmt(r.shopify)[3:]:>19}{_fmt(r.odoo)[3:]:>19}"
              f"{_fmt(r.flete)[7:]:>15}")
    print(f"  {'':<24}{'-' * 62}")
    print(f"  {'TOTAL':<24}{len(det):>9,}{_fmt(det['total_shopify'].sum())[3:]:>19}"
          f"{_fmt(det['con_iva_odoo'].sum())[3:]:>19}{_fmt(det['envio_shopify'].sum())[7:]:>15}")

    ok_sh = abs(det["total_shopify"].sum() - ped["total"].sum()) < 1
    ok_od = abs(det["con_iva_odoo"].sum() - con_iva_mes) < 1
    print(f"\n  cuadre Shopify: {'✔' if ok_sh else '✘'}   cuadre Odoo: {'✔' if ok_od else '✘'}"
          f"   ·   sin clasificar: {int(det['situacion'].isna().sum())}")

    print(f"\n{'-' * 94}\n⭐ EL FLETE NO SE FACTURA — la prueba esta en la CxC\n{'-' * 94}")
    print(f"  Shopify cobro al cliente ............ {_fmt(ped['total'].sum())}")
    print(f"  Odoo le facturo (CxC del mes) ...... {_fmt(cxc_mes)}")
    print(f"  base + IVA del mes ................. {_fmt(con_iva_mes)}"
          f"   {'← la CxC es base+IVA y nada mas' if abs(cxc_mes - con_iva_mes) < 200000 else ''}")
    fl = det.loc[det["situacion"] == "FLETE_NO_FACTURADO", "envio_shopify"].sum()
    print(f"  flete cobrado y NO facturado ....... {_fmt(fl)}"
          f"   en {int((det['situacion'] == 'FLETE_NO_FACTURADO').sum()):,} pedidos")
    regal = det.loc[(det["situacion"] == "CUADRA") & (det["envio_shopify"] > 0), "envio_shopify"]
    print(f"  flete REGALADO (envio gratis) ...... {_fmt(regal.sum())}   en {len(regal):,} pedidos")
    print("\n  ⚠ El DW refleja Odoo con exactitud. Que el flete deba o no ir en la factura es una")
    print("    PREGUNTA CONTABLE, y este informe la plantea con numeros: no la responde.")

    rev = det[det["situacion"] == "REVISAR"]
    if len(rev):
        print(f"\n  ⛔ {len(rev)} pedidos cuya diferencia NO es 0 ni el flete — caso nuevo:")
        print(rev[["pedido", "factura", "total_shopify", "envio_shopify", "con_iva_odoo",
                   "diferencia"]].head(15).to_string(index=False))
    else:
        print("\n  ✓ RESIDUO 0: ningun pedido comun se sale de 'cuadra' o 'flete'.")

    if len(kits):
        k = kits[kits["mes"].astype(str).str.startswith(mes)]
        print(f"\n{'-' * 94}\n⚠ KITS SIN CODIGO — venta real que NINGUN tablero cuenta\n{'-' * 94}")
        print(f"  en {mes}, todos los canales ...... {_fmt(k['base_sin_iva'].sum())} sin IVA "
              f"({len(k):,} filas mes×canal×kit)")
        print(f"  desde {mes[:4]}-01 ................... {_fmt(kits['base_sin_iva'].sum())} sin IVA")
        print(f"  kits afectados: {kits['kit'].nunique()}  ->  "
              f"{', '.join(sorted(kits['kit'].unique())[:4])}...")
        print("  Causa: no tienen `default_code` en Odoo y `v_ventas_producto` exige prefijo")
        print("  PCN/KD/TNG/B8. Por eso `mv_ventas_mes` (la intranet) va por debajo de lo facturado.")

    # ── LA ESCALERA: de lo que dice el panel de Shopify a lo que dice el tablero ─────────────
    print(f"\n{'=' * 94}\nLA ESCALERA COMPLETA — las tres cifras que se citan y por qué difieren"
          f"\n{'=' * 94}")
    def _sit(s, col="con_iva_odoo"):
        f = det["situacion"] == s
        return int(f.sum()), det.loc[f, col].sum()

    n_nopag, v_nopag = _sit("NO_PAGADO", "total_shopify")
    n_otro, v_otro = _sit("FACTURADO_OTRO_MES", "total_shopify")
    n_jul, otro_mes = _sit("FACTURA_DE_OTRO_MES")
    n_nc, nc = _sit("NOTA_CREDITO")
    fact_ago = det.loc[det["situacion"].isin(["CUADRA", "FLETE_NO_FACTURADO"]), "con_iva_odoo"].sum()
    kits_iva = (kd["base"].sum() + kd["iva"].sum()) if len(kd) else 0.0
    for et, v, nota in [
        ("Shopify `Total` — lo que PAGÓ el cliente", ped["total"].sum(), "el panel de Shopify"),
        ("(−) flete cobrado y NO facturado", -fl, ""),
        (f"(−) {n_nopag} pedidos sin pagar (expirados)", -v_nopag, ""),
        (f"(−) {n_otro} pedidos facturados en otro mes", -v_otro, ""),
        ("= FACTURAS de los pedidos del mes", fact_ago, "⭐ la cifra del gerente"),
        (f"(+) {n_jul} facturas de pedidos de otro mes", otro_mes, ""),
        (f"(−) {n_nc} notas crédito por retracto", nc, ""),
        ("= FACTURADO NETO del mes", fact_ago + otro_mes + nc, ""),
        ("(−) kits ARCHIVADOS sin código en Odoo", -kits_iva, "⚠ venta real que no se ve"),
        (f"(−) {n_anul} líneas anuladas (es_reverso)", -anul, "no cuentan en ninguno de los dos"),
    ]:
        print(f"  {et:<48}{_fmt(v)}   {nota}")
    tablero = fact_ago + otro_mes + nc - kits_iva - anul
    print(f"  {'':<48}{'-' * 18}")
    print(f"  {'= lo que muestra la INTRANET':<48}{_fmt(tablero)}   ⭐ la cifra del tablero")
    if mv_con_iva:
        d = tablero - mv_con_iva
        print(f"  {'  mv_ventas_mes dice':<48}{_fmt(mv_con_iva)}   "
              f"{'✔ AL PESO' if abs(d) < 1000 else f'⚠ residuo {d:,.0f}'}")

    print(f"\n  ── EL REPARTO DE LA DIFERENCIA, CON SU DUEÑO ──")
    dif = fact_ago - tablero
    for et, v, quien in [
        ("kits archivados sin código en Odoo", kits_iva, "⭐ TIENE RAZÓN EL GERENTE: es venta real"),
        ("notas crédito por retracto", -nc, "⭐ TIENE RAZÓN EL TABLERO: son devoluciones"),
        ("facturas de pedidos de otro mes", -otro_mes, "borde de mes, de ninguno de los dos"),
        ("líneas anuladas (es_reverso)", anul, "anulaciones, de ninguno de los dos"),
    ]:
        pct = f"{100 * v / dif:.0f} %" if dif else "n/d"
        print(f"    {et:<38}{_fmt(v)}  {pct:>6}   {quien}")
    print(f"    {'':<38}{'-' * 18}")
    print(f"    {'diferencia gerente vs tablero':<38}{_fmt(dif)}")

    if salida:
        det.sort_values(["situacion", "pedido"]).to_csv(
            salida, index=False, sep=";", encoding="utf-8-sig", decimal=",", float_format="%.2f")
        print(f"\n✔ CSV escrito: {salida}   ({len(det):,} filas, separador ';', UTF-8 con BOM)")
    if salida_kits:
        kits.to_csv(salida_kits, index=False, sep=";", encoding="utf-8-sig", decimal=",",
                    float_format="%.2f")
        print(f"✔ CSV de kits escrito: {salida_kits}   ({len(kits):,} filas)")

    if (salida_kits_detalle or salida_shopify_kits) and len(kd):
        mapa = sku_shopify_de_los_kits(sh_raw, kd)
        kd["sku_shopify"] = kd["kit_odoo"].map(lambda k: mapa.get(k, (None, None))[0])
        kd["nombre_shopify"] = kd["kit_odoo"].map(lambda k: mapa.get(k, (None, None))[1])
        kd["con_iva"] = kd["base"] + kd["iva"]
        # La linea EXACTA de Shopify: por pedido + SKU (no por pedido a secas, ver el aviso de
        # sku_shopify_de_los_kits).
        li = sh_raw[sh_raw["Lineitem sku"].notna()][
            ["Name", "Lineitem sku", "Lineitem quantity", "Lineitem price"]].copy()
        li = li.rename(columns={"Lineitem quantity": "cantidad_shopify",
                                "Lineitem price": "precio_shopify"})
        kd = kd.merge(li, how="left",
                      left_on=["pedido", "sku_shopify"], right_on=["Name", "Lineitem sku"])
        cols = ["kit_odoo", "sku_odoo", "sku_shopify", "nombre_shopify", "factura",
                "fecha_factura", "pedido", "cliente", "unidades", "base", "iva", "con_iva",
                "cantidad_shopify", "precio_shopify"]

        print(f"\n{'-' * 94}\nEL SKU QUE FALTA EN ODOO **SÍ LO MANDA SHOPIFY**\n{'-' * 94}")
        res = (kd.groupby(["kit_odoo", "sku_odoo", "sku_shopify", "nombre_shopify"], dropna=False)
                 .agg(facturas=("factura", "nunique"), unidades=("unidades", "sum"),
                      con_iva=("con_iva", "sum")).reset_index()
                 .sort_values("con_iva", ascending=False))
        print(res.to_string(index=False))
        print("\n  ⇒ La factura cae en un producto ARCHIVADO y SIN NINGUN identificador en Odoo")
        print("    (`default_code` y `barcode` los dos en NULL, verificado ficha por ficha).")
        print("  ⚠ Y de esos SKU, SOLO ALGUNOS existen en Odoo: medido el 2026-09-08, `PCNKIT16` y")
        print("    `PCNKIT39` si (en categoria `All`, fuera de la lista de Kits) y `PCNKIT17`,")
        print("    `PCNKIT23`, `PCNKIT30`, `PCNKIT6`, `PCNKIT3` NO EXISTEN en Odoo: viven solo en")
        print("    Shopify. Verificar antes de afirmar que 'el SKU ya existe en Odoo'.")
        print("  ⇒ El `default_code` NO lo escribe la integracion de Shopify, asi que el reporte")
        print("    NO puede depender de el. Es lo que resuelve la definicion por categoria + PdV.")

        if salida_kits_detalle:
            kd[cols].sort_values(["kit_odoo", "fecha_factura", "factura"]).to_csv(
                salida_kits_detalle, index=False, sep=";", encoding="utf-8-sig", decimal=",",
                float_format="%.2f")
            print(f"\n✔ CSV factura a factura: {salida_kits_detalle}   ({len(kd):,} filas)")
        if salida_shopify_kits:
            skus = set(kd["sku_shopify"].dropna())
            reco = sh_raw[sh_raw["Lineitem sku"].isin(skus)]
            reco.to_csv(salida_shopify_kits, index=False, sep=";", encoding="utf-8-sig")
            print(f"✔ CSV recorte de Shopify: {salida_shopify_kits}   ({len(reco):,} lineas de "
                  f"pedido, SKU {', '.join(sorted(skus))})")

    # ── EL LISTADO KIT DE ODOO <-> SKU DE SHOPIFY ────────────────────────────────────────────
    if salida_kits_sku and len(ks):
        mapa = sku_shopify_de_los_kits(sh_raw, kd) if len(kd) else {}
        for c in ("base", "iva", "unidades"):
            ks[c] = pd.to_numeric(ks[c]).fillna(0)
        ks["con_iva"] = ks["base"] + ks["iva"]
        ks["sku_odoo"] = "(SIN CODIGO)"
        ks["sku_shopify"] = ks["kit_odoo"].map(lambda k: mapa.get(k, (None, None))[0])
        ks["nombre_shopify"] = ks["kit_odoo"].map(lambda k: mapa.get(k, (None, None))[1])
        codigos = set(cat_kits.values())
        # ⚠ Un kit sin SKU aqui NO es un dato que falte: es que no tuvo pedidos en el mes que trae
        # el CSV de Shopify (el export es de UN mes y el listado cubre el ano). Se etiqueta para que
        # nadie lo lea como un hueco.
        sin_csv = f"(sin pedidos en el CSV de {mes})"
        ks["existe_en_odoo"] = ks["sku_shopify"].map(
            lambda s: sin_csv if not s else ("SI" if s in codigos else "NO"))
        ks["sku_shopify"] = ks["sku_shopify"].fillna(sin_csv)
        ks["nombre_shopify"] = ks["nombre_shopify"].fillna(sin_csv)
        # El kit CON codigo de nombre mas parecido: es lo que demuestra que NO es un typo, porque
        # el parecido resulta ser OTRO producto (PCNKIT17 'Rizos largos y abundantes' contra
        # PCNKIT14 'Rizos largos e HIDRATADOS').
        norm = {_norm(n): (n, c) for n, c in cat_kits.items()}

        def _parecido(fila):
            if not fila["nombre_shopify"] or fila["nombre_shopify"] == sin_csv:
                return sin_csv
            cerca = difflib.get_close_matches(_norm(fila["nombre_shopify"]), list(norm),
                                              n=1, cutoff=0.5)
            if not cerca:
                return "NINGUNO parecido"
            nom, cod = norm[cerca[0]]
            return f"{cod} {nom}"

        ks["kit_con_codigo_mas_parecido"] = ks.apply(_parecido, axis=1)
        cols = ["kit_odoo", "sku_odoo", "sku_shopify", "nombre_shopify", "existe_en_odoo",
                "kit_con_codigo_mas_parecido", "primera_venta", "ultima_venta", "facturas",
                "unidades", "base", "iva", "con_iva", "canales"]

        if usar_odoo:
            hist = codigo_historico_en_facturas(ks["kit_odoo"], desde or f"{mes[:4]}-01-01")
            ks["codigo_historico_en_factura"] = ks["kit_odoo"].map(
                lambda k: hist.get(k, (None, 0))[0])
            ks["lineas_que_lo_respaldan"] = ks["kit_odoo"].map(lambda k: hist.get(k, (None, 0))[1])
            ks["coincide_con_shopify"] = ks.apply(
                lambda r: "—" if not r["codigo_historico_en_factura"]
                or r["existe_en_odoo"] == sin_csv
                else ("SI" if r["codigo_historico_en_factura"] == r["sku_shopify"] else "NO"),
                axis=1)
            cols = cols[:6] + ["codigo_historico_en_factura", "lineas_que_lo_respaldan",
                               "coincide_con_shopify"] + cols[6:]
            print(f"\n{'-' * 94}")
            print("⭐ EL CODIGO QUE EL PRODUCTO TENIA **AL FACTURAR** (del concepto de la linea)")
            print("-" * 94)
            print(ks[["kit_odoo", "codigo_historico_en_factura", "lineas_que_lo_respaldan",
                      "sku_shopify", "coincide_con_shopify"]].to_string(index=False))
            n_si = int((ks["coincide_con_shopify"] == "SI").sum())
            n_ev = int((ks["coincide_con_shopify"] != "—").sum())
            print(f"\n  coinciden Odoo(al facturar) y Shopify: {n_si} de {n_ev}")
            print("  ⇒ Los productos SI tenian codigo el dia de la venta, y era el MISMO que manda")
            print("    Shopify. Se lo quitaron despues (18-19 de agosto), y por eso la venta se")
            print("    volvio invisible HACIA ATRAS: el tablero identificaba el producto comercial")
            print("    por el PREFIJO DEL CODIGO. No se facturo mal ni se despacho mal.")

        print(f"\n{'-' * 94}\nKIT DE ODOO  <->  SKU DE SHOPIFY   (el listado)\n{'-' * 94}")
        print(ks[["kit_odoo", "sku_shopify", "existe_en_odoo", "ultima_venta", "facturas",
                  "unidades", "con_iva"]].to_string(index=False))
        print(f"\n  TOTAL: {_fmt(ks['con_iva'].sum())} con IVA  ·  "
              f"{int(ks['unidades'].sum()):,} unidades  ·  {int(ks['facturas'].sum()):,} facturas")
        n_no = int((ks["existe_en_odoo"] == "NO").sum())
        n_con = int((ks["existe_en_odoo"] != sin_csv).sum())
        print(f"  SKU de Shopify que NO existen en nuestro catalogo: {n_no} de {n_con}")
        ult = ks["ultima_venta"].max()
        print(f"\n  ⭐ ULTIMA venta de un kit sin codigo: {ult}. Si es de hace semanas, el catalogo")
        print("     de Odoo YA se limpio y lo que queda es HISTORICO, no un problema vivo.")
        print("  ⚠ El SKU sale del CSV de Shopify (`Lineitem sku`) cruzado por PEDIDO, no de la")
        print("    factura: la factura de Odoo no trae codigo. Y el 'mas parecido' es OTRO producto")
        print("    en todos los casos => no es un error de digitacion, es un kit que faltaba.")
        ks[cols].to_csv(salida_kits_sku, index=False, sep=";", encoding="utf-8-sig",
                        decimal=",", float_format="%.2f")
        print(f"\n✔ CSV kit<->SKU escrito: {salida_kits_sku}   ({len(ks):,} kits)")

    print(f"\n{'-' * 94}\nCOMO LEER ESTO\n{'-' * 94}")
    print("· TRES cifras distintas se llaman 'la venta de Shopify' y las tres son correctas:")
    print(f"    {ped['total'].sum():>15,.0f}  lo que el cliente PAGO (con flete)")
    print(f"    {cxc_mes:>15,.0f}  lo que Odoo le FACTURO (CxC con IVA)")
    print("    (mv_ventas_mes)  venta de PRODUCTO COMERCIAL, lo que ve la intranet")
    print("  Comparar dos cualesquiera de frente produce un hueco que no existe.")
    print("· `Taxes` del CSV NO es el IVA del 19 %. El IVA de este informe sale del ASIENTO.")
    print("· El borde de mes es asimetrico: el CSV filtra por `Created at` y el DW por")
    print("  `fecha_factura`. Por eso hay facturas de pedidos de otro mes en las dos direcciones.")
    print("· Las notas credito no traen `#`: su `ref` es 'Reversion de: FExxxxx, motivo'.")
    print("· Las cifras de un mes en curso CAMBIAN: el ETL del DW corre cada 15 min.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Concilia el export de Shopify contra el DW.")
    ap.add_argument("--csv", required=True, help="ruta del orders_export de Shopify")
    ap.add_argument("--mes", default=None,
                    help="mes a conciliar YYYY-MM (por defecto, el que domina el CSV)")
    ap.add_argument("--salida", default=None,
                    help="CSV de salida con una fila por pedido/documento y su situacion")
    ap.add_argument("--salida-kits", dest="salida_kits", default=None,
                    help="CSV con los kits sin codigo que ningun tablero cuenta (resumen mes x canal)")
    ap.add_argument("--salida-kits-detalle", dest="salida_kits_detalle", default=None,
                    help="CSV FACTURA A FACTURA de los kits sin codigo: numero de factura, cliente, "
                         "el SKU que manda Shopify y el que falta en Odoo, unidades y valor")
    ap.add_argument("--salida-kits-sku", dest="salida_kits_sku", default=None,
                    help="CSV con el listado KIT DE ODOO <-> SKU DE SHOPIFY: si ese SKU existe en "
                         "nuestro catalogo, el kit con codigo mas parecido, primera y ULTIMA venta, "
                         "facturas, unidades y valor. Es el inventario del historico invisible")
    ap.add_argument("--odoo", action="store_true",
                    help="con --salida-kits-sku: lee de ODOO el codigo que el producto tenia AL "
                         "FACTURAR (del concepto de la linea). Es la prueba de que Odoo y Shopify "
                         "estaban alineados y de que el codigo se retiro despues")
    ap.add_argument("--desde", default=None,
                    help="fecha de inicio del listado de kits (por defecto, 1-ene del ano del mes)")
    ap.add_argument("--salida-shopify-kits", dest="salida_shopify_kits", default=None,
                    help="CSV con el recorte del export de Shopify de esos mismos kits, tal cual "
                         "lo entrega Shopify (para poner los dos lados en la mesa)")
    ap.add_argument("--timeout", type=int, default=120,
                    help="statement_timeout en segundos (por defecto 120). Ver el aviso de la "
                         "cabecera: sin el, una consulta pesada puede parar el cron")
    a = ap.parse_args()
    main(a.csv, a.mes, a.salida, a.salida_kits, a.salida_kits_detalle,
         a.salida_shopify_kits, a.salida_kits_sku, a.desde, a.odoo, a.timeout)
