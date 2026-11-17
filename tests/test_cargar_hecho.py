"""
Pruebas EN MEMORIA de la carga del hecho contable (sin Odoo, sin Postgres).

    python -m unittest tests.test_cargar_hecho

Nacen del descuadre de la empresa 8 (2026-11-17): el rebuild del 2-nov se saltó pares de líneas
con ids consecutivos porque paginaba por `offset` sobre un conjunto que cambiaba mientras se leía,
y el incremental nunca borraba las líneas que Odoo quitaba al editar un asiento.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import etl_dw_marts as E  # noqa: E402


class OdooFalso:
    """`search_read` que entiende lo que usa el ETL: id >, move_id in, parent_state/state, offset."""

    def __init__(self, lineas, al_pedir=None, asientos=None):
        self.lineas = lineas            # {id: {"id", "move_id", "parent_state", ...}}
        self.asientos = asientos or {}  # {id: {"id", "state", "write_date"}}
        self.al_pedir = al_pedir        # callback(n_llamada) para cambiar el conjunto entre páginas
        self.llamadas = 0

    @staticmethod
    def _cumple(reg, dominio):
        for campo, op, valor in dominio:
            v = reg.get(campo)
            if isinstance(v, list):   # many2one [id, nombre]: Odoo compara por el id
                v = v[0]
            if op == ">" and not v > valor:
                return False
            if op == "=" and v != valor:
                return False
            if op == "!=" and v == valor:
                return False
            if op == "in" and v not in valor:
                return False
        return True

    def search_read(self, modelo, domain, fields, limit=None, offset=0, order="id asc",
                    context=None):
        if modelo == "account.move.line":
            self.llamadas += 1
            if self.al_pedir:
                self.al_pedir(self.llamadas)
            fuente = self.lineas
        else:
            fuente = self.asientos
        filas = sorted((r for r in fuente.values() if self._cumple(r, domain)),
                       key=lambda r: r["id"])
        filas = filas[offset:]
        if limit:
            filas = filas[:limit]
        return [dict(r) for r in filas]

    def read(self, modelo, ids, fields, chunk=500, context=None):
        return [{"id": i} for i in dict.fromkeys(ids)]


def _linea(i, move, estado="posted"):
    return {"id": i, "move_id": [move, f"M{move}"], "parent_state": estado,
            "write_date": f"2026-11-0{1 + i % 9} 10:00:00"}


class LoaderFalso:
    """Guarda cada sentencia y sus parámetros; `rowcount` fijo para contar."""

    def __init__(self):
        self.sentencias = []

    def get_connection(self):
        loader = self

        class _Cur:
            rowcount = 2

            def execute(self, sql, params=None):
                loader.sentencias.append((sql, params))

            def fetchall(self):
                return []

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def rollback(self):
                pass

        class _Cm:
            def __enter__(self):
                return _Conn()

            def __exit__(self, *a):
                return False

        return _Cm()


def _cargar(od, **kw):
    """`cargar_hecho` con todo lo que no es paginación neutralizado; devuelve los ids escritos."""
    escritos = []
    with mock.patch.object(E, "PAGINA", 4), \
            mock.patch.object(E, "cargar_dims_lote", lambda *a, **k: None), \
            mock.patch.object(E, "asegurar_dims_hecho", lambda *a, **k: None), \
            mock.patch.object(E, "construir_hecho", lambda lineas, *a, **k: lineas), \
            mock.patch.object(E, "upsert", lambda loader, df, *a, **k: escritos.extend(
                l["id"] for l in df)):
        E.cargar_hecho(od, LoaderFalso(), [["parent_state", "=", "posted"]],
                       None, None, None, None, None, **kw)
    return escritos


class TestPaginacionPorClave(unittest.TestCase):

    def test_no_se_salta_filas_si_el_conjunto_cambia_entre_paginas(self):
        """🔴 El caso del 2-nov: tras leer la 1ª página, un asiento YA LEÍDO sale de `posted`.

        Con `offset=4` la 2ª página empezaría dos filas más allá y las líneas 5 y 6 no se leerían
        nunca. Con `id > 4`, sí.
        """
        lineas = {i: _linea(i, move=(i + 1) // 2) for i in range(1, 13)}

        def despublicar_el_primer_asiento(n):
            if n == 2:   # justo antes de pedir la 2ª página
                for i in (1, 2):
                    lineas[i]["parent_state"] = "draft"

        escritos = _cargar(OdooFalso(lineas, al_pedir=despublicar_el_primer_asiento))
        self.assertEqual(sorted(set(escritos)), list(range(1, 13)))

    def test_CONTROL_sin_cambios_lee_todo_una_sola_vez(self):
        lineas = {i: _linea(i, move=i) for i in range(1, 11)}
        escritos = _cargar(OdooFalso(lineas))
        self.assertEqual(escritos, list(range(1, 11)))


class TestReconciliarAsientos(unittest.TestCase):

    def test_borra_del_hecho_solo_lo_que_odoo_ya_no_tiene(self):
        # El asiento 7 tiene hoy en Odoo las líneas 70 y 71 (la 72 se borró al editarlo).
        od = OdooFalso({70: _linea(70, 7), 71: _linea(71, 7)})
        loader = LoaderFalso()
        E.reconciliar_asientos(od, loader, [7, 7, None])
        sql, params = loader.sentencias[-1]
        self.assertIn("DELETE FROM marts.fact_movimiento_contable", sql)
        self.assertEqual(params, ([7], [70, 71]))

    def test_asiento_sin_lineas_publicadas_se_vacia_y_el_array_no_va_vacio(self):
        """`[]` iría a Postgres como `'{}'` sin tipo; con [-1] se borra todo el asiento igual."""
        od = OdooFalso({80: _linea(80, 8, estado="draft")})
        loader = LoaderFalso()
        E.reconciliar_asientos(od, loader, [8])
        self.assertEqual(loader.sentencias[-1][1], ([8], [-1]))

    def test_el_incremental_reconcilia_cada_lote(self):
        lineas = {i: _linea(i, move=100) for i in range(1, 4)}
        with mock.patch.object(E, "reconciliar_asientos", return_value=0) as rec:
            _cargar(OdooFalso(lineas), reconciliar=True)
        rec.assert_called_once()
        self.assertEqual(set(rec.call_args.args[2]), {100})

    def test_CONTROL_sin_reconciliar_no_se_borra_nada(self):
        lineas = {i: _linea(i, move=100) for i in range(1, 4)}
        with mock.patch.object(E, "reconciliar_asientos", return_value=0) as rec:
            _cargar(OdooFalso(lineas))
        rec.assert_not_called()


class TestAsientosNoPublicados(unittest.TestCase):

    def test_pasado_a_borrador_sale_del_hecho(self):
        asientos = {5: {"id": 5, "state": "draft", "write_date": "2026-11-16 12:00:00"},
                    6: {"id": 6, "state": "posted", "write_date": "2026-11-16 12:00:00"},
                    7: {"id": 7, "state": "cancel", "write_date": "2026-11-01 12:00:00"}}
        od = OdooFalso({}, asientos=asientos)
        with mock.patch.object(E, "reconciliar_asientos", return_value=3) as rec:
            n = E.quitar_asientos_no_publicados(od, LoaderFalso(), "2026-11-15 00:00:00")
        self.assertEqual(n, 3)
        self.assertEqual(rec.call_args.args[2], [5])   # ni el publicado ni el viejo

    def test_un_dia_antes_en_utc_y_sin_zona(self):
        self.assertEqual(E._un_dia_antes("2026-11-17 13:31:41+00:00"), "2026-11-16 13:31:41")
        self.assertEqual(E._un_dia_antes("2026-11-17 08:31:41-05:00"), "2026-11-16 13:31:41")
        self.assertEqual(E._un_dia_antes("2026-11-17 13:31:41"), "2026-11-16 13:31:41")


if __name__ == "__main__":
    unittest.main()
