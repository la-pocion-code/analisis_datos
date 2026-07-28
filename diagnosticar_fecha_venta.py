"""
diagnosticar_fecha_venta.py — Audita la columna `fecha_venta` de marts.v_ventas_producto y explica,
peso por peso, en qué se diferencia de medir por `fecha_factura` (y por tanto del Excel de CLEAN DATA).
SOLO LECTURA (no toca el ETL ni la BD). Reusable mes a mes.

Contexto: `fecha_venta` = COALESCE(map_nc_factura.fecha_venta, fecha_factura) (sql/marts/14_ventas.sql).
Solo puede mover NOTAS CRÉDITO: el puente `marts.map_nc_factura` se puebla únicamente con `out_refund`
(etl_dw_marts.py::enlazar_notas_credito), así que una FACTURA siempre conserva su propia fecha.

Caso que motivó el script (abr-2026 +652,8M / mar-2026 −573,0M frente a `fecha_factura`):
  RINV254 (28-abr-2026, NOVAVENTA, −662,2M) es la ANULACIÓN TOTAL de FE7301 (09-mar-2026, +662,2M):
  mismo importe, mismas 18 líneas. Por `fecha_venta` ambas caen en marzo y netean a 0 (correcto: la
  venta no existió). Por `fecha_factura` marzo se queda una venta fantasma de +662M y abril un crédito
  fantasma de −662M. El Excel comete ese error porque casa NC↔factura por `ref`+producto y DESCARTA las
  que no matchean (clase_reportes_new.py); RINV254 tiene `referencia` NULL. ⇒ medir por `fecha_venta`.

Qué reporta:
  1. Integridad de la columna: ninguna FACTURA se mueve de mes; nadie se queda sin fecha.
  2. Efecto de reubicación por mes: neto por `mes_venta` vs por mes de `fecha_factura`.
  3. Detalle auditable: las NC responsables de cada mes con efecto material.
  4. Anulaciones que `es_reverso` NO detecta (Odoo dejó `reversed_entry_id` NULL): netean bien pero
     inflan el BRUTO y las cantidades en ambos sentidos.
  5. Notas débito contadas como venta + NC parcialmente conciliadas con proporcion=1 (riesgo latente).

Uso:  python diagnosticar_fecha_venta.py
"""
import sys
import logging
import warnings

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from classes.db_loader import DBLoader

logging.disable(logging.INFO)   # el informe se lee mejor sin el log de conexión de DBLoader

ANIO = 2026
UMBRAL_MM = 50.0     # millones: a partir de qué efecto mensual se pide el detalle de NC del bloque 3
MM = 1e6


def _p(df, vacio="(sin filas)"):
    if df is None:
        return "ERROR EN LA CONSULTA (ver log)"
    if df.empty:
        return vacio
    return df.to_string(index=False)


def _titulo(n, txt):
    print()
    print("=" * 115)
    print(f"{n}) {txt}")
    print("=" * 115)


