"""
validar_nc.py — Valida que las NOTAS CRÉDITO que el DW resta a las ventas sean solo de las que
contienen PRODUCTOS comerciales, y muestra las que podrían explicar el gap contra el Excel. SOLO
LECTURA (no toca el ETL ni la BD). Reusable.

Contexto: `enlazar_notas_credito()` (etl_dw_marts.py) enlaza NC->factura por la CONCILIACIÓN de CxC,
SIN verificar que la NC tenga producto. El filtro de producto comercial (PCN/KD/TNG/B8, clase 4) solo
actúa en `v_ventas_producto`. Este script confirma que:
  1. Toda NC que efectivamente RESTA en ventas tiene producto comercial (por construcción de la vista).
  2. Cuántas NC del puente `map_nc_factura` NO tienen producto (enlazadas por CxC pero no afectan
     ventas) — para confirmar el "nada más".
  3. NC con producto que NO están en el puente (no conciliadas): restan en su PROPIO mes (fallback
     a fecha_factura) -> candidatas al gap.
  4. NC reubicadas por fecha_venta a un mes distinto al suyo (la redistribución esperada, 19_nc_factura).

Uso:  python validar_nc.py
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, ".")
from classes.db_loader import DBLoader

ANIO = 2026


def _fmt(df):
    df = df.copy()
    for c in df.columns:
        if df[c].dtype.kind in "fi" and not c.startswith("mes"):
            df[c] = df[c].round(0)
    return df.to_string(index=False)


def main():
    lo = DBLoader()

    # ── 1) Composición del puente map_nc_factura. Se prueba la presencia de PRODUCTO COMERCIAL
    #       contra el HECHO (no v_ventas_producto, que excluye los reversos y confundiría el conteo). ──
    resumen = lo.consultar("""
        WITH nc AS (SELECT DISTINCT nc_factura_id FROM marts.map_nc_factura),
             prod AS (   -- NC con >=1 línea clase 4 de producto comercial (con o sin es_reverso)
                 SELECT DISTINCT f.factura_id
                 FROM marts.fact_movimiento_contable f
                 JOIN marts.dim_cuenta   c ON c.cuenta_id  = f.cuenta_id
                 JOIN marts.dim_producto p ON p.producto_id = f.producto_id
                 WHERE c.clase_codigo = '4'
                   AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%'
                        OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%')),
             rev AS (   -- NC que son ANULACIÓN real (es_reverso) → se excluyen de ventas a propósito
                 SELECT DISTINCT factura_id
                 FROM marts.fact_movimiento_contable WHERE es_reverso IS TRUE)
        SELECT
          (SELECT COUNT(*) FROM nc) AS nc_en_puente,
          (SELECT COUNT(*) FROM nc WHERE nc_factura_id IN (SELECT factura_id FROM prod))
              AS con_producto,
          (SELECT COUNT(*) FROM nc WHERE nc_factura_id NOT IN (SELECT factura_id FROM prod))
              AS sin_producto,
          (SELECT COUNT(*) FROM nc WHERE nc_factura_id IN (SELECT factura_id FROM prod)
                                     AND nc_factura_id IN (SELECT factura_id FROM rev))
              AS con_prod_anuladas,
          (SELECT COUNT(*) FROM nc WHERE nc_factura_id IN (SELECT factura_id FROM prod)
                                     AND nc_factura_id NOT IN (SELECT factura_id FROM rev))
              AS con_prod_en_ventas
    """)
    print("=" * 78)
    print("1) map_nc_factura — ¿las NC enlazadas contienen producto comercial?")
    print("   (presencia de producto probada contra el HECHO, no la vista)")
    print("=" * 78)
    print(_fmt(resumen))
    print("\n  con_producto        = NC con >=1 línea clase 4 PCN/KD/TNG/B8")
    print("  sin_producto        = NC de solo servicio/flete/ajuste (no deberían restar ventas)")
    print("  con_prod_anuladas   = con producto pero es_reverso (anulación real, excluida a propósito)")
    print("  con_prod_en_ventas  = con producto y NO reverso → estas SÍ restan en ventas")

    # ── 2) Detalle de las NC del puente SIN ningún producto comercial (las accionables) ──
    sin_prod = lo.consultar("""
        WITH prod AS (
            SELECT DISTINCT f.factura_id
            FROM marts.fact_movimiento_contable f
            JOIN marts.dim_cuenta   c ON c.cuenta_id  = f.cuenta_id
            JOIN marts.dim_producto p ON p.producto_id = f.producto_id
            WHERE c.clase_codigo = '4'
              AND (p.codigo LIKE 'PCN%' OR p.codigo LIKE 'KD%'
                   OR p.codigo LIKE 'TNG%' OR p.codigo LIKE 'B8%'))
        SELECT DISTINCT m.nc_factura_id, f.numero, f.fecha_factura::date AS fecha
        FROM marts.map_nc_factura m
        JOIN marts.fact_movimiento_contable f ON f.factura_id = m.nc_factura_id
        WHERE m.nc_factura_id NOT IN (SELECT factura_id FROM prod)
        ORDER BY 3 DESC
        LIMIT 40
    """)
    print("\n" + "-" * 78)
    print("NC del puente SIN producto comercial (solo servicio/flete; top 40 por fecha):")
    print("-" * 78)
    print(_fmt(sin_prod) if not sin_prod.empty else "  (ninguna — todas las NC del puente traen producto)")

    # ── 3) NC out_refund CON producto que NO están en el puente (restan en su propio mes) ──
    fuera_puente = lo.consultar(f"""
        SELECT v.mes_venta AS mes,
               COUNT(DISTINCT v.factura_id) AS nc,
               SUM(v.venta_subtotal)        AS monto
        FROM marts.v_ventas_producto v
        WHERE v.tipo_movimiento = 'out_refund'
          AND v.anio_venta = {ANIO}
          AND v.factura_id NOT IN (SELECT DISTINCT nc_factura_id FROM marts.map_nc_factura)
        GROUP BY 1 ORDER BY 1
    """)
    print("\n" + "=" * 78)
    print(f"2) NC con producto NO conciliadas ({ANIO}) — restan en SU propio mes (fecha_factura).")
    print("   Son las que el Excel tampoco casa por `ref`; candidatas al gap.")
    print("=" * 78)
    print(_fmt(fuera_puente) if not fuera_puente.empty else "  (ninguna)")

    # ── 4) NC reubicadas por fecha_venta a un mes distinto al de la NC ──
    reubicadas = lo.consultar(f"""
        SELECT EXTRACT(MONTH FROM v.fecha_factura)::int AS mes_nc,
               v.mes_venta                              AS mes_factura,
               COUNT(DISTINCT v.factura_id)             AS nc,
               SUM(v.venta_subtotal)                    AS monto
        FROM marts.v_ventas_producto v
        JOIN marts.map_nc_factura m ON m.nc_factura_id = v.factura_id
        WHERE v.tipo_movimiento = 'out_refund'
          AND v.anio_venta = {ANIO}
          AND date_trunc('month', v.fecha_venta) <> date_trunc('month', v.fecha_factura)
        GROUP BY 1, 2 ORDER BY 4
        LIMIT 30
    """)
    print("\n" + "=" * 78)
    print(f"3) NC reubicadas por fecha_venta ({ANIO}) — mes de la NC vs mes de su factura (top 30):")
    print("=" * 78)
    print(_fmt(reubicadas) if not reubicadas.empty else "  (ninguna)")

    # ── 5) Total de NC que resta el DW por mes (referencia) ──
    total = lo.consultar(f"""
        SELECT v.mes_venta AS mes,
               COUNT(DISTINCT v.factura_id) AS nc,
               SUM(v.venta_subtotal)        AS monto_neto
        FROM marts.v_ventas_producto v
        WHERE v.tipo_movimiento = 'out_refund' AND v.anio_venta = {ANIO}
        GROUP BY 1 ORDER BY 1
    """)
    print("\n" + "=" * 78)
    print(f"4) Total de NC que el DW resta a las ventas por mes ({ANIO}):")
    print("=" * 78)
    print(_fmt(total) if not total.empty else "  (ninguna)")


if __name__ == "__main__":
    main()
