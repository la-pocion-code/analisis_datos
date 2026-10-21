"""
respaldo_local.py — Respaldo del cron del ETL desde un PC de la oficina.

El ETL del almacén lo dispara Railway cada 15 min (`run_dw.py`). El 2026-10-21 Railway tuvo una
caída («Dashboard and API unavailable, builds paused») y **no disparó el cron** en dos tramos
(07:05–09:30 y 12:32–16:30 UTC): ventas llegó a tener 4 h de atraso, sin un solo error en ningún
log. Este script es el plan B: una tarea programada de Windows lo corre cada 30 min y, **solo si
el almacén está atrasado**, ejecuta la misma corrida que Railway.

Cómo decide:
  1. Lee cuándo se refrescó por última vez `mv_ventas_mes` (`marts.bi_mv_refresh`). Es la MV que
     mira la gente: se refresca en cada tick, así que es la señal más sensible.
  2. Si tiene menos de `MINUTOS_ATRASO` (25), Railway está al día → no hace nada y lo dice.
  3. Si está atrasada, corre `run_dw.main()` bajo el MISMO advisory lock que Railway
     (`run_dw.LOCK_KEY`): si Railway está corriendo en ese momento, este se omite. Nunca se pisan.

⚠⚠ El rebuild del año (`run_dw.DIAS_REBUILD`) se APAGA aquí. Esa ventana se decide con el reloj
de la máquina: Railway va en UTC y este PC en hora de Bogotá, así que aquí caería a otra hora y
podría coincidir con la de Railway del día siguiente. El rebuild es un DELETE del año: no se
arriesga desde un respaldo. El cierre horario sí corre, porque lo decide el estado de la BD.

Deja rastro en dos sitios:
  - `logs/respaldo_local.log`: una línea por corrida (también cuando no hace nada).
  - `marts.etl_control`, modelo `respaldo_local`: la hora de la última corrida REAL desde este PC
    y cuántas lleva. Así, mirando la base, se sabe si una corrida salió de aquí y no de Railway.

Uso:
    python respaldo_local.py            # lo que corre la tarea programada
    python respaldo_local.py --mirar    # solo dice si haría falta; no ejecuta nada
    python respaldo_local.py --forzar   # corre aunque el almacén esté al día

La tarea se instala con `respaldo_local.cmd` (ver su cabecera).
"""
import argparse
import logging
import os
import socket
import sys
from datetime import datetime

# Directorio del repo: el .env y los logs se resuelven contra él, venga de donde venga la tarea.
RAIZ = os.path.dirname(os.path.abspath(__file__))
os.chdir(RAIZ)

MV_SENAL = "mv_ventas_mes"
MINUTOS_ATRASO = 25
MODELO_RASTRO = "respaldo_local"
LOG = os.path.join(RAIZ, "logs", "respaldo_local.log")


def _log(texto: str) -> None:
    """Una línea en el log propio y en la consola. Solo ASCII en la consola: la de Windows es
    cp1252 y un carácter fuera de ella abortaría el script justo el día que hace falta."""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{socket.gethostname()}] {texto}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")
    print(linea.encode("ascii", "replace").decode("ascii"))


def _minutos_desde_refresco(conn):
    """Minutos desde el último refresco OK de la MV señal, con el reloj de la BD (no el del PC)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT EXTRACT(EPOCH FROM (now() - refreshed_at)) / 60.0, ok
        FROM marts.bi_mv_refresh WHERE mv_name = %s
    """, (MV_SENAL,))
    fila = cur.fetchone()
    if fila is None:
        return None, None
    return float(fila[0]), fila[1]


def _dejar_rastro(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO marts.etl_control (modelo, ultimo_write, filas, actualizado)
        VALUES (%s, now(), 1, now())
        ON CONFLICT (modelo) DO UPDATE
           SET ultimo_write = now(), filas = COALESCE(marts.etl_control.filas, 0) + 1,
               actualizado = now()
    """, (MODELO_RASTRO,))


def main(mirar: bool = False, forzar: bool = False) -> int:
    import run_dw                       # importa el ETL: se hace aquí, después del chdir
    from classes.db_loader import DBLoader

    run_dw.DIAS_REBUILD = set()         # ver cabecera: el rebuild nunca sale de este PC

    with DBLoader().get_connection() as conn:
        conn.autocommit = True          # el lock no debe quedar atrapado en una transacción
        minutos, ok = _minutos_desde_refresco(conn)
        estado = ("sin registro" if minutos is None
                  else f"{MV_SENAL} refrescada hace {minutos:.0f} min" + ("" if ok else " (con ERROR)"))

        atrasada = minutos is None or minutos >= MINUTOS_ATRASO or not ok
        if not atrasada and not forzar:
            _log(f"AL DIA: {estado}. Railway esta corriendo; no se hace nada.")
            return 0
        if mirar:
            _log(f"HARIA FALTA: {estado}." if atrasada else f"AL DIA: {estado} (--mirar).")
            return 0

        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (run_dw.LOCK_KEY,))
        if not cur.fetchone()[0]:
            _log(f"OMITIDO: {estado}, pero hay una corrida activa (Railway u otro PC) con el lock.")
            return 0
        try:
            _log(f"CORRIENDO: {estado}" + (" (--forzar)" if forzar and not atrasada else "") + ".")
            inicio = datetime.now()
            run_dw.main(conn)
            _dejar_rastro(conn)
            despues, ok2 = _minutos_desde_refresco(conn)
            _log(f"TERMINADO en {(datetime.now() - inicio).total_seconds():.0f} s; "
                 f"{MV_SENAL} ahora con {despues:.0f} min" + ("" if ok2 else " (con ERROR)") + ".")
        except Exception as exc:        # la tarea programada no tiene consola: el motivo va al log
            logging.exception("Fallo el respaldo local")
            _log(f"FALLO: {type(exc).__name__}: {exc}")
            return 1
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (run_dw.LOCK_KEY,))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--mirar", action="store_true", help="solo dice si haria falta; no ejecuta")
    p.add_argument("--forzar", action="store_true", help="corre aunque el almacen este al dia")
    a = p.parse_args()
    sys.exit(main(mirar=a.mirar, forzar=a.forzar))
