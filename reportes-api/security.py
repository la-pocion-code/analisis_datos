"""
security.py — Autorización del agente.

Dos capas:
1. API key entre n8n y el microservicio (header X-API-Key).
2. Whitelist de identidad (email/teléfono) contra agente.usuarios_autorizados; devuelve el
   rol y las empresas permitidas. Toda consulta se registra en agente.log_consultas.
"""
from __future__ import annotations
import os
import json
from dotenv import load_dotenv
from db import get_conn

load_dotenv()
API_KEY = os.getenv("REPORTES_API_KEY", "")


def check_api_key(provided: str | None) -> bool:
    return bool(API_KEY) and provided == API_KEY


def autorizar(canal: str, identidad: str) -> dict | None:
    """Devuelve {rol, empresas} si la identidad está autorizada y activa; None si no."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT rol, empresas FROM agente.usuarios_autorizados
               WHERE canal = %s AND lower(identidad) = lower(%s) AND activo IS TRUE""",
            (canal, identidad),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"rol": row[0], "empresas": row[1]}


def log_consulta(canal, identidad, autorizado, pregunta, herramienta, params, ok, error=None):
    try:
        with get_conn() as conn:
            conn.set_session(readonly=False, autocommit=True)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agente.log_consultas
                       (canal, identidad, autorizado, pregunta, herramienta, params, ok, error)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (canal, identidad, autorizado, pregunta, herramienta,
                     json.dumps(params, ensure_ascii=False, default=str), ok, error),
                )
    except Exception:
        pass  # la auditoría nunca debe tumbar la respuesta
