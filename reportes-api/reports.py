"""
reports.py — Reportes curados sobre las vistas del DW (cifras deterministas = calzan con el BI).

Fuentes:
  * marts.v_balance_comprobacion : clasificación oficial (seccion/concepto/nivel_movimiento) + saldo
    por empresa × cuenta × mes. Base del Estado de Resultados y el Balance.
  * marts.v_ventas_producto      : ventas netas a grano de producto (cliente, categoría, empresa, mes).

Signo de presentación (P&G / Balance): clase 4 (ingresos) y clases 2/3 (pasivo/patrimonio) son de
naturaleza crédito → se muestran como -saldo; clases 1/5/6/7 (activo/costo/gasto) como +saldo.
"""
from __future__ import annotations
from db import run_query

# clase → factor de presentación (para mostrar en positivo lo que corresponde)
_SIGNO = "CASE WHEN c_clase IN ('4','2','3') THEN -1 ELSE 1 END"


def estado_resultados(empresa_id: int, anio: int, mes: int | None = None) -> list[dict]:
    """P&G del periodo (mes concreto o año completo si mes=None), por línea del reporte."""
    filtro_mes = "AND mes = %(mes)s" if mes else ""
    sql = f"""
        SELECT seccion, concepto, nivel_movimiento,
               SUM((CASE WHEN clase_codigo IN ('4','2','3') THEN -1 ELSE 1 END) * saldo) AS valor
        FROM marts.v_balance_comprobacion
        WHERE empresa_id = %(empresa)s AND anio = %(anio)s {filtro_mes}
          AND clase_codigo IN ('4','5','6','7')
        GROUP BY seccion, concepto, nivel_movimiento
        ORDER BY seccion, concepto, nivel_movimiento
    """
    return run_query(sql, {"empresa": empresa_id, "anio": anio, "mes": mes})


def balance(empresa_id: int, anio: int, mes: int) -> list[dict]:
    """Balance (acumulado a fin del mes): clases 1/2/3, saldo acumulado hasta el periodo."""
    sql = f"""
        SELECT seccion, concepto, nivel_movimiento,
               SUM((CASE WHEN clase_codigo IN ('4','2','3') THEN -1 ELSE 1 END) * saldo) AS valor
        FROM marts.v_balance_comprobacion
        WHERE empresa_id = %(empresa)s
          AND (anio < %(anio)s OR (anio = %(anio)s AND mes <= %(mes)s))
          AND clase_codigo IN ('1','2','3')
        GROUP BY seccion, concepto, nivel_movimiento
        ORDER BY seccion, concepto, nivel_movimiento
    """
    return run_query(sql, {"empresa": empresa_id, "anio": anio, "mes": mes})


def top_clientes(empresa_id: int, anio: int, mes: int | None = None, top: int = 10) -> list[dict]:
    filtro_mes = "AND mes = %(mes)s" if mes else ""
    sql = f"""
        SELECT cliente, SUM(venta_subtotal) AS ventas
        FROM marts.v_ventas_producto
        WHERE empresa_id = %(empresa)s AND anio = %(anio)s {filtro_mes}
        GROUP BY cliente
        ORDER BY ventas DESC
        LIMIT %(top)s
    """
    return run_query(sql, {"empresa": empresa_id, "anio": anio, "mes": mes, "top": top})


def ventas_por_categoria(empresa_id: int, anio: int, mes: int | None = None) -> list[dict]:
    """Ventas por categoría de cliente (proxy de 'canal' hasta exponer fact.canal en la vista)."""
    filtro_mes = "AND mes = %(mes)s" if mes else ""
    sql = f"""
        SELECT categoria, SUM(venta_subtotal) AS ventas
        FROM marts.v_ventas_producto
        WHERE empresa_id = %(empresa)s AND anio = %(anio)s {filtro_mes}
        GROUP BY categoria
        ORDER BY ventas DESC
    """
    return run_query(sql, {"empresa": empresa_id, "anio": anio, "mes": mes})


def cuentas_clave(empresa_id: int, anio: int, mes: int | None = None, top: int = 6) -> list[dict]:
    """Ingresos y utilidad bruta por cliente clave (UB = ingreso − costo por tercero).
    NOTA: requiere una vista que cruce ingreso y costo por tercero; hoy v_ventas_producto solo trae
    ingreso. Devuelve ingresos por cliente; el costo/UB por tercero se agrega en una vista futura
    (ver docs/agente_runbook.md → pendientes)."""
    return top_clientes(empresa_id, anio, mes, top)
