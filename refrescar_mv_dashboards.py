"""
refrescar_mv_dashboards.py — Refresca las vistas materializadas que consumen los
dashboards de la INTRANET (app `apps/dashboards` del repo `proyecto pocion/intranet`).

DDL de esas vistas: sql/marts/23_mv_dashboards.sql. Contrato de datos y detalle
de cada vista: docs/dashboards_intranet.md.

Se llama desde run_dw.py después de la carga incremental (cron horario de
Railway) y también se puede correr a mano:

    python refrescar_mv_dashboards.py                 # todas
    python refrescar_mv_dashboards.py --mv mv_ventas_dia
    python refrescar_mv_dashboards.py --no-concurrente  # si alguna nunca se pobló

Cada refresco se registra en `marts.bi_mv_refresh` (la intranet lo lee para
invalidar su caché y mostrar "datos actualizados hace X").

⚠ DOS DETALLES QUE IMPORTAN
1. `REFRESH MATERIALIZED VIEW CONCURRENTLY` **no puede correr dentro de una
   transacción** → la conexión va en autocommit. CONCURRENTLY es lo que permite
   que la intranet siga leyendo mientras se refresca (sin él, la vista queda
   bloqueada varios segundos y los tableros se congelan).
2. CONCURRENTLY exige que la vista **ya esté poblada** y tenga un índice ÚNICO.
   Si falla por eso, se reintenta sin CONCURRENTLY automáticamente.
"""
import argparse
import logging
import time

from classes.db_loader import DBLoader

# Orden de refresco. Las 4 primeras son independientes entre sí (leen de v_ventas_bi
# o de bi_presupuesto) y van de la más barata a la más cara, para que un fallo tardío
# no deje todo sin refrescar.
# ⚠ `mv_ventas_presupuesto_mes` va SIEMPRE AL FINAL: lee de mv_ventas_mes y de
# mv_presupuesto_mes, así que necesita que ambas estén ya refrescadas o mostraría el
# cruce contra datos viejos.
MVS = (
    "mv_presupuesto_mes",
    "mv_ventas_kpi_mes",
    "mv_ventas_dia",
    "mv_ventas_mes",
    "mv_ventas_presupuesto_mes",
)


def _registrar(cur, mv: str, filas, duracion_ms: int, ok: bool, error: str | None) -> None:
    """Deja constancia en marts.bi_mv_refresh (upsert por mv_name)."""
    cur.execute(
        """
        INSERT INTO marts.bi_mv_refresh (mv_name, refreshed_at, filas, duracion_ms, ok, error)
        VALUES (%s, now(), %s, %s, %s, %s)
        ON CONFLICT (mv_name) DO UPDATE
           SET refreshed_at = now(),
               filas        = EXCLUDED.filas,
               duracion_ms  = EXCLUDED.duracion_ms,
               ok           = EXCLUDED.ok,
               error        = EXCLUDED.error
        """,
        (mv, filas, duracion_ms, ok, (error or "")[:2000] or None),
    )


def refrescar(mvs=MVS, concurrente: bool = True) -> dict:
    """
    Refresca las MV indicadas. Un fallo en una NO detiene las demás: el cron del
    ETL nunca debe caerse porque un tablero no se pudo refrescar.

    Devuelve {'ok': [...], 'fallidas': [(mv, error), ...]}.
    """
    resultado = {"ok": [], "fallidas": []}

    with DBLoader().get_connection() as conn:
        # REFRESH ... CONCURRENTLY no admite transacción.
        conn.autocommit = True
        cur = conn.cursor()

        for mv in mvs:
            t0 = time.perf_counter()
            modo = "CONCURRENTLY " if concurrente else ""
            try:
                try:
                    cur.execute(f"REFRESH MATERIALIZED VIEW {modo}marts.{mv}")
                except Exception as exc:
                    # Nunca poblada / sin índice único → reintento sin CONCURRENTLY.
                    if not concurrente:
                        raise
                    logging.warning(
                        "%s: CONCURRENTLY falló (%s). Reintentando sin CONCURRENTLY.",
                        mv, str(exc).splitlines()[0],
                    )
                    cur.execute(f"REFRESH MATERIALIZED VIEW marts.{mv}")

                dur_ms = int((time.perf_counter() - t0) * 1000)
                cur.execute(f"SELECT count(*) FROM marts.{mv}")
                filas = cur.fetchone()[0]

                _registrar(cur, mv, filas, dur_ms, True, None)
                resultado["ok"].append(mv)
                logging.info("MV %s refrescada: %s filas en %.1f s", mv, f"{filas:,}", dur_ms / 1000)

            except Exception as exc:
                dur_ms = int((time.perf_counter() - t0) * 1000)
                msg = str(exc).splitlines()[0]
                resultado["fallidas"].append((mv, msg))
                logging.exception("Fallo al refrescar la MV %s", mv)
                # El registro del fallo también es best-effort.
                try:
                    _registrar(cur, mv, None, dur_ms, False, msg)
                except Exception:
                    logging.warning("No se pudo registrar el fallo de %s en bi_mv_refresh", mv)

        cur.close()

    logging.info(
        "Refresco de MV terminado: %s ok, %s fallidas%s",
        len(resultado["ok"]),
        len(resultado["fallidas"]),
        f" ({', '.join(mv for mv, _ in resultado['fallidas'])})" if resultado["fallidas"] else "",
    )
    return resultado


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresca las MV de los dashboards de la intranet.")
    ap.add_argument("--mv", action="append", choices=list(MVS),
                    help="Refrescar solo esta MV (repetible). Por defecto: todas.")
    ap.add_argument("--no-concurrente", action="store_true",
                    help="Usar REFRESH sin CONCURRENTLY (bloquea lecturas; solo para la 1.ª carga).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    refrescar(tuple(args.mv) if args.mv else MVS, concurrente=not args.no_concurrente)


if __name__ == "__main__":
    main()
