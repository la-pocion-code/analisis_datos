"""
validar_ventas.py — Concilia marts.v_ventas_producto (DW) contra el base_ventas del pipeline de Excel
(CSV en CLEAN DATA). Solo lectura. Reusable cada mes.

Alineación necesaria para que cuadre (ver docs/GUIA_OPERACION.md §7):
  1. TODAS las empresas (el Excel no distingue; ene-2026 estaba en empresa 1, luego en la 8).
  2. Por FECHA DE FACTURA (el Excel agrupa por invoice_date, no por fecha contable).
  3. Producto comercial [PCN/[KD/[TNG/[B8 (ya filtrado en ambos lados).

Nota: `es_reverso` = ANULACIÓN real (factura + NC de reversión ≥99%), NO `payment_state='reversed'`
(que en este Odoo lo pone el FACTORING y las NC PARCIALES → ventas reales que SÍ cuentan). Con eso el
total 2026 Excel vs DW ≈ 0%. Diferencias residuales esperadas:
  - Timing: un CSV viejo vs el DW recién cargado (más facturas) → el DW puede quedar más alto.
  - NC/anulaciones fechadas en un mes distinto al de la factura.

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
# Índices de columna (la cabecera trae acentos con encoding inconsistente → se leen por posición):
#   0 NUMERO_FACTURA · 3 MES · 4 AÑO · 7 CATEGORÍA · 8 PRODUCTO · 9 CANTIDAD · 12 TOTAL($) neto
COLS = [0, 3, 4, 7, 8, 9, 12]
NOMBRES = ["numero", "mes", "anio", "categoria", "producto", "cantidad", "total_cop"]
COD_RE = re.compile(r"^\[([A-Za-z0-9]+)\]")
MESES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
         7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}


def cargar_excel():
    dfs = []
    for f in FILES_2026:
        ruta = os.path.join(CLEAN_DATA, f)
        d = pd.read_csv(ruta, sep=";", decimal=",", encoding="utf-8",
                        usecols=COLS, names=NOMBRES, header=0)
        dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce")
    df["total_cop"] = pd.to_numeric(df["total_cop"], errors="coerce")
    df["codigo"] = df["producto"].str.extract(COD_RE)[0]
    return df[df["anio"] == 2026].copy()


def _fmt(df, val="venta"):
    df = df.copy()
    for c in df.columns:
        if df[c].dtype.kind in "fi" and c != "mes":
            df[c] = df[c].round(0)
    return df.to_string(index=False)


def main():
    lo = DBLoader()
    xl = cargar_excel()

    # ── 1) Conciliación mensual: Excel vs DW (todas las empresas, por fecha de factura) ──
    xl_mes = xl.groupby("mes").agg(excel=("total_cop", "sum"),
                                   lineas_xl=("total_cop", "size")).reset_index()
    dw_mes = lo.consultar("""
        SELECT EXTRACT(MONTH FROM fecha_factura)::int mes,
               SUM(venta_subtotal) dw, COUNT(*) lineas_dw
        FROM marts.v_ventas_producto
        WHERE EXTRACT(YEAR FROM fecha_factura) = 2026
        GROUP BY 1 ORDER BY 1""")
    m = xl_mes.merge(dw_mes, on="mes", how="outer").sort_values("mes")
    m["dif"] = m["dw"] - m["excel"]
    m["dif_%"] = (m["dif"] / m["excel"] * 100).round(1)
    m["mes"] = m["mes"].map(MESES)
    print("=" * 78)
    print("CONCILIACIÓN MENSUAL 2026 — v_ventas_producto (DW) vs base_ventas (Excel)")
    print("Todas las empresas · por FECHA DE FACTURA · producto comercial · neto")
    print("=" * 78)
    print(_fmt(m))
    tot_xl, tot_dw = m["excel"].sum(), m["dw"].sum()
    print(f"\nTOTAL 2026  Excel={tot_xl:,.0f}  DW={tot_dw:,.0f}  "
          f"dif={tot_dw - tot_xl:,.0f} ({(tot_dw - tot_xl) / tot_xl * 100:+.1f}%)")

    # ── 2) Causa a nivel categoría para los meses fuera de ±2% ──
    fuera = m[m["dif_%"].abs() > 2]["mes"].tolist()
    inv = {v: k for k, v in MESES.items()}
    for mes_nom in fuera:
        mesn = inv[mes_nom]
        xlc = (xl[xl["mes"] == mesn].groupby("categoria")["total_cop"].sum()
               .rename("excel").reset_index())
        dwc = lo.consultar(f"""
            SELECT COALESCE(categoria,'(nulo)') categoria, SUM(venta_subtotal) dw
            FROM marts.v_ventas_producto
            WHERE EXTRACT(YEAR FROM fecha_factura)=2026 AND EXTRACT(MONTH FROM fecha_factura)={mesn}
            GROUP BY 1""")
        c = xlc.merge(dwc, on="categoria", how="outer").fillna(0)
        c["dif"] = c["dw"] - c["excel"]
        c = c.sort_values("dif").reset_index(drop=True)
        print("\n" + "-" * 78)
        print(f"CAUSA {mes_nom} 2026 — Excel vs DW por CATEGORÍA (ordenado por diferencia)")
        print("-" * 78)
        print(_fmt(c))

    # ── 3) Diagnóstico: facturas ANULADAS (es_reverso) que el Excel cuenta y el DW excluye ──
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
