"""
run_dw.py — Entrypoint del CRON de Railway (ETL del data warehouse `marts`).

Es el cron del proyecto (Procfile + railway.toml → `python run_dw.py`, cronSchedule "*/15 * * * *").
Reemplaza al antiguo sync raw `etl_odoo_incremental.py` (archivado en archivado/). El DW lee de
Odoo directo por XML-RPC, no de `raw`. En cada disparo decide qué correr según la fecha/hora:

- Cada 15 min (ticks :15/:30/:45):  carga incremental LIGERA (dimensiones por write_date + hecho
                                    nuevo). Salta los pasos de coste FIJO.
- Tick de la hora en punto (:00):   corrida COMPLETA (además: kits, nombre comercial y todos los
                                    pasos de cierre — reversos, puentes NC/ND, categoría, PUC).
- Siempre, al final:                REFRESCO de las vistas materializadas de los dashboards.
- Días 3 y 24, 03h (tick :00):      además RECREACIÓN del año actual (--rebuild) → refleja borrados.

Por qué el reparto ligero/completo: de los ~6,5 min de una corrida incremental, casi todo es coste
FIJO e independiente del delta (full scans del hecho en marcar_reversos / marcar_reversos_puente /
consolidar_categoria / canonicalizar_puc, TRUNCATE+rebuild de los puentes NC/ND y de
dim_kit_componente, full scan de product.product en Odoo). Repetirlo 4 veces por hora no trae ni una
fila más. ⚠ El precio: en los ticks ligeros las líneas nuevas quedan sin `categoria`, sin
`es_reverso` y sin puente NC/ND resuelto hasta el cierre de la hora.

⚠ DOS GUARDAS que hacen seguro el `*/15`:
  1. `MINUTO_CIERRE`: el rebuild y el cierre solo disparan en el primer tick de la hora. Sin esto,
     `hour == HORA_REBUILD` se cumpliría en :00, :15, :30 y :45 → 4 rebuilds solapados del año
     actual, cada uno arrancando con un DELETE del año. Es corrupción, no solo coste.
  2. ADVISORY LOCK de Postgres: si la corrida anterior sigue viva, este tick se omite y sale con 0.
     Los pasos de cierre reconstruyen tablas que otra corrida lee; solaparlas deja los puentes
     NC/ND incompletos.

⚠ La hora es la del contenedor: en Railway es **UTC**, no Colombia. O sea que la ventana de rebuild
"días 3 y 24 a las 03h" cae en la práctica a las 22:00 del día anterior en hora de Colombia.

Variables de entorno requeridas (las mismas del proyecto): url, db, username_odoo, password,
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
"""
import logging
from datetime import datetime

import etl_dw_marts as etl
import refrescar_mv_dashboards as mvd
from classes.db_loader import DBLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Días del mes y hora (UTC) en que además se recrea el año actual (~1 semana antes de fin de mes
# y unos días después de iniciar el mes).
DIAS_REBUILD = {3, 24}
HORA_REBUILD = 3
# El cron corre cada 15 min: solo el primer tick de la hora hace el cierre y el rebuild.
MINUTO_CIERRE = 15
# Clave del advisory lock (arbitraria pero fija: identifica "el ETL del DW").
LOCK_KEY = 815_2026


def main():
    ahora = datetime.now()
    # Corrida completa una vez por hora; los otros 3 ticks son ligeros.
    completa = ahora.minute < MINUTO_CIERRE
    logging.info(f"run_dw disparado: {ahora:%Y-%m-%d %H:%M} "
                 f"({'COMPLETA' if completa else 'ligera'})")

    # 1) Incremental (incluye refresco de dimensiones por write_date).
    try:
        etl.main("incremental", None, cierre=completa)
    except Exception:
        logging.exception("Fallo en la corrida incremental")

    # 2) Recreación del año actual en los días/hora programados. Solo en el tick de la hora:
    #    con `*/15` la condición de hora se cumpliría 4 veces y el rebuild dura mucho más de 15 min.
    if completa and ahora.day in DIAS_REBUILD and ahora.hour == HORA_REBUILD:
        logging.info("Ventana de recreación: ejecutando --rebuild (año actual).")
        try:
            etl.main("rebuild", None)
        except Exception:
            logging.exception("Fallo en la recreación (rebuild)")

    # 3) Refresco de las vistas materializadas que leen los dashboards de la
    #    intranet. Va AL FINAL para que recoja también lo que trajo el rebuild.
    #    Envuelto en try/except: si un tablero no se puede refrescar, el ETL
    #    igual terminó bien (y el fallo queda en marts.bi_mv_refresh).
    #    `completa` decide si se refrescan también las MV de CONTABILIDAD: son de
    #    grano mensual y en los ticks ligeros las líneas nuevas aún no tienen
    #    `categoria`, así que refrescarlas cada 15 min no aporta y sí ensucia.
    try:
        mvd.refrescar(completa=completa)
    except Exception:
        logging.exception("Fallo al refrescar las MV de dashboards")


def _con_lock():
    """Corre main() solo si no hay otra corrida activa. El lock de sesión se libera solo al cerrar
    la conexión, así que basta con mantenerla abierta durante toda la corrida."""
    with DBLoader().get_connection() as conn:
        conn.autocommit = True          # el lock no debe quedar atrapado en una transacción abierta
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        if not cur.fetchone()[0]:
            logging.warning("Corrida anterior aún activa: se omite este tick.")
            return
        try:
            main()
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


if __name__ == "__main__":
    _con_lock()
