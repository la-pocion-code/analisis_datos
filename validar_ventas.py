"""
validar_ventas.py — Concilia marts.v_ventas_producto (DW) contra el base_ventas del pipeline de Excel
(CSV en CLEAN DATA). Solo lectura. Reusable cada mes.

Alineación necesaria para que cuadre (ver docs/GUIA_OPERACION.md §7):
  1. TODAS las empresas (el Excel no distingue; ene-2026 estaba en empresa 1, luego en la 8).
  2. Por FECHA_VENTA (no la contable ni la propia de la NC): para una nota crédito es la fecha de la
     FACTURA que corrige, así la NC resta en el mes de la venta original — igual que hace el Excel al
     casar la NC con su factura. Ej.: NCR1858 (mar-2026) corrige FEVY80693 (nov-2025) → resta en nov.
  3. Producto comercial [PCN/[KD/[TNG/[B8 (ya filtrado en ambos lados).

Nota: `es_reverso` = ANULACIÓN real (factura + NC de reversión ≥99%), NO `payment_state='reversed'`
(que en este Odoo lo pone el FACTORING y las NC PARCIALES → ventas reales que SÍ cuentan). Cuando Odoo
deja `reversed_entry_id` NULL, la anulación se detecta por el puente NC (`marcar_reversos_puente`).

⚠⚠ **CORREGIDO 2026-07-31 — EL SIGNO ESTABA AL REVÉS, Y ERA CULPA DE ESTE SCRIPT.**
Hasta hoy `FILES_2026` solo traía los 3 ficheros de VENTAS y **no el de notas crédito**, que en 2026
va aparte (`FILE_NC_2026`). Medido: esos 3 tienen **0 filas negativas y 0 documentos
RINV/RFEX/RPOS**, o sea que son BRUTO puro. Así que la conciliación comparaba el NETO del DW contra
el BRUTO del Excel, y de ahí salía el «DW por debajo» que se documentó durante dos semanas.

Con el fichero de NC incluido (medido 2026-07-31, ene-jul):
  - Excel bruto        55.165.069.210
  - NC del Excel       −2.852.867.976
  - **Excel NETO       52.312.201.234**  ← y coincide AL PESO con `exploded_data`, que se construye
    por separado: es la prueba de que ese fichero es complemento y no solapamiento.
  - **DW               54.376.881.626  ⇒ el DW está +3,9% POR ENCIMA, no por debajo.**

Por qué el DW resta tan poca NC (−273,9M contra los −2.852,9M del Excel) sin que sea un error: ante
una anulación total el Excel **netea** (deja la factura y le resta la NC) y el DW **la saca entera**
(`es_reverso` marca las dos), así que esa NC no aparece como resta en el DW. Comparar «cuánta NC
resta cada uno» no es manzana con manzana; lo comparable es el NETO.
⚠ Buena parte del +3,9% es **timing**: los CSV son fotos (Enero-Mayo es del 18-jun y Junio del
6-jul) y el DW sigue cargando cada 15 min. Antes de leerlo como una diferencia de reglas, mirar la
fecha de modificación de los CSV.

Cifras históricas del cuadre contra el BRUTO, que es lo que este script medía antes: −5,1% (con el
bug de `es_reverso`) → −0,0% (tras el fix) → −1,1% (al pasar a `fecha_venta`) → −1,3% (2026-07-28).
Los documentos que explicaban ese −1,3% siguen siendo reales y valen para el bruto:
  - **mar**: facturas ANULADAS que el Excel cuenta por su valor completo (`FE7301` 662,2M +
    `FE9576`/`FE9570` 278M ≈ 941M), compensadas por `NDY1` (+612,9M), que anula la NC que anuló
    `FE7281` y por tanto REVIVE esa venta de marzo.
  - **abr**: ~200M de NC de exportación que el DW sí resta (`RFEX2`, enlazada por
    `reversed_entry_id`).
  - **ene**: `NDY14` (113M) anula una NC de una factura de dic-2025 → sale de 2026.
En años CERRADOS el DW sí queda por debajo del manual, y poco: **2024 −0,68%** (18.622,2M vs
18.750,2M) y **2025 −0,59%** (82.417,4M vs 82.907,8M), medido contra `exploded_data`.
Las NOTAS DÉBITO ya no cuentan como venta, salvo las que anulan una nota crédito (y esas van al mes de
la factura que reviven): ver `marts.map_nd_factura` y `marts.v_notas_debito_excluidas`.
Otras diferencias residuales esperadas:
  - Timing: un CSV viejo vs el DW recién cargado (más facturas) → el DW puede quedar más alto.
  - NC que el Excel descarta porque su `ref` no casa con una factura-producto.
⭐ Para auditar la columna `fecha_venta` y las NC: `python diagnosticar_fecha_venta.py`.

Uso:  python validar_ventas.py
"""
import os
import re
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, ".")
from classes.db_loader import DBLoader

