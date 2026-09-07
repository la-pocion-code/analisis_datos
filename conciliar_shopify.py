"""
conciliar_shopify.py — Concilia el export de pedidos de SHOPIFY contra la venta que el DW tiene en
Odoo, y descompone la diferencia concepto por concepto. SOLO LECTURA (no toca el ETL ni la BD).

La llave: `Name` del CSV de Shopify (`#157126`) <-> `account.move.ref` de Odoo, que en el DW vive
en `marts.fact_movimiento_contable.referencia`. Verificado: casan 5.385 de 5.424 pedidos de agosto.

⭐ EL HALLAZGO QUE MOTIVO EL SCRIPT — la venta de Shopify del DW **no es comparable** con el `Total`
de Shopify, y la razon es estructural, no un error:

  * El `Total` de Shopify **incluye el flete** que le cobra al cliente.
  * En Odoo, Shopify aterriza en **una sola cuenta clase 4** (`41353801 VENTA DE COSMETICOS
    GRAVADO 19%`): **no existe cuenta ni producto de flete**. Contablemente el transporte no es
    venta de cosmeticos, asi que Odoo tiene razon en no facturarlo como tal.

  ⇒ Quien compare las dos cifras de frente vera un ~3,4 % de hueco QUE NO EXISTE. Medido en agosto
  de 2026: de los 5.385 pedidos comunes, **cada uno** cumple `dif = 0` **o** `dif = flete`, y el
  residuo ("otra cosa") es **0 pedidos**. El flete explica el 74,5 % de la diferencia del mes.

Dos trampas del CSV, medidas:
  ⚠ La columna `Taxes` **NO es el IVA del 19 %** (7.168,91 en un pedido de 214.500). No sirve para
    el cruce. Aqui el IVA se lee del ASIENTO (base clase 4 + cuenta 2408), no de Shopify.
  ⚠ El CSV se filtra por `Created at`, asi que el borde de mes es ASIMETRICO: un pedido de julio
    facturado en agosto queda fuera del CSV, y uno de agosto facturado en septiembre aun no esta.

⚠ El factor de IVA **no se cablea**. En Shopify sale 1,19000 exacto (a consumidor final todo es
  gravado), pero se MIDE del asiento: en cuanto entre un producto excluido, un 1,19 fijo mentiria.

Uso:  python conciliar_shopify.py --csv D:\\Downloads\\orders_export_1.csv
      python conciliar_shopify.py --csv ... --mes 2026-09
"""
import sys
import logging
import warnings
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")

import pandas as pd

sys.path.insert(0, ".")
from classes.db_loader import DBLoader

# ⚠ DESPUES del import: `classes/db_loader.py` llama a `logging.basicConfig` al importarse y
# devuelve el nivel a INFO, asi que silenciarlo antes no sirve de nada.
logging.getLogger().setLevel(logging.ERROR)

# Campos del CSV a nivel PEDIDO. Shopify exporta una fila por linea de pedido y solo pone estos
# en la PRIMERA fila del pedido, asi que se agregan con `first` (que salta los nulos).
NUM = ["Subtotal", "Taxes", "Total", "Discount Amount", "Shipping"]
OBLIGATORIAS = NUM + ["Name", "Financial Status", "Created at"]


def _fmt(v):
    return f"{v:>18,.0f}".replace(",", ".")


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
    return ped


def leer_dw(lo, mes):
    """Venta de Shopify del mes por DOCUMENTO, con el IVA leido del asiento.

    Se agrega primero por `factura_id` porque una factura son MUCHAS lineas del hecho y la
    `referencia` se repite en todas: sumar sin agrupar por documento multiplicaria el valor.
    """
    ini = f"{mes}-01"
    df = lo.consultar(f"""
      WITH doc AS (
        SELECT f.factura_id,
               max(f.referencia) FILTER (WHERE f.referencia LIKE '#%')       AS name,
               max(f.numero)                                                  AS documento,
               max(f.tipo_movimiento)                                         AS tipo,
               max(f.fecha_factura)::text                                     AS fecha_factura,
               max(f.referencia)                                              AS referencia,
               sum(CASE WHEN c.clase_codigo = '4' THEN f.venta_neta ELSE 0 END)          AS base,
               sum(CASE WHEN c.codigo LIKE '2408%' THEN f.credito - f.debito ELSE 0 END) AS iva
        FROM marts.fact_movimiento_contable f
        JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
        WHERE f.categoria = 'SHOPIFY'
          AND f.es_venta
          AND f.fecha_factura >= DATE '{ini}'
          AND f.fecha_factura <  DATE '{ini}' + INTERVAL '1 month'
        GROUP BY 1)
      SELECT *, base + iva AS con_iva FROM doc
    """)
    if df is None:
        sys.exit("ERROR: la consulta al DW no devolvio nada (revisa la conexion).")
    for c in ("base", "iva", "con_iva"):
        df[c] = pd.to_numeric(df[c])
    return df


