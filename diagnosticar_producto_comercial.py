"""
diagnosticar_producto_comercial.py — Responde «que define un PRODUCTO COMERCIAL, y cuanta venta
real se queda fuera de los tableros por esa definicion». SOLO LECTURA (no toca vistas, MV ni ETL).

QUE DEFINE HOY UN PRODUCTO COMERCIAL — una sola condicion, en `sql/marts/14_ventas.sql:213`
(repetida en `:259` para `v_nc_sin_asignar`):

    AND p.codigo IS NOT NULL
    AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%' OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%')

O sea el **prefijo del `default_code` de Odoo**: una CONVENCION DE NOMBRES, no un campo del negocio
ni una marca que alguien mantenga. De ahi sus tres modos de fallo (medidos en 2026):

  1. Producto **sin `default_code`** -> invisible.        9 kits ~ 390 M
  2. Codigo con **otro prefijo** -> invisible.            ADD14 38,7 M · sachets SCHT 13,4 M · merch
  3. Prefijo correcto pero **no es producto terminado**    PCNKIT16 7,7 M · PCNKIT39 0,9 M
     -> entra sin deber.

⭐ Un PCN nuevo ENTRA SOLO: se comprobo con PCN34/PCN35 (lanzados el 2026-06-03), que aparecen en
`mv_ventas_mes` de agosto en 9 canales cada uno (~472 M y ~420 M). La duda «el tablero no cuenta los
productos nuevos» es INFUNDADA; lo que se pierde es lo que no tiene codigo o no lleva prefijo
conocido.

LA ALTERNATIVA QUE ODOO YA MANTIENE: el arbol de categorias (`dim_producto.categoria`), que para un
producto real vale `Inventario/Producto Terminado/<Linea>`. Captura **445 M mas en 2026**, cubre los
kits sin codigo y **excluye sola** los descuentos y servicios.
  ⚠ NO es un cambio gratis y este script NO lo aplica: metería merchandising (TOTE BAG, VASO KIDS,
  RIÑONERA, NECESER, COSMETIQUERA) y sachets de muestra `(OBS)`, y si eso cuenta como «venta de
  producto» es DECISION DE NEGOCIO. Precedente del repo: la *linea* de producto ya se migro de
  `bi_lineas` al arbol de Odoo el 2026-07-30 por este mismo argumento.

⛔ Fija `statement_timeout` en la conexion. El 2026-09-07 una consulta de analisis se quedo 3 h 11 m
  viva sobre `v_ventas_producto` reteniendo un lock sobre `dim_cuenta`; detras se encolo el
  `ALTER TABLE` del cron y tras el 8 sesiones mas: **el DW estuvo parado 2,5 horas**. Ver los gotchas
  de `CLAUDE.md`.
  ⭐ Por eso este script replica los filtros de `v_ventas_producto` SOBRE EL HECHO y no consulta la
  vista. Medido: el informe entero pasa con `--timeout 1`, o sea que cada consulta tarda **menos de
  un segundo**, contra las horas de la vista. Los 7 joins de la vista eran el problema, no el hecho.

Uso:  python diagnosticar_producto_comercial.py
      python diagnosticar_producto_comercial.py --mes 2026-08 --categoria SHOPIFY
      python diagnosticar_producto_comercial.py --salida productos.csv
"""
import os
import sys
import logging
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

# La definicion vigente, en un solo sitio para que el informe no pueda desviarse de 14_ventas.sql.
PREFIJOS = ("PCN%", "KD%", "TNG%", "B8%")
RAIZ_TERMINADO = "Inventario/Producto Terminado%"

def _expr_prefijo(pct="%%"):
    """La condicion de producto comercial, tal cual esta en 14_ventas.sql.

    ⚠ `pct='%%'` para EMBEBER en una consulta con parametros: psycopg2 interpola con paramstyle
    pyformat, asi que un `%` literal dentro del SQL hay que duplicarlo o falla con un
    `dict is not a sequence` que no dice nada. Con `pct='%'` sale la version legible para imprimir.
    """
    return ("(p.codigo IS NOT NULL AND ("
            + " OR ".join(f"p.codigo LIKE '{x[:-1]}{pct}'" for x in PREFIJOS) + "))")


SQL_PREFIJO = _expr_prefijo("%%")     # para las consultas
TXT_PREFIJO = _expr_prefijo("%")      # para el informe


def _fmt(v):
    return f"{v:>17,.0f}".replace(",", ".")