CLEAN_DATA = r"G:\Otros ordenadores\Mi portátil\VENTA MENSUAL\CLEAN DATA"
FILES_2026 = ["Ventas_Enero_2026_Mayo_2026.csv", "Ventas_Junio_2026.csv", "Ventas_Julio_2026.csv"]
# ⚠ En 2026 las NOTAS CRÉDITO van en un fichero APARTE y estos tres NO las traen: medido
# 2026-07-31, los tres juntos tienen **0 filas negativas y 0 documentos RINV/RFEX/RPOS**. O sea
# que `FILES_2026` es el BRUTO, no el neto (al revés que 2024-25, donde el pipeline las netificaba
# dentro de la fila de la factura). Sin este fichero el Excel sale 2.852,9M por encima de lo que
# el propio proceso manual considera su cifra final, y la conciliación cambia de SIGNO.
# Prueba de que es complemento y no solapamiento: 55.165.069.210 − 2.852.867.976 = 52.312.201.234,
# **exactamente** el total de `exploded_data`, que se construye por separado.
FILE_NC_2026 = "Ventas_Febrero_2026_Julio_2026.csv"
# Índices de columna (la cabecera trae acentos con encoding inconsistente → se leen por posición):
#   0 NUMERO_FACTURA · 3 MES · 4 AÑO · 7 CATEGORÍA · 8 PRODUCTO · 9 CANTIDAD · 12 TOTAL($) neto
COLS = [0, 3, 4, 7, 8, 9, 12]
NOMBRES = ["numero", "mes", "anio", "categoria", "producto", "cantidad", "total_cop"]
COD_RE = re.compile(r"^\[([A-Za-z0-9]+)\]")
MESES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
         7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}


def _leer(ficheros):
    dfs = []
    for f in ficheros:
        ruta = os.path.join(CLEAN_DATA, f)
        d = pd.read_csv(ruta, sep=";", decimal=",", encoding="utf-8",
                        usecols=COLS, names=NOMBRES, header=0)
        dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce")
    df["total_cop"] = pd.to_numeric(df["total_cop"], errors="coerce")
    df["codigo"] = df["producto"].str.extract(COD_RE)[0]
    return df[df["anio"] == 2026].copy()


def cargar_excel():
    """El BRUTO del Excel: solo facturas (los 3 ficheros de ventas, sin notas crédito)."""
    return _leer(FILES_2026)


def cargar_excel_nc():
    """Las NOTAS CRÉDITO del Excel, que en 2026 van en un fichero aparte (valores negativos).

    Se lee por separado a propósito y NO se concatena con las facturas: así se conserva el
    diagnóstico `excel_vs_bruto` (que compara el bruto del Excel contra el bruto del DW) y a la
    vez se puede publicar el NETO, que es la cifra que el proceso manual considera final.
    """
    return _leer([FILE_NC_2026])


def _fmt(df, val="venta"):
    df = df.copy()
    for c in df.columns:
        if df[c].dtype.kind in "fi" and c != "mes":
            df[c] = df[c].round(0)
    return df.to_string(index=False)


