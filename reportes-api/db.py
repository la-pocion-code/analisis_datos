"""
db.py — Conexión de solo lectura a Postgres `marts` + guardas para el tool SQL.

Usa el rol `agente_ro` (SELECT-only, ver sql/marts/20_agente.sql). Toda consulta ad-hoc
del agente pasa por `run_select()`, que rechaza cualquier cosa que no sea un único SELECT
sobre las vistas whitelisted y fuerza un LIMIT.
"""
from __future__ import annotations
import os
import re
import contextlib
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# Conexión RO: preferir credenciales del rol agente_ro; si no, caer a DB_* del repo.
_DSN = dict(
    host=os.getenv("RO_DB_HOST", os.getenv("DB_HOST")),
    port=os.getenv("RO_DB_PORT", os.getenv("DB_PORT")),
    dbname=os.getenv("RO_DB_NAME", os.getenv("DB_NAME")),
    user=os.getenv("RO_DB_USER", "agente_ro"),
    password=os.getenv("RO_DB_PASSWORD", os.getenv("DB_PASSWORD")),
    connect_timeout=10,
)

# Vistas que el tool SQL puede leer (defensa en profundidad; el rol RO además solo tiene GRANT aquí).
VISTAS_PERMITIDAS = {
    "marts.v_balance_comprobacion",
    "marts.v_ventas",
    "marts.v_ventas_producto",
}

MAX_LIMIT = 5000
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"merge|call|do|vacuum|analyze|comment|reindex|refresh)\b",
    re.IGNORECASE,
)


@contextlib.contextmanager
def get_conn():
    conn = psycopg2.connect(**_DSN)
    try:
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    finally:
        conn.close()


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Ejecuta una consulta parametrizada (para los endpoints curados)."""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or ())
        return [dict(r) for r in cur.fetchall()]


def run_select(sql: str) -> list[dict]:
    """
    Ejecuta un SELECT ad-hoc del agente con guardas:
    - una sola sentencia, debe empezar por SELECT o WITH
    - sin palabras clave de escritura/DDL
    - solo referencia vistas whitelisted
    - LIMIT forzado (<= MAX_LIMIT)
    """
    limpio = sql.strip().rstrip(";").strip()
    if ";" in limpio:
        raise ValueError("Solo se permite una sentencia.")
    if not re.match(r"^(select|with)\b", limpio, re.IGNORECASE):
        raise ValueError("Solo se permiten consultas SELECT.")
    if _FORBIDDEN.search(limpio):
        raise ValueError("La consulta contiene operaciones no permitidas (solo lectura).")

    # Debe tocar únicamente vistas permitidas.
    referencias = set(re.findall(r"\bmarts\.\w+", limpio, re.IGNORECASE))
    no_permitidas = {r for r in referencias if r.lower() not in {v.lower() for v in VISTAS_PERMITIDAS}}
    if no_permitidas:
        raise ValueError(f"Solo se permiten estas vistas: {sorted(VISTAS_PERMITIDAS)}. "
                         f"No permitidas: {sorted(no_permitidas)}")
    if not referencias:
        raise ValueError("La consulta debe leer una vista de marts permitida.")

    if not re.search(r"\blimit\b", limpio, re.IGNORECASE):
        limpio += f" LIMIT {MAX_LIMIT}"

    return run_query(limpio)