def main():
    lo = DBLoader()

    # ── 1) Integridad: la columna solo puede mover NOTAS CRÉDITO ────────────────────────────────
    _titulo(1, "INTEGRIDAD — ninguna FACTURA cambia de mes (fecha_venta_distinta debe ser 0 en out_invoice)")
    print(_p(lo.consultar("""
        SELECT tipo_movimiento,
               COUNT(*)                                                           AS lineas,
               COUNT(*) FILTER (WHERE fecha_venta IS DISTINCT FROM fecha_factura)  AS fecha_venta_distinta,
               COUNT(*) FILTER (WHERE fecha_factura IS NULL)                       AS sin_fecha_factura,
               COUNT(*) FILTER (WHERE fecha_venta   IS NULL)                       AS sin_fecha_venta
        FROM marts.v_ventas_producto
        GROUP BY 1 ORDER BY 1
    """)))
    print("\n   Sanidad del prorrateo del puente (proporcion debe sumar 1 por NC, nunca >1 ni <0):")
    print(_p(lo.consultar("""
        SELECT COUNT(DISTINCT nc_factura_id)                                      AS nc_total,
               COUNT(*)                                                           AS pares,
               COUNT(DISTINCT nc_factura_id) FILTER (WHERE proporcion > 1.0000001) AS nc_prop_mayor_1,
               COUNT(DISTINCT nc_factura_id) FILTER (WHERE proporcion < 0)         AS nc_prop_negativa,
               MIN(fecha_venta) AS fecha_min, MAX(fecha_venta) AS fecha_max
        FROM marts.map_nc_factura
    """)))
    print(_p(lo.consultar("""
        SELECT ROUND(suma::numeric, 4) AS suma_proporcion, COUNT(*) AS n_nc
        FROM (SELECT nc_factura_id, SUM(proporcion) AS suma
              FROM marts.map_nc_factura GROUP BY 1) s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 5
    """)))

    # ── 2) Efecto de reubicación de las NC, mes a mes ───────────────────────────────────────────
    _titulo(2, f"EFECTO DE REUBICACIÓN {ANIO} — neto por MES_VENTA vs por MES DE FACTURA (millones COP)")
    efecto = lo.consultar(f"""
        WITH a AS (SELECT mes_venta AS mes, SUM(venta_subtotal) x
                   FROM marts.v_ventas_producto WHERE anio_venta = {ANIO} GROUP BY 1),
             b AS (SELECT EXTRACT(MONTH FROM fecha_factura)::int AS mes, SUM(venta_subtotal) y
                   FROM marts.v_ventas_producto
                   WHERE EXTRACT(YEAR FROM fecha_factura) = {ANIO} GROUP BY 1)
        SELECT COALESCE(a.mes, b.mes)      AS mes,
               ROUND(a.x / {MM}, 1)        AS por_fecha_venta,
               ROUND(b.y / {MM}, 1)        AS por_fecha_factura,
               ROUND((a.x - b.y) / {MM}, 1) AS efecto_reubicacion
        FROM a FULL JOIN b ON a.mes = b.mes ORDER BY 1
    """)
    print(_p(efecto))
    print("\n   Composición por mes_venta (bruto = facturas, nc = notas crédito que restan):")
    print(_p(lo.consultar(f"""
        SELECT mes_venta,
               ROUND(SUM(venta_subtotal) FILTER (WHERE tipo_movimiento = 'out_invoice') / {MM}, 1) AS bruto_mm,
               ROUND(SUM(venta_subtotal) FILTER (WHERE tipo_movimiento = 'out_refund')  / {MM}, 1) AS nc_mm,
               ROUND(SUM(venta_subtotal) / {MM}, 1)                                               AS neto_mm
        FROM marts.v_ventas_producto WHERE anio_venta = {ANIO} GROUP BY 1 ORDER BY 1
    """)))

    # ── 3) Detalle auditable de los meses con efecto material ───────────────────────────────────
    meses = []
    if efecto is not None and not efecto.empty:
        col = efecto["efecto_reubicacion"].astype(float).abs()
        meses = efecto.loc[col > UMBRAL_MM, "mes"].dropna().astype(int).tolist()
    _titulo(3, f"DETALLE — NC responsables de los meses con |efecto| > {UMBRAL_MM:.0f}M: {meses or 'ninguno'}")
    if meses:
        # ⚠ `fact_movimiento_contable` NO tiene índice por `factura_id`: la identidad de los documentos
        # se resuelve con UN solo scan agregado sobre los ids ya reducidos (un `SELECT DISTINCT` del
        # hecho completo tarda >15 min y bloquea el DDL de las vistas).
        lista = ",".join(str(m) for m in meses)
        print(_p(lo.consultar(f"""
            WITH nc_tot AS (   -- valor TOTAL de cada NC (los trozos prorrateados suman el entero)
                SELECT v.factura_id, SUM(v.venta_subtotal) AS valor
                FROM marts.v_ventas_producto v
                WHERE v.tipo_movimiento = 'out_refund'
                  AND date_trunc('month', v.fecha_venta) <> date_trunc('month', v.fecha_factura)
                  AND (v.mes_venta IN ({lista})
                       OR EXTRACT(MONTH FROM v.fecha_factura)::int IN ({lista}))
                  AND (v.anio_venta = {ANIO} OR EXTRACT(YEAR FROM v.fecha_factura) = {ANIO})
                GROUP BY 1
            ),
            ids AS (
                SELECT factura_id FROM nc_tot
                UNION
                SELECT m.factura_id FROM marts.map_nc_factura m
                WHERE m.nc_factura_id IN (SELECT factura_id FROM nc_tot)
            ),
            docs AS (
                SELECT factura_id, MIN(numero) AS numero, MIN(fecha_factura) AS fecha,
                       MIN(referencia) AS referencia, MIN(reversed_factura_id) AS reversed_id,
                       MIN(tercero_id) AS tercero_id
                FROM marts.fact_movimiento_contable
                WHERE factura_id IN (SELECT factura_id FROM ids)
                GROUP BY 1
            )
            SELECT nc.numero AS nc_numero, nc.fecha AS fecha_nc,
                   to_char(nc.fecha, 'YYYY-MM') AS mes_emision,
                   m.metodo_enlace,
                   fa.numero AS factura_numero,
                   to_char(m.fecha_venta, 'YYYY-MM') AS mes_destino,
                   ROUND(m.proporcion::numeric, 4) AS proporcion,
                   t.nombre AS cliente,
                   ROUND(n.valor * m.proporcion / {MM}, 1) AS atribuido_mm,
                   nc.referencia AS nc_referencia
            FROM nc_tot n
            JOIN docs nc ON nc.factura_id = n.factura_id
            JOIN marts.map_nc_factura m ON m.nc_factura_id = n.factura_id
            JOIN docs fa ON fa.factura_id = m.factura_id
            LEFT JOIN marts.dim_tercero t ON t.tercero_id = nc.tercero_id
            WHERE ABS(n.valor * m.proporcion) > 1e6
              AND date_trunc('month', m.fecha_venta) <> date_trunc('month', nc.fecha)
            ORDER BY ABS(n.valor * m.proporcion) DESC LIMIT 40
        """)))
    else:
        print("Ningún mes supera el umbral: la reubicación de NC no mueve materialmente ningún mes.")

    # ── 4) Anulaciones totales que es_reverso no detecta ────────────────────────────────────────
    _titulo(4, "ANULACIONES QUE es_reverso NO DETECTA (Odoo dejó reversed_entry_id NULL) — inflan el BRUTO")
    print(_p(lo.consultar(f"""
        WITH nc AS (
          SELECT f.factura_id, MIN(f.numero) numero, MIN(f.fecha_factura) fecha_nc,
                 bool_or(f.reversed_factura_id IS NOT NULL) tiene_reversed,
                 SUM(f.venta_neta) val
          FROM marts.fact_movimiento_contable f JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
          WHERE c.clase_codigo = '4' AND f.tipo_movimiento = 'out_refund'
            AND f.es_reverso IS NOT TRUE
          GROUP BY 1),
        fa AS (
          SELECT f.factura_id, MIN(f.numero) numero, MIN(f.fecha_factura) fecha_fa, SUM(f.venta_neta) val
          FROM marts.fact_movimiento_contable f JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
          WHERE c.clase_codigo = '4' AND f.tipo_movimiento = 'out_invoice'
            AND f.es_reverso IS NOT TRUE
          GROUP BY 1)
        SELECT nc.numero AS nc_numero, nc.fecha_nc, fa.numero AS factura, fa.fecha_fa,
               ROUND(fa.val / {MM}, 1) AS factura_mm, ROUND(nc.val / {MM}, 1) AS nc_mm,
               ROUND((-nc.val / NULLIF(fa.val, 0))::numeric, 3) AS cobertura,
               nc.tiene_reversed,
               (date_trunc('month', nc.fecha_nc) <> date_trunc('month', fa.fecha_fa)) AS cruza_mes
        FROM marts.map_nc_factura m
        JOIN nc ON nc.factura_id = m.nc_factura_id
        JOIN fa ON fa.factura_id = m.factura_id
        WHERE m.proporcion > 0.999 AND fa.val > 0
          AND (-nc.val / fa.val) BETWEEN 0.99 AND 1.01
          AND nc.fecha_nc >= '{ANIO}-01-01' AND nc.fecha_nc < '{ANIO + 1}-01-01'
        ORDER BY fa.val DESC LIMIT 30
    """), vacio="(ninguna: todas las anulaciones totales están marcadas es_reverso)"))

    # ── 5) Notas débito como venta + riesgo latente del prorrateo ───────────────────────────────
    _titulo(5, f"NOTAS DÉBITO contadas como VENTA en {ANIO} (son out_invoice y pasan todos los filtros)")
    print(_p(lo.consultar(f"""
        SELECT mes_venta,
               ROUND(SUM(venta_subtotal) / {MM}, 1) AS nota_debito_mm,
               COUNT(DISTINCT factura_id)           AS docs
        FROM marts.v_ventas_producto
        WHERE anio_venta = {ANIO} AND es_nota_debito IS TRUE
        GROUP BY 1 ORDER BY 1
    """), vacio="(ninguna nota débito en ventas)"))
    print("\n   Neto con y sin notas débito (millones):")
    print(_p(lo.consultar(f"""
        SELECT mes_venta,
               ROUND(SUM(venta_subtotal) / {MM}, 1) AS neto_mm,
               ROUND(SUM(venta_subtotal) FILTER (WHERE es_nota_debito IS NOT TRUE) / {MM}, 1)
                                                    AS neto_sin_nd_mm
        FROM marts.v_ventas_producto
        WHERE anio_venta = {ANIO} GROUP BY 1 ORDER BY 1
    """)))
    print("\n   RIESGO LATENTE — NC con proporcion=1 pero CxC sin conciliar del todo (residual <> 0):")
    print("   (el prorrateo normaliza sobre lo conciliado contra facturas, así que el 100% del valor")
    print("    de la NC se atribuye a la factura que sí concilió, aunque solo cubriera una parte)")
    print(_p(lo.consultar(f"""
        WITH docs AS (   -- un solo scan: identidad + residual de CxC por documento
            SELECT factura_id, MIN(numero) AS numero, MIN(fecha_factura) AS fecha,
                   SUM(saldo_pendiente) FILTER (WHERE es_cxc IS TRUE) AS residual
            FROM marts.fact_movimiento_contable
            WHERE es_venta IS TRUE GROUP BY 1
        )
        SELECT nc.numero AS nc_numero, nc.fecha AS fecha_nc,
               ROUND(nc.residual / {MM}, 1) AS residual_cxc_mm,
               fa.numero AS factura_destino, fa.fecha AS fecha_dest,
               ROUND(m.proporcion::numeric, 4) AS proporcion, m.metodo_enlace
        FROM marts.map_nc_factura m
        JOIN docs nc ON nc.factura_id = m.nc_factura_id
        JOIN docs fa ON fa.factura_id = m.factura_id
        WHERE m.proporcion > 0.999 AND ABS(nc.residual) > 1e5
          AND nc.fecha >= '{ANIO}-01-01' AND nc.fecha < '{ANIO + 1}-01-01'
        ORDER BY ABS(nc.residual) DESC LIMIT 20
    """), vacio="(ninguna: todas las NC del puente están conciliadas por completo)"))

    print()
    print("=" * 115)
    print("Recordatorio: las diferencias de marzo/abril contra el Excel de CLEAN DATA son ESPERADAS.")
    print("El Excel descarta las NC que no casan por `ref`+producto; el DW las enlaza por conciliación.")
    print("Medir ventas por `fecha_venta`. Ver docs/guia_bi_ventas.md.")
    print("=" * 115)


if __name__ == "__main__":
    main()
