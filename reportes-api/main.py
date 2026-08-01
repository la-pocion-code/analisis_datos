"""
main.py — reportes-api: microservicio de reportes financieros para el agente (n8n → Claude).

Expone herramientas curadas (deterministas, calzan con el BI) y un tool SQL SELECT-only.
Autenticación: header X-API-Key (n8n↔API) + whitelist de identidad (agente.usuarios_autorizados).
Toda consulta queda auditada en agente.log_consultas.

Arranque local:  uvicorn main:app --reload --port 8000
Railway (start):  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import reports
from db import run_select
from security import check_api_key, autorizar, log_consulta

app = FastAPI(title="La Poción · reportes-api", version="0.1.0")


class Ctx(BaseModel):
    canal: str            # 'google_chat' | 'whatsapp' | 'gmail'
    identidad: str        # email o teléfono E.164


class ReporteReq(Ctx):
    empresa_id: int
    anio: int
    mes: int | None = None
    top: int | None = 10


class SqlReq(Ctx):
    sql: str
    pregunta: str | None = None


def _auth(api_key: str | None, ctx: Ctx):
    if not check_api_key(api_key):
        raise HTTPException(401, "API key inválida")
    perfil = autorizar(ctx.canal, ctx.identidad)
    if not perfil:
        log_consulta(ctx.canal, ctx.identidad, False, None, None, ctx.model_dump(), False, "no autorizado")
        raise HTTPException(403, "Identidad no autorizada")
    return perfil


def _check_empresa(perfil: dict, empresa_id: int):
    if empresa_id not in (perfil.get("empresas") or []):
        raise HTTPException(403, f"Sin acceso a la empresa {empresa_id}")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/estado-resultados")
def estado_resultados(req: ReporteReq, x_api_key: str | None = Header(None)):
    perfil = _auth(x_api_key, req)
    _check_empresa(perfil, req.empresa_id)
    try:
        data = reports.estado_resultados(req.empresa_id, req.anio, req.mes)
        log_consulta(req.canal, req.identidad, True, None, "estado_resultados", req.model_dump(), True)
        return {"data": data}
    except Exception as e:
        log_consulta(req.canal, req.identidad, True, None, "estado_resultados", req.model_dump(), False, str(e))
        raise HTTPException(500, str(e))


@app.post("/balance")
def balance(req: ReporteReq, x_api_key: str | None = Header(None)):
    perfil = _auth(x_api_key, req)
    _check_empresa(perfil, req.empresa_id)
    if not req.mes:
        raise HTTPException(422, "El balance requiere 'mes' (es acumulado a fin de ese mes).")
    data = reports.balance(req.empresa_id, req.anio, req.mes)
    log_consulta(req.canal, req.identidad, True, None, "balance", req.model_dump(), True)
    return {"data": data}


@app.post("/top-clientes")
def top_clientes(req: ReporteReq, x_api_key: str | None = Header(None)):
    perfil = _auth(x_api_key, req)
    _check_empresa(perfil, req.empresa_id)
    data = reports.top_clientes(req.empresa_id, req.anio, req.mes, req.top or 10)
    log_consulta(req.canal, req.identidad, True, None, "top_clientes", req.model_dump(), True)
    return {"data": data}


@app.post("/ventas-categoria")
def ventas_categoria(req: ReporteReq, x_api_key: str | None = Header(None)):
    perfil = _auth(x_api_key, req)
    _check_empresa(perfil, req.empresa_id)
    data = reports.ventas_por_categoria(req.empresa_id, req.anio, req.mes)
    log_consulta(req.canal, req.identidad, True, None, "ventas_categoria", req.model_dump(), True)
    return {"data": data}


@app.post("/query")
def query(req: SqlReq, x_api_key: str | None = Header(None)):
    """Tool SQL ad-hoc SELECT-only (solo vistas whitelisted)."""
    perfil = _auth(x_api_key, req)
    try:
        data = run_select(req.sql)
        log_consulta(req.canal, req.identidad, True, req.pregunta, "query",
                     {"sql": req.sql}, True)
        return {"data": data, "rows": len(data)}
    except ValueError as e:  # rechazo por guardas (no es error de servidor)
        log_consulta(req.canal, req.identidad, True, req.pregunta, "query", {"sql": req.sql}, False, str(e))
        raise HTTPException(400, str(e))
    except Exception as e:
        log_consulta(req.canal, req.identidad, True, req.pregunta, "query", {"sql": req.sql}, False, str(e))
        raise HTTPException(500, str(e))


# TODO(F1): endpoints /pdf y /excel que arman el board-style reusando el motor del repo raíz
# (classes/clase_reportes_new.py, classes/send_mail.py, pymupdf/matplotlib) y devuelven un link de
# Google Drive (classes/drive_loader.py) o el archivo. Ver docs/agente_runbook.md.
