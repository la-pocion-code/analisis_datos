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

Por qué el reparto ligero/completo: el coste de una corrida es casi todo FIJO e independiente del
delta (full scans del hecho en marcar_reversos / marcar_reversos_puente / consolidar_categoria /
canonicalizar_puc, TRUNCATE+rebuild de los puentes NC/ND y de dim_kit_componente, full scan de
product.product en Odoo). Repetirlo 4 veces por hora no trae ni una fila más: lo que se evita es
**4× el tráfico XML-RPC a Odoo y 4× los full scans del hecho**, no tiempo de reloj.
⚠ Medido EN RAILWAY (2026-07-29): una corrida completa tarda **~26 s** (22-30 s en 6 corridas). Las
cifras de minutos que se ven en `db_loader.log` son de una máquina local, donde la latencia a Odoo y
a Postgres domina; no sirven para dimensionar el cron.
⚠ El precio del reparto: en los ticks ligeros las líneas nuevas quedan sin `categoria`, sin
`es_reverso` y sin puente NC/ND resuelto hasta el cierre de la hora.

⚠ DOS GUARDAS que hacen seguro el `*/15`:
  1. El cierre y el rebuild corren UNA VEZ POR HORA, y se decide por **estado en la BD**
     (`marts.etl_control`, clave `cierre_dw`), no por el minuto del reloj: el cron de Railway se
     retrasa 0-4 min (medido: ticks a :00, :01, :02, :03), así que una guarda tipo `minute < 15`
     depende de que la deriva no se coma la ventana — si un tick de :00 arrancara pasado el minuto 15,
     esa hora perdería el cierre **en silencio**. Con el estado, la deriva deja de importar y el
     rebuild de los días 3/24 tampoco puede duplicarse.
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
# Clave con la que se registra en marts.etl_control que el CIERRE ya corrió en la hora en curso.
MODELO_CIERRE = "cierre_dw"
# Clave del advisory lock (arbitraria pero fija: identifica "el ETL del DW").
LOCK_KEY = 815_2026


def _toca_cierre(conn) -> bool:
    """¿Este tick debe ser la corrida COMPLETA? Sí cuando el cierre todavía no ha corrido en la hora
    en curso. Se decide por ESTADO (marts.etl_control) y con el reloj de la BD, no con el del
    contenedor ni con el minuto del tick: así da igual que el cron de Railway se retrase."""
    cur = conn.cursor()
    cur.execute("""
        SELECT NOT EXISTS (
            SELECT 1 FROM marts.etl_control
            WHERE modelo = %s
              AND date_trunc('hour', actualizado) >= date_trunc('hour', now())
        )
    """, (MODELO_CIERRE,))
    return bool(cur.fetchone()[0])


def _marcar_cierre(conn) -> None:
    """Deja constancia de que el cierre de esta hora ya se hizo. Se llama SOLO si terminó bien: si
    falla, el siguiente tick lo reintenta en vez de dejar la hora sin consolidar."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO marts.etl_control (modelo, actualizado) VALUES (%s, now())
        ON CONFLICT (modelo) DO UPDATE SET actualizado = now()
    """, (MODELO_CIERRE,))


def main(conn):
    ahora = datetime.now()
    completa = _toca_cierre(conn)
    logging.info(f"run_dw disparado: {ahora:%Y-%m-%d %H:%M} "
                 f"({'COMPLETA' if completa else 'ligera'})")

    # 1) Incremental (incluye refresco de dimensiones por write_date).
    ok = True
    try:
        etl.main("incremental", None, cierre=completa)
    except Exception:
        ok = False
        logging.exception("Fallo en la corrida incremental")

    # 2) Recreación del año actual en los días/hora programados. Va dentro del tick de cierre para
    #    que no pueda dispararse cuatro veces en la ventana 03:00-03:45 (cada rebuild arranca con un
    #    DELETE del año en curso; solaparlos es corrupción, no solo coste).
    if completa and ahora.day in DIAS_REBUILD and ahora.hour == HORA_REBUILD:
        logging.info("Ventana de recreación: ejecutando --rebuild (año actual).")
        try:
            etl.main("rebuild", None)
        except Exception:
            ok = False
            logging.exception("Fallo en la recreación (rebuild)")

    if completa and ok:
        _marcar_cierre(conn)

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
            main(conn)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


if __name__ == "__main__":
    _con_lock()
