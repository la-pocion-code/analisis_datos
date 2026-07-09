"""
run_dw.py — Entrypoint del CRON de Railway (ETL del data warehouse `marts`).

Es el cron del proyecto (Procfile + railway.toml → `python run_dw.py`, cronSchedule "0 * * * *").
Reemplaza al antiguo sync raw `etl_odoo_incremental.py` (archivado en archivado/). El DW lee de
Odoo directo por XML-RPC, no de `raw`. En cada disparo decide qué correr según la fecha/hora:

- Siempre:            carga INCREMENTAL (hecho + cartera + dimensiones por write_date).
- Días 3 y 24, 03h:   además RECREACIÓN del año actual (--rebuild) → refleja borrados.

El cron es HORARIO a propósito: el rebuild se decide por `hour==3`; con `*/15` se dispararía varias
veces en la ventana 03:00–03:45.

Variables de entorno requeridas (las mismas del proyecto): url, db, username_odoo, password,
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
"""
import logging
from datetime import datetime
import etl_dw_marts as etl

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Días del mes y hora en que además se recrea el año actual (~1 semana antes de fin de mes
# y unos días después de iniciar el mes).
DIAS_REBUILD = {3, 24}
HORA_REBUILD = 3


def main():
    ahora = datetime.now()
    logging.info(f"run_dw disparado: {ahora:%Y-%m-%d %H:%M}")

    # 1) Siempre: incremental (incluye refresco de dimensiones por write_date).
    try:
        etl.main("incremental", None)
    except Exception:
        logging.exception("Fallo en la corrida incremental")

    # 2) Recreación del año actual en los días/hora programados.
    if ahora.day in DIAS_REBUILD and ahora.hour == HORA_REBUILD:
        logging.info("Ventana de recreación: ejecutando --rebuild (año actual).")
        try:
            etl.main("rebuild", None)
        except Exception:
            logging.exception("Fallo en la recreación (rebuild)")


if __name__ == "__main__":
    main()