def conectar(timeout_s):
    """⛔ statement_timeout obligatorio: ver el aviso de la cabecera."""
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), connect_timeout=15,
        options=f"-c statement_timeout={int(timeout_s * 1000)}")


def _norm(s):
    """Nombre normalizado para detectar duplicados: sin tildes, sin puntuacion, minusculas."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


def leer_productos(conn, desde, categoria=None):
    """Una fila por producto con venta, y las dos definiciones evaluadas.

    ⚠ Replica el filtro de `v_ventas_producto` (clase 4 + es_venta + sin reversos) SOBRE EL HECHO,
    no sobre la vista: la vista reconstruye 7 joins y es justo la que provoco el incidente de locks.
    """
    cat = "AND f.categoria = %(cat)s" if categoria else ""
    return pd.read_sql(f"""
      SELECT p.producto_id,
             p.codigo,
             p.nombre,
             p.es_kit,
             p.categoria,
             {SQL_PREFIJO}                        AS pasa_prefijo,
             (p.categoria LIKE %(raiz)s)          AS es_producto_terminado,
             count(*)                             AS lineas,
             sum(f.venta_neta)                    AS base
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta   cu ON cu.cuenta_id  = f.cuenta_id
      JOIN marts.dim_producto p  ON p.producto_id = f.producto_id
      WHERE f.es_venta
        AND cu.clase_codigo = '4'
        AND f.es_reverso IS NOT TRUE
        AND f.fecha_factura >= DATE %(desde)s
        {cat}
      GROUP BY 1, 2, 3, 4, 5, 6, 7
    """, conn, params={"desde": desde, "raiz": RAIZ_TERMINADO, "cat": categoria})


def leer_puente(conn, mes, categoria):
    """De lo FACTURADO (clase 4) a lo que muestra el tablero, bucket por bucket.

    Reproduce las exclusiones de `v_ventas_producto` en el mismo orden que el `WHERE` de la vista,
    para que cada peso caiga en exactamente un bucket y la suma cuadre con el total del periodo.
    """
    cat = "AND f.categoria = %(cat)s" if categoria else ""
    return pd.read_sql(f"""
      SELECT CASE
          WHEN f.es_reverso IS TRUE                       THEN '6_es_reverso (anulada)'
          WHEN p.producto_id IS NULL                      THEN '3_linea sin producto'
          WHEN p.es_kit AND p.codigo IS NULL              THEN '2_KIT sin default_code'
          WHEN p.codigo IS NULL                           THEN '4_producto sin codigo (no kit)'
          WHEN NOT {SQL_PREFIJO}                          THEN '5_codigo NO comercial'
          WHEN f.tipo_movimiento = 'out_refund' AND m.nc_factura_id IS NULL
                                                          THEN '7_NC sin factura enlazada'
          ELSE '1_COMERCIAL (entra al tablero)' END       AS bucket,
        count(*) AS lineas, sum(f.venta_neta) AS base
      FROM marts.fact_movimiento_contable f
      JOIN marts.dim_cuenta cu ON cu.cuenta_id = f.cuenta_id
      LEFT JOIN marts.dim_producto p ON p.producto_id = f.producto_id
      LEFT JOIN marts.map_nc_factura m ON m.nc_factura_id = f.factura_id
      WHERE f.es_venta
        AND cu.clase_codigo = '4'
        AND f.fecha_factura >= DATE %(ini)s
        AND f.fecha_factura <  (DATE %(ini)s + INTERVAL '1 month')
        {cat}
      GROUP BY 1 ORDER BY 1
    """, conn, params={"ini": f"{mes}-01", "cat": categoria})


def leer_mv(conn, mes, categoria):
    """Lo que ve la intranet, por `periodo_factura_aaaamm` para comparar manzana con manzana
    (el puente va por `fecha_factura`; `mv_ventas_mes` tambien expone esa fecha)."""
    cat = "AND categoria = %(cat)s" if categoria else ""
    df = pd.read_sql(f"""
      SELECT sum(venta) AS venta, sum(venta_con_iva) AS con_iva
      FROM marts.mv_ventas_mes
      WHERE periodo_factura_aaaamm = %(p)s {cat}
    """, conn, params={"p": int(mes.replace("-", "")), "cat": categoria})
    return (pd.to_numeric(df["venta"]).fillna(0).iloc[0],
            pd.to_numeric(df["con_iva"]).fillna(0).iloc[0])


def main(desde, mes, categoria, salida, timeout):
    conn = conectar(timeout)
    try:
        prod = leer_productos(conn, desde, categoria)
        puente = leer_puente(conn, mes, categoria)
        mv_venta, mv_con_iva = leer_mv(conn, mes, categoria)
    finally:
        conn.close()

    prod["base"] = pd.to_numeric(prod["base"]).fillna(0)
    puente["base"] = pd.to_numeric(puente["base"]).fillna(0)
    ambito = f"canal {categoria}" if categoria else "TODOS los canales"

    print(f"\n{'=' * 96}")
    print(f"QUE DEFINE UN PRODUCTO COMERCIAL   ·   desde {desde}   ·   {ambito}")
    print("=" * 96)
    print("\n1. LA DEFINICION VIGENTE  (sql/marts/14_ventas.sql:213)")
    print("   " + TXT_PREFIJO.replace(" OR ", "\n        OR "))
    print("\n   ⚠ Es el PREFIJO del `default_code` de Odoo: una convencion de nombres, no un campo")
    print("     del negocio. Un PCN nuevo entra solo; lo que se pierde es lo que no tiene codigo")
    print("     o no lleva un prefijo conocido.")

    print(f"\n{'-' * 96}\n2. LOS CUATRO CUADRANTES: prefijo  x  categoria de Odoo\n{'-' * 96}")
    cua = (prod.groupby(["pasa_prefijo", "es_producto_terminado"])
               .agg(productos=("producto_id", "nunique"), lineas=("lineas", "sum"),
                    base=("base", "sum")).reset_index())
    print(f"  {'prefijo':<9}{'Prod.Terminado':<16}{'productos':>10}{'lineas':>10}{'base':>19}"
          f"   lectura")
    lectura = {(True, True): "el nucleo: las dos definiciones coinciden",
               (False, True): "⚠ LO QUE EL TABLERO PIERDE",
               (True, False): "se perderia si se cambiara a categoria",
               (False, False): "descuentos y servicios: bien fuera de las dos"}
    for r in cua.itertuples():
        print(f"  {'✔' if r.pasa_prefijo else '✘':<9}{'✔' if r.es_producto_terminado else '✘':<16}"
              f"{int(r.productos):>10,}{int(r.lineas):>10,}{_fmt(r.base):>19}"
              f"   {lectura[(r.pasa_prefijo, r.es_producto_terminado)]}")
    tot_l, tot_b = int(prod["lineas"].sum()), prod["base"].sum()
    print(f"  {'':<25}{'':>10}{'-' * 29}")
    print(f"  {'TOTAL':<25}{prod['producto_id'].nunique():>10,}{tot_l:>10,}{_fmt(tot_b):>19}")
    ok = int(cua["lineas"].sum()) == tot_l
    print(f"\n  los cuadrantes suman el total: {'✔' if ok else '✘'}")

    print(f"\n{'-' * 96}\n3a. LO QUE LA CATEGORIA CAPTURARIA Y EL PREFIJO NO  (el hueco)\n{'-' * 96}")
    a = prod[(~prod["pasa_prefijo"]) & prod["es_producto_terminado"]].copy()
    a["codigo"] = a["codigo"].fillna("(SIN CODIGO)")
    print(a.sort_values("base", ascending=False)[
        ["codigo", "nombre", "es_kit", "lineas", "base"]].to_string(index=False))
    kit = a[a["es_kit"]]["base"].sum()
    print(f"\n  total: {_fmt(a['base'].sum())}   de lo cual KITS: {_fmt(kit)} "
          f"({len(a[a['es_kit']])} kits)  ·  el resto es merchandising y sachets de muestra")

    print(f"\n{'-' * 96}\n3b. LO QUE EL PREFIJO ACEPTA Y LA CATEGORIA NO\n{'-' * 96}")
    b = prod[prod["pasa_prefijo"] & (~prod["es_producto_terminado"])]
    print(b.sort_values("base", ascending=False)[
        ["codigo", "nombre", "categoria", "lineas", "base"]].to_string(index=False))
    print(f"\n  total: {_fmt(b['base'].sum())}   (`categoria = 'All'` = sin categoria asignada en Odoo)")

    print(f"\n{'-' * 96}\n4. EL PUENTE: de lo FACTURADO a lo que muestra el TABLERO  ({mes}, {ambito})"
          f"\n{'-' * 96}")
    for r in puente.itertuples():
        print(f"  {r.bucket[2:]:<34}{int(r.lineas):>9,} lineas {_fmt(r.base)}")
    com = puente.loc[puente["bucket"].str.startswith("1"), "base"].sum()
    print(f"  {'':<34}{'':>9}        {'-' * 17}")
    print(f"  {'TOTAL facturado (clase 4)':<34}{int(puente['lineas'].sum()):>9,} lineas "
          f"{_fmt(puente['base'].sum())}")
    print(f"\n  COMERCIAL (deberia ser el tablero) . {_fmt(com)}")
    print(f"  mv_ventas_mes dice ................. {_fmt(mv_venta)}")
    res = com - mv_venta
    pct = 100 * res / mv_venta if mv_venta else 0
    print(f"  residuo ............................ {_fmt(res)}   ({pct:+.3f} %)")
    print("  ⚠ El residuo conocido es el desplazamiento de `fecha_venta` de las notas credito")
    print("    (la NC resta en el mes de SU factura). Se reporta, no se esconde.")

    print(f"\n{'-' * 96}\n5. SOSPECHA DE DUPLICADOS EN ODOO  (mismo nombre, uno con codigo y otro sin)"
          f"\n{'-' * 96}")
    prod["_n"] = prod["nombre"].map(_norm)
    dup = []
    for n, g in prod.groupby("_n"):
        if len(g) > 1 and g["codigo"].isna().any() and g["codigo"].notna().any():
            dup.append(g)
    if dup:
        d = pd.concat(dup)
        d["codigo"] = d["codigo"].fillna("(SIN CODIGO)")
        print(d.sort_values(["_n", "base"], ascending=[True, False])[
            ["nombre", "codigo", "categoria", "es_kit", "lineas", "base"]].to_string(index=False))
        print("\n  ⚠ Es una SOSPECHA, no un hecho: confirmarla es mirar las fichas en Odoo. Si son")
        print("    duplicados, la raiz esta en Odoo y no en el filtro del DW.")
    else:
        print("  ninguna.")

    if salida:
        out = prod.drop(columns=["_n"]).copy()
        out["codigo"] = out["codigo"].fillna("(SIN CODIGO)")
        out["clasificacion"] = out.apply(
            lambda r: "AMBAS" if r.pasa_prefijo and r.es_producto_terminado
            else ("SOLO_CATEGORIA (el hueco)" if r.es_producto_terminado
                  else ("SOLO_PREFIJO" if r.pasa_prefijo else "NINGUNA (no es venta de producto)")),
            axis=1)
        out.sort_values(["clasificacion", "base"], ascending=[True, False]).to_csv(
            salida, index=False, sep=";", encoding="utf-8-sig", decimal=",", float_format="%.2f")
        print(f"\n✔ CSV escrito: {salida}   ({len(out):,} productos, separador ';', UTF-8 con BOM)")

    print(f"\n{'-' * 96}\nCOMO LEER ESTO\n{'-' * 96}")
    print("· El tablero NO pierde productos nuevos: un PCN nuevo entra solo (probado con")
    print("  PCN34/PCN35, lanzados el 2026-06-03 y presentes en mv_ventas_mes de agosto).")
    print("· Lo que pierde es lo que NO TIENE CODIGO (los kits) o lleva OTRO PREFIJO (merch,")
    print("  sachets). El bloque 3a es la lista exacta y su valor.")
    print("· El bloque 3b es el precio de cambiar a la categoria de Odoo: son productos con")
    print("  `categoria = 'All'`, o sea sin categoria asignada.")
    print("· ⚠ Cambiar la definicion es DECISION DE NEGOCIO: sube las cifras ya publicadas y")
    print("  arrastra merchandising y muestras. Este informe mide; no cambia nada.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Que define un producto comercial y cuanta venta queda fuera de los tableros.")
    ap.add_argument("--desde", default="2026-01-01",
                    help="fecha de inicio del analisis de productos (por defecto 2026-01-01)")
    ap.add_argument("--mes", default="2026-08", help="mes del puente facturado->tablero (YYYY-MM)")
    ap.add_argument("--categoria", default=None,
                    help="limitar a un canal (p.ej. SHOPIFY); por defecto, todos")
    ap.add_argument("--salida", default=None, help="CSV con el detalle por producto")
    ap.add_argument("--timeout", type=int, default=180,
                    help="statement_timeout en segundos (por defecto 180). Ver el aviso ⛔ de la "
                         "cabecera: sin el, una consulta pesada puede parar el cron")
    a = ap.parse_args()
    main(a.desde, a.mes, a.categoria, a.salida, a.timeout)
