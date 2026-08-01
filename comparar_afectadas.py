"""
comparar_afectadas.py — Cruza los Excel de FACTURAS AFECTADAS (resultado del pipeline de Excel) contra
lo que el DW resta por notas crédito, para ubicar qué facturas/NC NO están cruzando y explicar el gap
del ~4%. SOLO LECTURA (no toca el ETL ni la BD). Reusable.

Los .xlsx de `.../RAW DATA/FACTURAS AFECTADAS` son exportes de Odoo con columnas
`Líneas de factura/Número · Producto · Cantidad · Total` (todo negativo = lo que el Excel DESCUENTA de
cada factura por devoluciones). No traen fecha ni empresa. Varios exportes se solapan (las mismas
líneas aparecen en más de un archivo) -> se DEDUPLICA por (numero, producto, cantidad, total).

Comparación por (numero de FACTURA original, codigo de producto):
  · Excel  = suma de `Total` de las líneas afectadas.
  · DW     = NC enlazadas a esa factura (map_nc_factura) × proporcion, a grano de producto.
Buckets del gap:
  · solo_excel : el Excel descuenta la factura pero el DW NO la cruza (NC no enlazada / distinta).
  · solo_dw    : el DW resta una NC que el Excel no casó por `ref`.

Uso:  python comparar_afectadas.py
"""
import glob
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

CARPETA = r"G:\Otros ordenadores\Mi portátil\VENTA MENSUAL\RAW DATA\FACTURAS AFECTADAS"
COD_RE = re.compile(r"^\[([A-Za-z0-9]+)\]")
COMERCIAL = ("PCN", "KD", "TNG", "B8")


def _fmt(df):
    df = df.copy()
    for c in df.columns:
        if df[c].dtype.kind in "fi":
            df[c] = df[c].round(0)
    return df.to_string(index=False)


def cargar_afectadas():
    """Lee y deduplica los .xlsx. Devuelve líneas afectadas a grano (numero, codigo)."""
    frames = []
    for ruta in sorted(glob.glob(os.path.join(CARPETA, "*.xlsx"))):
        x = pd.read_excel(ruta)
        if x.empty:
            continue
        # Cabeceras con encoding roto: se toman por POSICIÓN (0 numero,1 producto,2 cantidad,3 total).
        x = x.iloc[:, :4]
        x.columns = ["numero", "producto", "cantidad", "total"]
        frames.append(x)
    df = pd.concat(frames, ignore_index=True)
    # Dedup: las mismas líneas aparecen en varios exportes solapados.
    df = df.drop_duplicates(subset=["numero", "producto", "cantidad", "total"])
    df["numero"] = df["numero"].astype(str).str.strip()
    df["codigo"] = df["producto"].astype(str).str.extract(COD_RE)[0]
    df["total"] = pd.to_numeric(df["total"], errors="coerce")
    df = df[df["codigo"].notna()].copy()
    df["comercial"] = df["codigo"].str.upper().str.startswith(COMERCIAL)
    return df