def main(csv, mes=None):
    ped = leer_shopify(csv)
    if mes is None:
        mes = ped["mes"].mode().iloc[0]          # el mes que domina el CSV
        fuera = int((ped["mes"] != mes).sum())
        if fuera:
            print(f"⚠ {fuera} pedidos del CSV son de otro mes distinto de {mes}; se conservan "
                  f"(el borde de mes es parte de lo que se mide).")
    print(f"\n{'=' * 94}")
    print(f"CONCILIACION SHOPIFY <-> ODOO/DW   ·   mes {mes}   ·   {csv}")
    print("=" * 94)

    lo = DBLoader()
    dw = leer_dw(lo, mes)
    base_mes = dw["base"].sum()
    iva_mes = dw["iva"].sum()
    con_iva_mes = base_mes + iva_mes
    factor = con_iva_mes / base_mes if base_mes else float("nan")
    print(f"\nDW: {len(dw):,} documentos · base {_fmt(base_mes)} · IVA {_fmt(iva_mes)} "
          f"· con IVA {_fmt(con_iva_mes)}")
    aviso = ("   (todo gravado al 19 %)" if abs(factor - 1.19) < 1e-4
             else "   ⚠ no es 1,19: hay producto no gravado en la mezcla")
    print(f"    factor de IVA MEDIDO del asiento: {factor:.5f}{aviso}")

    nombres_dw = set(dw["name"].dropna())
    por_name = (dw.dropna(subset=["name"]).groupby("name", as_index=False)
                  .agg(con_iva=("con_iva", "sum")))
    comunes = ped.merge(por_name, on="name", how="inner")
    comunes["dif"] = comunes["total"] - comunes["con_iva"]

    solo_sh = ped[~ped["name"].isin(nombres_dw)]
    sh_nopag = solo_sh[solo_sh["estado"] != "paid"]
    sh_pag = solo_sh[solo_sh["estado"] == "paid"]
    solo_od = dw[dw["name"].isna() | ~dw["name"].isin(set(ped["name"]))]
    od_fact = solo_od[solo_od["tipo"] == "out_invoice"]
    od_nc = solo_od[solo_od["tipo"] == "out_refund"]

    cero = comunes["dif"].abs() < 1
    flete = (~cero) & ((comunes["dif"] - comunes["envio"]).abs() < 1)
    otro = (~cero) & (~flete)
    flete_cobrado = comunes.loc[flete, "envio"].sum()

    print(f"\n{'-' * 94}\nEL PUENTE\n{'-' * 94}")
    estados_nopag = ", ".join(sorted(sh_nopag["estado"].dropna().unique())) or "—"
    tot = 0.0
    for etiqueta, valor, nota in [
        (f"Shopify `Total` ({len(ped):,} pedidos)", ped["total"].sum(), ""),
        (f"(−) {len(sh_nopag):,} pedidos sin pagar ({estados_nopag})",
         -sh_nopag["total"].sum(), "correcto: Odoo no factura lo no pagado"),
        (f"(−) {len(sh_pag):,} pedidos PAGADOS sin factura ESTE mes",
         -sh_pag["total"].sum(), "⚠ revisar: suele ser borde de mes, no un hueco"),
        (f"(−) flete cobrado en {int(flete.sum()):,} pedidos",
         -flete_cobrado, "correcto: Odoo no factura el flete"),
    ]:
        tot += valor
        print(f"  {etiqueta:<58s}{_fmt(valor)}   {nota}")

    odoo_com = comunes["con_iva"].sum()
    ok_com = "✓ AL PESO" if abs(tot - odoo_com) < 1 else f"⚠ descuadre {tot - odoo_com:,.0f}"
    print(f"  {'':<58s}{'-' * 18}")
    print(f"  {f'= producto de los {len(comunes):,} pedidos comunes':<58s}{_fmt(tot)}")
    print(f"  {'  Odoo con IVA de esos mismos pedidos':<58s}{_fmt(odoo_com)}   {ok_com}")

    tot = odoo_com
    for etiqueta, valor, nota in [
        (f"(+) {len(od_fact):,} facturas de pedidos de otro mes", od_fact["con_iva"].sum(),
         "borde de mes (el CSV filtra por Created at)"),
        (f"(−) {len(od_nc):,} notas credito (retractos/devoluciones)", od_nc["con_iva"].sum(),
         "devoluciones reales, sin # de Shopify"),
    ]:
        tot += valor
        print(f"  {etiqueta:<58s}{_fmt(valor)}   {nota}")
    ok_mes = ("✓ AL PESO" if abs(tot - con_iva_mes) < 2
              else f"⚠ descuadre {tot - con_iva_mes:,.0f}")
    print(f"  {'':<58s}{'-' * 18}")
    print(f"  {f'= Odoo/DW {mes} con IVA':<58s}{_fmt(tot)}")
    print(f"  {'  el DW dice':<58s}{_fmt(con_iva_mes)}   {ok_mes}")

    dif_total = ped["total"].sum() - con_iva_mes
    pct = f"{100 * flete_cobrado / dif_total:.1f} %" if dif_total else "n/d"
    print(f"\n  DIFERENCIA Shopify − Odoo: {_fmt(dif_total)}"
          f"   ·   de ella, FLETE: {_fmt(flete_cobrado)} ({pct})")

    print(f"\n{'-' * 94}\nCLASIFICACION DE LOS {len(comunes):,} PEDIDOS COMUNES\n{'-' * 94}")
    for etiqueta, f in [("dif = 0        (Odoo == Shopify al peso)", cero),
                        ("dif = flete    (Shopify cobro envio, Odoo no lo factura)", flete),
                        ("⛔ OTRA COSA   (residuo sin explicar)", otro)]:
        print(f"  {etiqueta:<60s}{int(f.sum()):>7,} pedidos {_fmt(comunes.loc[f, 'dif'].sum())}")
    regalado = comunes.loc[cero & (comunes["envio"] > 0)]
    print(f"\n  flete FACTURADO por Shopify:   {_fmt(comunes['envio'].sum())}")
    print(f"  flete REGALADO (envio gratis): {_fmt(regalado['envio'].sum())} "
          f"en {len(regalado):,} pedidos")

    if int(otro.sum()):
        print("\n  ⛔ HAY RESIDUO: estos pedidos no los explica ni el flete. Es el problema NUEVO.")
        orden = comunes[otro]["dif"].abs().sort_values(ascending=False).index
        print(comunes[otro].reindex(orden)[["name", "estado", "total", "subtotal", "envio",
                                            "descuento", "con_iva", "dif"]]
              .head(15).to_string(index=False))
    else:
        print("\n  ✓ RESIDUO 0: el flete explica TODA la diferencia de los pedidos comunes.")

    if len(sh_pag):
        print(f"\n{'-' * 94}")
        print(f"A REVISAR: {len(sh_pag)} pedidos PAGADOS en Shopify sin factura en {mes} "
              f"({sh_pag['total'].sum():,.0f})")
        print("-" * 94)
        print(sh_pag[["name", "creado", "total"]].sort_values("creado").to_string(index=False))
        print("\n  ⚠ ANTES de llamarlo hueco: si son de los ultimos dias del mes, lo normal es que")
        print("  se facturaran al mes siguiente. Medido en agosto de 2026, los 5 que salian aqui se")
        print("  facturaron el 1-2 de septiembre ⇒ eran BORDE DE MES, no un hueco. Comprobarlo:")
        print("    SELECT referencia, numero, fecha_factura FROM marts.fact_movimiento_contable")
        print(f"     WHERE referencia IN ({', '.join(repr(n) for n in sh_pag['name'].head(5))});")

    if len(solo_od):
        print(f"\n{'-' * 94}")
        print(f"SOLO EN ODOO: {len(solo_od)} documentos (base {solo_od['base'].sum():,.0f})")
        print("-" * 94)
        print(solo_od[["documento", "tipo", "fecha_factura", "name", "referencia", "base"]]
              .sort_values("base").to_string(index=False))

    print(f"\n{'-' * 94}\nCOMO LEER ESTO\n{'-' * 94}")
    print("· El `Total` de Shopify lleva FLETE y la venta del DW no: no se comparan de frente.")
    print("  Odoo tiene UNA sola cuenta clase 4 para Shopify y ninguna de transporte.")
    print("· `Taxes` del CSV NO es el IVA del 19 %. El IVA de este informe sale del ASIENTO.")
    print("· El borde de mes es asimetrico: el CSV filtra por `Created at` y el DW por")
    print("  `fecha_factura`. Por eso aparecen facturas de pedidos de otro mes.")
    print("· Las notas credito no traen `#` de Shopify: su `ref` es 'Reversion de: FExxxxx, motivo'.")
    print("· Las cifras de un mes en curso CAMBIAN: el ETL del DW corre cada 15 min.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Concilia el export de Shopify contra el DW.")
    ap.add_argument("--csv", required=True, help="ruta del orders_export de Shopify")
    ap.add_argument("--mes", default=None,
                    help="mes a conciliar YYYY-MM (por defecto, el que domina el CSV)")
    a = ap.parse_args()
    main(a.csv, a.mes)