def main():
    lo = DBLoader()
    xl = cargar_excel()
    xl_nc = cargar_excel_nc()

    # ── 1) Conciliación mensual: Excel vs DW (todas las empresas, por fecha de factura) ──
    # ⚠ `excel_bruto` = solo facturas; `excel_nc` = el fichero de notas crédito;
    # `excel` = el NETO, que es la cifra final del proceso manual y la que hay que comparar.
    xl_mes = xl.groupby("mes").agg(excel_bruto=("total_cop", "sum"),
                                   lineas_xl=("total_cop", "size")).reset_index()
    nc_mes = xl_nc.groupby("mes").agg(excel_nc=("total_cop", "sum")).reset_index()
    xl_mes = xl_mes.merge(nc_mes, on="mes", how="outer").fillna({"excel_nc": 0})
    xl_mes["excel"] = xl_mes["excel_bruto"] + xl_mes["excel_nc"]
    dw_mes = lo.consultar("""
        SELECT mes_venta AS mes,
               SUM(venta_subtotal) dw, COUNT(*) lineas_dw
        FROM marts.v_ventas_producto
        WHERE anio_venta = 2026
        GROUP BY 1 ORDER BY 1""")
    m = xl_mes.merge(dw_mes, on="mes", how="outer").sort_values("mes")
    m["dif"] = m["dw"] - m["excel"]
    m["dif_%"] = (m["dif"] / m["excel"] * 100).round(1)
    m["mes"] = m["mes"].map(MESES)
    print("=" * 78)
    print("CONCILIACIÓN MENSUAL 2026 — v_ventas_producto (DW) vs base_ventas (Excel)")
    print("Todas las empresas · por FECHA_VENTA (la NC resta en el mes de su factura) · comercial")
    print("excel = excel_bruto + excel_nc (el fichero de NC va aparte y SÍ cuenta)")
    print("=" * 78)
    print(_fmt(m[["mes", "excel_bruto", "excel_nc", "excel", "dw", "dif", "dif_%",
                  "lineas_xl", "lineas_dw"]]))
    tot_br, tot_nc = m["excel_bruto"].sum(), m["excel_nc"].sum()
    tot_xl, tot_dw = m["excel"].sum(), m["dw"].sum()
    print(f"\nExcel bruto={tot_br:,.0f}  NC={tot_nc:,.0f}  ->  Excel NETO={tot_xl:,.0f}")
    print(f"TOTAL 2026  Excel={tot_xl:,.0f}  DW={tot_dw:,.0f}  "
          f"dif={tot_dw - tot_xl:,.0f} ({(tot_dw - tot_xl) / tot_xl * 100:+.1f}%)")
    print(f"   (contra el bruto, que es lo que este script comparaba antes: "
          f"{(tot_dw - tot_br) / tot_br * 100:+.1f}%)")

    # ── 2) Causa a nivel categoría para los meses fuera de ±2% ──
    fuera = m[m["dif_%"].abs() > 2]["mes"].tolist()
    inv = {v: k for k, v in MESES.items()}
    for mes_nom in fuera:
        mesn = inv[mes_nom]
        # ⚠ Las NC entran también aquí: si no, el desglose por categoría compara el BRUTO del
        # Excel contra el NETO del DW y la categoría que recibió la NC parece descuadrada.
        xl_todo = pd.concat([xl, xl_nc], ignore_index=True)
        xlc = (xl_todo[xl_todo["mes"] == mesn].groupby("categoria")["total_cop"].sum()
               .rename("excel").reset_index())
        dwc = lo.consultar(f"""
            SELECT COALESCE(categoria,'(nulo)') categoria, SUM(venta_subtotal) dw
            FROM marts.v_ventas_producto
            WHERE anio_venta=2026 AND mes_venta={mesn}
            GROUP BY 1""")
        c = xlc.merge(dwc, on="categoria", how="outer").fillna(0)
        c["dif"] = c["dw"] - c["excel"]
        c = c.sort_values("dif").reset_index(drop=True)
        print("\n" + "-" * 78)
        print(f"CAUSA {mes_nom} 2026 — Excel vs DW por CATEGORÍA (ordenado por diferencia)")
        print("-" * 78)
        print(_fmt(c))

    # ── 3) NOTAS CRÉDITO: cuánto resta cada lado ──
    # ⚠ CORREGIDO 2026-07-31. Aquí decía que «el Excel es el resultado YA NETO del pipeline… no
    # tiene filas negativas ni documentos de NC». Eso vale para 2024-25, pero en 2026 las NC van
    # en un fichero APARTE (`FILE_NC_2026`) y los 3 de ventas son BRUTO puro: medido, 0 filas
    # negativas y 0 documentos RINV/RFEX/RPOS. El script no lo leía, así que comparaba el DW
    # contra el bruto del Excel y por eso el DW «salía por debajo».
    # `excel_vs_bruto` compara BRUTO contra BRUTO (por eso usa `excel_bruto`, no `excel`) y sigue
    # sirviendo para lo de siempre: ≈0 ⇒ los dos lados parten del mismo bruto.
    # Ej.: FE9565/FE9570/FE9576 (mar-2026) salen en el Excel por su valor COMPLETO aunque estén 100%
    # anuladas por RINV/2026/0098/0100/0101; en el DW factura + NC netean 0.
    dwb = lo.consultar("""
        SELECT mes_venta AS mes,
               SUM(venta_subtotal) FILTER (WHERE tipo_movimiento='out_invoice') dw_bruto,
               SUM(venta_subtotal) dw_neto
        FROM marts.v_ventas_producto
        WHERE anio_venta=2026
        GROUP BY 1 ORDER BY 1""")
    nc = m[["mes", "dif"]].copy()
    nc["mesn"] = nc["mes"].map(inv)
    nc = nc.merge(dwb, left_on="mesn", right_on="mes", how="left", suffixes=("", "_d"))
    nc = nc.merge(xl_mes[["mes", "excel_bruto", "excel_nc"]], left_on="mesn", right_on="mes",
                  how="left", suffixes=("", "_x"))
    nc["nc_dw"] = nc["dw_neto"] - nc["dw_bruto"]              # lo que el DW resta (negativo)
    nc["excel_vs_bruto"] = nc["excel_bruto"] - nc["dw_bruto"]  # ≈0 ⇒ mismo bruto en los dos lados
    nc["nc_gap"] = nc["excel_nc"] - nc["nc_dw"]               # cuánto resta el Excel de MÁS que el DW
    nc["residual"] = nc["dif"] - nc["nc_gap"]                 # lo no explicado por las NC (timing)
    print("\n" + "=" * 78)
    print("NOTAS CRÉDITO — cuánto resta cada lado. excel_vs_bruto compara BRUTO contra BRUTO")
    print("(≈0 ⇒ los dos parten de lo mismo). nc_gap = excel_nc - nc_dw: el Excel NETEA la")
    print("anulación mientras el DW la SACA entera (es_reverso), así que el DW resta mucho menos")
    print("en NC sin que eso sea un error. residual = lo no explicado por NC (timing).")
    print("=" * 78)
    print(_fmt(nc[["mes", "dif", "dw_bruto", "dw_neto", "nc_dw", "excel_bruto", "excel_nc",
                   "excel_vs_bruto", "nc_gap", "residual"]]))

    # Documentos de NC que el DW resta (lo accionable: son los que el Excel no alcanzó a netear)
    nc_docs = lo.consultar("""
        SELECT mes_venta AS mes, numero_factura, SUM(venta_subtotal) monto
        FROM marts.v_ventas_producto
        WHERE anio_venta=2026 AND tipo_movimiento='out_refund'
        GROUP BY 1, 2 ORDER BY 3 LIMIT 12""")
    if not nc_docs.empty:
        nc_docs = nc_docs.copy()
        nc_docs["mes"] = nc_docs["mes"].map(MESES)
        print("\nNC que resta el DW (top por monto):")
        print(_fmt(nc_docs))

    # ── 4) Diagnóstico: facturas ANULADAS (es_reverso) que el Excel cuenta y el DW excluye ──
    # payment_state='reversed' = factura anulada totalmente por una NC. El DW la excluye
    # (es_reverso IS NOT TRUE) → venta 0. El Excel, si su cruce de NC no la casó, la sigue contando.
    anul = lo.consultar("""
        SELECT EXTRACT(MONTH FROM f.fecha_factura)::int mes,
               COUNT(DISTINCT f.numero) facturas_anuladas,
               SUM(f.credito - f.debito) monto_anulado
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        JOIN marts.dim_producto p ON p.producto_id = f.producto_id
        WHERE EXTRACT(YEAR FROM f.fecha_factura)=2026 AND c.clase_codigo='4'
          AND f.tipo_movimiento='out_invoice' AND f.es_reverso IS TRUE
          AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%' OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%')
        GROUP BY 1 ORDER BY 1""")
    diag = m[["mes", "dif"]].copy()
    diag["mesn"] = diag["mes"].map(inv)
    diag = diag.merge(anul, left_on="mesn", right_on="mes", how="left", suffixes=("", "_a"))
    diag["monto_anulado"] = diag["monto_anulado"].fillna(0)
    diag["facturas_anuladas"] = diag["facturas_anuladas"].fillna(0)
    # residual = lo que NO explican las anuladas (dif + anulado; ≈0 si las anuladas explican el gap)
    diag["residual"] = diag["dif"] + diag["monto_anulado"]
    print("\n" + "=" * 78)
    print("DIAGNÓSTICO — anulaciones reales (es_reverso) por mes. Tras corregir es_reverso (no excluir")
    print("factoring/NC-parcial) el total cuadra ~0%. Estas son las anulaciones REALES excluidas;")
    print("dif = DW − Excel; residual = dif + monto_anulado (lo no explicado ≈ timing/parciales).")
    print("=" * 78)
    print(_fmt(diag[["mes", "dif", "facturas_anuladas", "monto_anulado", "residual"]]))


if __name__ == "__main__":
    main()
