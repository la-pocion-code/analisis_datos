"""
estado_dw.py — Estado del data warehouse (marts) de un solo comando.

    python estado_dw.py            # estado rápido (proceso, conteos por año, checks)
    python estado_dw.py --odoo     # además compara conteos por año contra Odoo (más lento)

Muestra: si el ETL está corriendo, conteo del hecho por año, partida doble, cobertura de
tipo_cliente y fecha DATE, y (con --odoo) el cuadre por año vs Odoo.
"""
import os
import sys
import argparse
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")  # consola Windows (cp1252) → evita UnicodeEncodeError
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes.db_loader import DBLoader


def _fmt(n):
    return f"{int(n):,}".replace(",", ".")


def proceso_vivo():
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
            capture_output=True, text=True, timeout=15).stdout
        return sum(1 for ln in out.splitlines() if "etl_dw_marts" in ln)
    except Exception:
        return "?"


def q(loader, sql):
    return loader.consultar(sql)


def main(odoo=False):
    loader = DBLoader()

    vivos = proceso_vivo()
    print("=" * 60)
    print(f"ETL etl_dw_marts corriendo: {'SÍ' if vivos and vivos != '?' and vivos > 0 else 'no'}"
          f"  ({vivos} proceso(s))")

    # Conteo por año
    df = q(loader, "SELECT fecha_key/10000 AS anio, COUNT(*) AS n "
                   "FROM marts.fact_movimiento_contable GROUP BY 1 ORDER BY 1")
    total = q(loader, "SELECT COUNT(*) n FROM marts.fact_movimiento_contable").n[0]
    print("\n-- Hecho por año --")
    if df is not None:
        for _, r in df.iterrows():
            print(f"   {int(r.anio)}: {_fmt(r.n):>12}")
    print(f"   TOTAL: {_fmt(total):>10}")

    # Checks de calidad
    chk = q(loader, """
        SELECT
          COUNT(*) FILTER (WHERE fecha IS NULL)            AS sin_fecha,
          COUNT(*) FILTER (WHERE es_venta AND es_reverso)  AS ventas_reverso,
          MIN(fecha) AS desde, MAX(fecha) AS hasta
        FROM marts.fact_movimiento_contable""")
    tc = q(loader, "SELECT COUNT(*) FILTER (WHERE tipo_cliente IS NOT NULL) con, COUNT(*) tot "
                   "FROM marts.dim_tercero")
    print("\n-- Chequeos --")
    if chk is not None:
        print(f"   Rango fechas: {chk.desde[0]} .. {chk.hasta[0]}")
        print(f"   Filas sin fecha DATE: {_fmt(chk.sin_fecha[0])}")
    if tc is not None:
        print(f"   dim_tercero con tipo_cliente: {_fmt(tc.con[0])} / {_fmt(tc.tot[0])}")

    # Partida doble por empresa (debe ser ~0)
    pd_ = q(loader, "SELECT empresa_id, ROUND(SUM(debito)-SUM(credito),2) desc_partida "
                    "FROM marts.fact_movimiento_contable GROUP BY 1 ORDER BY 1")
    print("\n-- Partida doble (SUM debito - SUM credito, debe ≈ 0) --")
    if pd_ is not None:
        for _, r in pd_.iterrows():
            print(f"   empresa {int(r.empresa_id) if r.empresa_id is not None else '-'}: {r.desc_partida}")

    # Comparación vs Odoo (opcional)
    if odoo:
        import xmlrpc.client
        from dotenv import load_dotenv
        load_dotenv()
        url = os.getenv("url").rstrip("/"); db = os.getenv("db")
        user = os.getenv("username_odoo"); pw = os.getenv("password")
        uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, user, pw, {})
        m = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        print("\n-- Cuadre por año: marts vs Odoo (posted) --")
        for y in [2024, 2025, 2026]:
            dom = [["parent_state", "=", "posted"],
                   ["date", ">=", f"{y}-01-01"], ["date", "<=", f"{y}-12-31"]]
            oc = m.execute_kw(db, uid, pw, "account.move.line", "search_count", [dom])
            mc = q(loader, f"SELECT COUNT(*) n FROM marts.fact_movimiento_contable "
                           f"WHERE fecha_key BETWEEN {y}0101 AND {y}1231").n[0]
            ok = "OK" if int(mc) == int(oc) else f"faltan {_fmt(oc-int(mc))}"
            print(f"   {y}: marts={_fmt(mc):>12}  odoo={_fmt(oc):>12}  [{ok}]")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--odoo", action="store_true", help="comparar conteos por año contra Odoo")
    main(ap.parse_args().odoo)