def main():
    lo = DBLoader()
    af = cargar_afectadas()
    af_com = af[af["comercial"]].copy()

    print("=" * 78)
    print("EXCEL — FACTURAS AFECTADAS (deduplicado)")
    print("=" * 78)
    print(f"  líneas afectadas: {len(af):,}  (comerciales PCN/KD/TNG/B8: {len(af_com):,})")
    print(f"  facturas distintas: {af['numero'].nunique():,}")
    print(f"  total descontado (todas):       {af['total'].sum():,.0f}")
    print(f"  total descontado (comerciales):  {af_com['total'].sum():,.0f}")

    # ── DW: una sola pasada pesada + cruce en pandas ──────────────────────────────────────────
    # No hay índice en factura_id/numero/tipo_movimiento -> cada consulta es un seq-scan de 4,3M.
    # Se minimiza: (a) las LÍNEAS de NC salen con el índice es_venta (son pocas miles);
    # (b) el puente es una tabla chica; (c) un ÚNICO seq-scan trae los `numero` de las facturas
    # involucradas. El prorrateo y la agrupación se hacen en pandas. Misma semántica que
    # v_ventas_producto: venta_neta * proporcion, clase 4, es_venta, es_reverso IS NOT TRUE, comercial.
    print("\n  (consultando el DW: líneas de NC + puente + números de factura...)")
    ncl = lo.consultar("""
        SELECT f.factura_id AS nc_id, p.codigo AS codigo, f.venta_neta AS venta_neta
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta   c ON c.cuenta_id   = f.cuenta_id
        JOIN marts.dim_producto p ON p.producto_id = f.producto_id
        WHERE f.es_venta IS TRUE AND f.tipo_movimiento = 'out_refund'
          AND c.clase_codigo = '4' AND f.es_reverso IS NOT TRUE
          AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%'
               OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%')
    """)
    mapa = lo.consultar("SELECT nc_factura_id, factura_id, proporcion FROM marts.map_nc_factura")

    # numeros de las facturas involucradas (NC + facturas originales) — un solo seq-scan
    ids = sorted(set(ncl["nc_id"]) | set(mapa["factura_id"]) | set(mapa["nc_factura_id"]))
    ph = ",".join(["%s"] * len(ids))
    numeros = lo.consultar(
        f"SELECT DISTINCT factura_id, numero FROM marts.fact_movimiento_contable WHERE factura_id IN ({ph})",
        ids)
    id2num = dict(zip(numeros["factura_id"], numeros["numero"].astype(str).str.strip()))

    # DW enlazado: atribuye venta_neta*proporcion a la FACTURA ORIGINAL + codigo (grano de la NC)
    enl = ncl.merge(mapa, left_on="nc_id", right_on="nc_factura_id", how="inner")
    enl["dw_total"] = enl["venta_neta"] * enl["proporcion"]
    enl["numero"] = enl["factura_id"].map(id2num)
    dw = enl.groupby(["numero", "codigo"], dropna=False)["dw_total"].sum().reset_index()

    # Totales de contexto (el prorrateo suma 1 por línea -> sumar venta_neta una vez por línea)
    nc_enlazadas = ncl[ncl["nc_id"].isin(set(mapa["nc_factura_id"]))]["venta_neta"].sum()
    nc_totales = ncl["venta_neta"].sum()
    print("\n" + "=" * 78)
    print("DW — notas crédito que resta el DW (out_refund comercial, todos los años)")
    print("=" * 78)
    print(f"  nc_enlazadas (atribuidas a su factura, comparables con el Excel): {nc_enlazadas:,.0f}")
    print(f"  nc_totales   (enlazadas + no conciliadas, restan en su propio mes): {nc_totales:,.0f}")
    print(f"  no conciliadas (restan en su propio mes): {nc_totales - nc_enlazadas:,.0f}")

    # ── Cruce por (numero, codigo): matched / solo_excel / solo_dw ──
    ex = (af_com.groupby(["numero", "codigo"])["total"].sum()
          .rename("excel_total").reset_index())
    dw["numero"] = dw["numero"].astype(str).str.strip()
    m = ex.merge(dw, on=["numero", "codigo"], how="outer", indicator=True)
    m["excel_total"] = m["excel_total"].fillna(0)
    m["dw_total"] = m["dw_total"].fillna(0)
    m["dif"] = m["dw_total"] - m["excel_total"]

    solo_excel = m[m["_merge"] == "left_only"].copy()
    solo_dw = m[m["_merge"] == "right_only"]
    ambos = m[m["_merge"] == "both"]
    print("\n" + "=" * 78)
    print("CRUCE por (factura, producto) — dónde está el gap")
    print("=" * 78)
    print(f"  en AMBOS      : {len(ambos):,} líneas · Excel={ambos['excel_total'].sum():,.0f} · "
          f"DW={ambos['dw_total'].sum():,.0f} · dif={ambos['dif'].sum():,.0f}")
    print(f"  solo EXCEL    : {len(solo_excel):,} líneas · {solo_excel['excel_total'].sum():,.0f}"
          "   (el DW NO cruza; ver desglose anuladas vs gap real abajo)")
    print(f"  solo DW       : {len(solo_dw):,} líneas · {solo_dw['dw_total'].sum():,.0f}"
          "   (el Excel no casó estas NC por `ref`; el DW SÍ las resta -> DW más correcto)")

    # ── Clasifica las facturas del Excel por si el DW las ANULA (es_reverso) ──────────────────────
    # Clave del gap: si una factura del Excel está 100% anulada (es_reverso), el DW NO la resta con
    # una NC: la EXCLUYE entera de las ventas (bruto 0). El Excel la cuenta y la resta en 'afectadas'.
    # Netean IGUAL (0 y 0), solo cambia el mecanismo -> esas 'solo_excel' NO son gap real.
    fac_excel = af_com["numero"].dropna().astype(str).unique().tolist()
    ph2 = ",".join(["%s"] * len(fac_excel))
    est = lo.consultar(f"""
        SELECT f.numero,
               bool_and(COALESCE(f.es_reverso, false)) FILTER (WHERE c.clase_codigo='4') AS anulada
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        WHERE f.numero IN ({ph2})
        GROUP BY 1
    """, fac_excel)
    est["numero"] = est["numero"].astype(str).str.strip()
    anuladas = set(est.loc[est["anulada"] == True, "numero"])          # noqa: E712
    existentes = set(est["numero"])
    solo_excel["clase"] = solo_excel["numero"].map(
        lambda n: "anulada (DW la excluye)" if n in anuladas
        else ("no existe en DW" if n not in existentes else "GAP REAL (venta viva sin NC en DW)"))

    resumen = (solo_excel.groupby("clase")
               .agg(lineas=("excel_total", "size"), monto=("excel_total", "sum"))
               .sort_values("monto").reset_index())
    print("\n" + "-" * 78)
    print("DESGLOSE de 'solo EXCEL' (lo que el Excel resta y el DW no cruza):")
    print("-" * 78)
    print(_fmt(resumen))
    print("\n  · anulada (DW la excluye) = factura 100% es_reverso: el DW la quita entera del bruto,")
    print("    no hace falta restarla -> netean igual (0 y 0). NO es gap real.")
    print("  · GAP REAL = factura viva (no anulada) que el Excel descuenta pero el DW no tiene NC.")
    print("  · no existe en DW = otra empresa/periodo no cargado.")

    gap = solo_excel[solo_excel["clase"].str.startswith("GAP REAL")]
    fac_gap = (gap.groupby("numero")["excel_total"].sum().sort_values().head(25).reset_index())
    print("\n" + "-" * 78)
    print("GAP REAL — facturas VIVAS que el Excel descuenta y el DW no cruza (top 25):")
    print("-" * 78)
    print(_fmt(fac_gap) if not fac_gap.empty else "  (ninguna — el gap 'solo_excel' es solo anulaciones)")

    # ── NC que el DW resta y el Excel no reconoce (top por monto) ──
    fac_solo_dw = (solo_dw.groupby("numero")["dw_total"].sum()
                   .sort_values().head(25).reset_index())
    print("\n" + "-" * 78)
    print("Facturas cuya NC resta el DW y el Excel NO reconoce (top 25 por monto):")
    print("-" * 78)
    print(_fmt(fac_solo_dw) if not fac_solo_dw.empty else "  (ninguna)")


if __name__ == "__main__":
    main()
