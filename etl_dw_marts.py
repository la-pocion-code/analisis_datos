"""
etl_dw_marts.py — Poblado del modelo estrella (esquema marts) desde Odoo.

Aditivo: NO toca el cron (etl_odoo_incremental.py) ni raw.odoo_apuntes. Reutiliza el patrón
de extracción de Odoo y la clase DBLoader. Requiere el DDL de sql/marts/ ya aplicado
(01_star_schema.sql, 02_vistas.sql, 03_control.sql).

Modos:
    python etl_dw_marts.py --full           # carga inicial histórica completa (por lotes, sin truncar)
    python etl_dw_marts.py --incremental    # solo cambios (write_date > marca de agua)  [por defecto]
    python etl_dw_marts.py --rebuild        # recreación total: TRUNCATE + recarga (refleja borrados)

Grano del hecho: línea de account.move.line (todos los move_type, state='posted').
La carga es POR LOTES (páginas de account.move.line) para no agotar memoria.
"""
import os
import re
import sys
import math
import time
import logging
import argparse
import xmlrpc.client
from datetime import date
import numpy as np
import pandas as pd
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes.db_loader import DBLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

PAGINA = 5000  # líneas por lote
CTX_ALL = {"active_test": False}  # incluir registros ARCHIVADOS (active=False) en dimensiones

# Rol de cada plan analítico en el hecho. NO se hardcodean IDs: se derivan del NOMBRE del plan
# en Odoo (account.analytic.plan) por nombre normalizado (ver derivar_plan_rol). El rol 'centro'
# va a centro_costo_id; los demás a columnas degeneradas homónimas.
PLAN_ROLES = {"canal", "linea_producto", "tipo_producto", "pais_analitico", "centro"}
# nombre de plan normalizado (sin acentos, minúsculas) -> rol
PLAN_NOMBRE_A_ROL = {
    "pais": "pais_analitico",
    "canal": "canal",
    "linea de producto": "linea_producto",
    "tipo de producto": "tipo_producto",
    "centro de costos": "centro",
    "centro de costo": "centro",
    "la pocion": "centro",   # excepción legacy: plan histórico de centro de costo
}


# ══ Conexión Odoo (mismo patrón que etl_odoo_incremental.py, con rstrip de la URL) ══
def conectar_odoo():
    url = os.getenv("url").rstrip("/")
    db = os.getenv("db")
    user = os.getenv("username_odoo")
    pw = os.getenv("password")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, user, pw, {})
    if not uid:
        raise RuntimeError("Autenticación Odoo fallida.")
    logging.info(f"Odoo conectado (uid={uid})")
    return db, uid, pw, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


class Odoo:
    def __init__(self, db, uid, pw, models):
        self.db, self.uid, self.pw, self.m = db, uid, pw, models

    def _exec(self, modelo, metodo, args, kwargs=None, reintentos=10):
        # Reintenta ante errores transitorios de Odoo (502/503, timeouts, cortes de red).
        # Ventana amplia (~10 min acumulados) para sobrevivir un reinicio/deploy de Odoo.
        for intento in range(1, reintentos + 1):
            try:
                return self.m.execute_kw(self.db, self.uid, self.pw, modelo, metodo, args, kwargs or {})
            except (xmlrpc.client.ProtocolError, ConnectionError, OSError, TimeoutError) as e:
                if intento == reintentos:
                    raise
                espera = min(120, 2 ** intento)
                logging.warning(f"Odoo {modelo}.{metodo} falló ({type(e).__name__}); "
                                f"reintento {intento}/{reintentos} en {espera}s")
                time.sleep(espera)

    @staticmethod
    def _limpiar(registros):
        # Odoo devuelve False para campos escalares vacíos → None (evita 'false' en columnas TEXT).
        # Los Many2one siguen siendo listas [id, nombre]; no se consumen booleanos crudos de Odoo.
        return [{k: (None if v is False else v) for k, v in r.items()} for r in registros]

    def search_read(self, modelo, domain, fields, limit=None, offset=0, order="id asc", context=None):
        opts = {"fields": fields, "offset": offset, "order": order}
        if limit:
            opts["limit"] = limit
        if context:
            opts["context"] = context
        return self._limpiar(self._exec(modelo, "search_read", [domain], opts))

    def read(self, modelo, ids, fields, chunk=500, context=None):
        ids = sorted({i for i in ids if i})
        out = []
        kw = {"fields": fields}
        if context:
            kw["context"] = context
        for i in range(0, len(ids), chunk):
            out.extend(self._exec(modelo, "read", [ids[i:i + chunk]], dict(kw)))
        return self._limpiar(out)


# ══ Helpers de conversión ══
def m2o_id(v):
    return int(v[0]) if isinstance(v, (list, tuple)) and v else None


def m2o_nombre(v):
    return v[1] if isinstance(v, (list, tuple)) and v else None


def as_int(v):
    """Normaliza un id a int de Python o None (evita floats/NaN en columnas BIGINT)."""
    if v is None or v is False or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fecha_key(s):
    if not s:
        return None
    s = str(s)[:10]
    try:
        return int(s[:4] + s[5:7] + s[8:10])
    except ValueError:
        return None


def puc(codigo):
    c = "" if codigo is None else str(codigo).strip()
    return (c[:1] or None, c[:2] or None, c[:4] or None, c[:6] or None)


NATURALEZA_N1 = {"1": "Débito", "5": "Débito", "6": "Débito", "7": "Débito", "8": "Débito",
                 "2": "Crédito", "3": "Crédito", "4": "Crédito", "9": "Crédito"}


# ── Clasificación de estados financieros derivada de los reportes de Odoo (account.report) ──
# seccion / concepto / nivel_movimiento se toman de las LÍNEAS de los informes de Odoo (es_CO):
# Balance/ESF para clases 1/2/3 y Estado de Resultados para 4/5/6/7. Las líneas hoja usan
# engine='account_codes' con prefijos de código PUC; se clasifica cada cuenta por el prefijo que la
# incluye (match más largo, respetando exclusiones). Subiendo del leaf a la raíz:
#   seccion = raíz · concepto = padre del leaf (intermedio) · nivel_movimiento = hoja (DETALLE, para PyG).
# Sin diccionarios manuales → fiel a Odoo.
REP_BALANCE_IDS = [24, 4]     # candidatos de Balance/ESF (localizado CO primero: nombres/prefijos PUC)
REP_PYL_IDS = [38, 23, 7]     # candidatos de Estado de Resultados (dashboard del usuario primero)
_TOK_ACCOUNT_CODES = re.compile(r"(\d+)(?:\\\(([\d,]+)\))?")


def _parse_account_codes(formula):
    """'51\\(5160,5165)' -> (includes={'51'}, excludes={'5160','5165'}); '1705 + 1710' -> {'1705','1710'}."""
    inc, exc = set(), set()
    for pref, ex in _TOK_ACCOUNT_CODES.findall(formula or ""):
        inc.add(pref)
        if ex:
            exc.update(ex.split(","))
    return inc, exc


def _hojas_reporte(od, rid):
    """Prefijos de las líneas hoja de un account.report con su jerarquía:
    seccion=raíz, concepto=padre del leaf (intermedio), nivel_movimiento=hoja (DETALLE)."""
    lineas = od._exec("account.report.line", "search_read", [[["report_id", "=", rid]]],
                      {"fields": ["id", "name", "parent_id"], "context": {"lang": "es_CO"}})
    if not lineas:
        return []
    by_id = {l["id"]: l for l in lineas}

    def cadena(lid):  # [hoja, ..., raíz]
        out = []
        while lid:
            l = by_id.get(lid)
            if not l:
                break
            out.append(l["name"])
            lid = m2o_id(l.get("parent_id"))
        return out

    exprs = od._exec("account.report.expression", "search_read",
                     [[["report_line_id", "in", list(by_id)], ["engine", "=", "account_codes"]]],
                     {"fields": ["report_line_id", "formula"], "context": {"lang": "es_CO"}})
    hojas = []
    for x in exprs:
        l = by_id.get(m2o_id(x["report_line_id"]))
        if not l:
            continue
        inc, exc = _parse_account_codes(x.get("formula"))
        cad = cadena(l["id"])                          # [hoja, padre, ..., raíz]
        leaf = cad[0] if cad else l["name"]            # hoja (DETALLE, va a nivel_movimiento)
        seccion = cad[-1] if cad else l["name"]        # raíz
        concepto = cad[1] if len(cad) >= 2 else leaf   # padre del leaf (intermedio)
        for p in inc:
            hojas.append((p, exc, leaf, concepto, seccion))
    return hojas


def cargar_clasificacion_reportes(od):
    """Devuelve clasificar(codigo) -> (nivel_movimiento=hoja/detalle, concepto, seccion), derivado de
    los reportes de Odoo (Balance + Estado de Resultados, es_CO)."""
    hojas = []
    for candidatos, etiqueta in ((REP_BALANCE_IDS, "balance"), (REP_PYL_IDS, "pyl")):
        for rid in candidatos:
            h = _hojas_reporte(od, rid)
            if h:
                logging.info(f"clasificación EF: reporte {etiqueta} id={rid} ({len(h)} prefijos)")
                hojas.extend(h)
                break
        else:
            logging.warning(f"clasificación EF: ningún reporte con account_codes en {candidatos} ({etiqueta})")

    def clasificar(codigo):
        if not codigo:
            return (None, None, None)
        mejor = None
        for pref, exc, leaf, concepto, sec in hojas:
            if codigo.startswith(pref) and not any(codigo.startswith(x) for x in exc):
                if mejor is None or len(pref) > len(mejor[0]):
                    mejor = (pref, leaf, concepto, sec)
        return (mejor[1], mejor[2], mejor[3]) if mejor else (None, None, None)

    return clasificar


# ── Nombres de la jerarquía PUC (clase/grupo/cuenta/subcuenta) desde account.group (es_CO) ──
# account.group tiene un nodo por prefijo puntual (code_prefix_start==end) con su nombre. Para cada
# longitud 1/2/4/6 se toma el nombre MÁS FRECUENTE del prefijo (resuelve duplicados triviales entre
# empresas: mayúsculas/acentos/singular-plural). Complementa (no reemplaza) seccion/concepto/nivel.
def cargar_puc_nombres(od):
    from collections import defaultdict, Counter
    grupos = od._exec("account.group", "search_read", [[]],
                      {"fields": ["name", "code_prefix_start", "code_prefix_end"],
                       "context": {"lang": "es_CO", "active_test": False}})
    cnt = {1: defaultdict(Counter), 2: defaultdict(Counter), 4: defaultdict(Counter), 6: defaultdict(Counter)}
    for g in grupos:
        a = (g.get("code_prefix_start") or "").strip()
        b = (g.get("code_prefix_end") or "").strip()
        nm = (g.get("name") or "").strip()
        if a and a == b and len(a) in cnt and nm:
            cnt[len(a)][a][nm] += 1
    mapa = {L: {p: c.most_common(1)[0][0] for p, c in d.items()} for L, d in cnt.items()}
    logging.info("nombres PUC (account.group): "
                 + " ".join(f"N{L}={len(mapa[L])}" for L in (1, 2, 4, 6)))

    def nombre_puc(codigo):
        if not codigo:
            return (None, None, None, None)
        return (mapa[1].get(codigo[:1]), mapa[2].get(codigo[:2]),
                mapa[4].get(codigo[:4]), mapa[6].get(codigo[:6]))

    return nombre_puc


def clave_dominante(dist):
    if not isinstance(dist, dict) or not dist:
        return None
    return max(dist.items(), key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) else 0)[0]


def _norm(s):
    """minúsculas sin acentos, para comparar nombres de plan robustamente."""
    import unicodedata
    s = (s or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def derivar_plan_rol(planes):
    """Construye {plan_id -> rol} desde account.analytic.plan (id, name) por nombre normalizado.
    Reemplaza los IDs fijos; deja traza en el log de lo derivado y avisa si falta un rol esperado."""
    plan_rol = {}
    for p in planes:
        rol = PLAN_NOMBRE_A_ROL.get(_norm(p.get("name")))
        if rol:
            plan_rol[as_int(p["id"])] = rol
    logging.info("plan_rol derivado de Odoo: "
                 + ", ".join(f"{p['id']}:{p.get('name')}->{plan_rol[as_int(p['id'])]}"
                             for p in planes if as_int(p["id"]) in plan_rol))
    faltan = PLAN_ROLES - set(plan_rol.values())
    if faltan:
        logging.warning(f"plan_rol: roles esperados SIN plan en Odoo: {sorted(faltan)}")
    return plan_rol


# ══ Carga a Postgres (UPSERT por lote, con aislamiento de fila ofensora) ══
def upsert(loader, df, tabla, pk, schema="marts", coalesce=None):
    if df is None or df.empty:
        return 0
    coalesce = set(coalesce or [])
    df = df.where(pd.notnull(df), None)
    cols = list(df.columns)
    pks = [pk] if isinstance(pk, str) else list(pk)
    set_cols = [c for c in cols if c not in pks]
    # coalesce: no pisar un valor existente con NULL (p.ej. tipo_cliente desde cartera)
    set_sql = ", ".join(
        (f"{c}=COALESCE(EXCLUDED.{c}, {schema}.{tabla}.{c})" if c in coalesce else f"{c}=EXCLUDED.{c}")
        for c in set_cols
    ) or f"{pks[0]}=EXCLUDED.{pks[0]}"
    sql = (f"INSERT INTO {schema}.{tabla} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({', '.join(pks)}) DO UPDATE SET {set_sql}")

    def _nat(v):
        # psycopg2 no adapta escalares numpy: convertir a tipos nativos de Python.
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        if isinstance(v, np.generic):
            v = v.item()
            if isinstance(v, float) and math.isnan(v):
                return None
        return v

    valores = [tuple(_nat(x) for x in row) for row in df.itertuples(index=False, name=None)]

    for intento in range(1, 6):  # reintenta el lote completo si se cae la conexión (idempotente)
        try:
            with loader.get_connection() as conn:
                cur = conn.cursor()
                try:
                    psycopg2.extras.execute_values(cur, sql, valores, page_size=1000)
                    conn.commit()
                    return len(valores)
                except (psycopg2.InterfaceError, psycopg2.OperationalError):
                    raise  # conexión caída → reintentar el lote completo
                except psycopg2.Error as e:
                    # error de DATOS (p.ej. valor muy largo): aislar la fila ofensora
                    try:
                        conn.rollback()
                    except psycopg2.Error:
                        pass
                    logging.error(f"[{schema}.{tabla}] error en lote ({e.pgcode} {str(e).strip()}); aislando fila…")
                    ok = 0
                    for fila in valores:
                        try:
                            psycopg2.extras.execute_values(cur, sql, [fila], page_size=1)
                            conn.commit()
                            ok += 1
                        except psycopg2.Error as e2:
                            conn.rollback()
                            logging.error(f"  FILA OFENSORA en {tabla}: {dict(zip(cols, fila))}\n    -> {str(e2).strip()}")
                    return ok
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            if intento == 5:
                raise
            espera = min(30, 2 ** intento)
            logging.warning(f"[{schema}.{tabla}] conexión caída ({type(e).__name__}); "
                            f"reintento {intento}/5 en {espera}s")
            time.sleep(espera)


def set_watermark(loader, modelo, ultimo_write, filas):
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO marts.etl_control (modelo, ultimo_write, filas, actualizado)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (modelo) DO UPDATE
            SET ultimo_write = GREATEST(marts.etl_control.ultimo_write, EXCLUDED.ultimo_write),
                filas = EXCLUDED.filas, actualizado = now();
        """, (modelo, ultimo_write, filas))
        conn.commit()


def get_watermark(loader, modelo):
    df = loader.consultar("SELECT ultimo_write FROM marts.etl_control WHERE modelo=%s", [modelo])
    if df is not None and not df.empty and df["ultimo_write"][0] is not None:
        return str(df["ultimo_write"][0])
    return None


# ══ Catálogos pequeños (se cargan una vez por corrida) ══
def cargar_catalogos_pequenos(od, loader):
    # Clasificación de estados financieros (seccion/concepto/nivel_movimiento) derivada de los
    # reportes de Odoo (account.report), y nombres de la jerarquía PUC desde account.group (es_CO).
    # Todo fiel a Odoo; sin diccionarios manuales.
    clasificar = cargar_clasificacion_reportes(od)
    nombre_puc = cargar_puc_nombres(od)
    cuentas = od.search_read("account.account", [], ["id", "code", "name", "account_type"], context=CTX_ALL)
    filas = []
    for c in cuentas:
        cod = c.get("code")
        nivel, concepto, seccion = clasificar(cod)
        clase_nombre, grupo_nombre, cuenta_nombre, subcuenta_nombre = nombre_puc(cod)
        filas.append({
            "cuenta_id": as_int(c["id"]), "codigo": cod, "nombre": c.get("name"),
            "clase_codigo": puc(cod)[0], "grupo_codigo": puc(cod)[1],
            "cuenta_codigo": puc(cod)[2], "subcuenta_codigo": puc(cod)[3],
            "clase_nombre": clase_nombre, "grupo_nombre": grupo_nombre,
            "cuenta_nombre": cuenta_nombre, "subcuenta_nombre": subcuenta_nombre,
            "nivel_movimiento": nivel, "concepto": concepto, "seccion": seccion,
            "naturaleza": NATURALEZA_N1.get(puc(cod)[0]),
            "tipo_cuenta": c.get("account_type"),
        })
    dc = pd.DataFrame(filas)
    upsert(loader, dc, "dim_cuenta", "cuenta_id")

    diarios = od.search_read("account.journal", [], ["id", "code", "name", "type"], context=CTX_ALL)
    dd = pd.DataFrame([{"diario_id": as_int(d["id"]), "codigo": d.get("code"),
                        "nombre": d.get("name"), "tipo": d.get("type")} for d in diarios])
    upsert(loader, dd, "dim_diario", "diario_id")

    # Rol de cada plan analítico derivado del NOMBRE del plan en Odoo (no IDs fijos).
    planes = od.search_read("account.analytic.plan", [], ["id", "name"], context=CTX_ALL)
    plan_rol = derivar_plan_rol(planes)

    aa = od.search_read("account.analytic.account", [],
                        ["id", "name", "code", "plan_id", "root_plan_id", "company_id", "active"],
                        context=CTX_ALL)
    an_plan = {a["id"]: m2o_id(a.get("root_plan_id")) for a in aa}
    an_nombre = {a["id"]: a.get("name") for a in aa}
    # dim_centro_costo 100% Odoo (account.analytic.account); NADA de fuentes locales.
    dcc = pd.DataFrame([{
        "centro_costo_id": as_int(a["id"]), "codigo": a.get("code"), "nombre": a.get("name"),
        "plan": m2o_nombre(a.get("plan_id")), "activo": bool(a.get("active")),
        "empresa_id": m2o_id(a.get("company_id")),
    } for a in aa if plan_rol.get(m2o_id(a.get("root_plan_id"))) == "centro"])
    upsert(loader, dcc, "dim_centro_costo", "centro_costo_id")

    empresas = od.search_read("res.company", [], ["id", "name"], context=CTX_ALL)
    de = pd.DataFrame([{"empresa_id": as_int(e["id"]), "nombre": e.get("name")} for e in empresas])
    upsert(loader, de, "dim_empresa", "empresa_id")

    logging.info(f"Catálogos: {len(dc)} cuentas, {len(dd)} diarios, {len(dcc)} centros de costo, "
                 f"{len(de)} empresas")
    return an_plan, an_nombre, plan_rol


# ══ Terceros (dim_tercero) — usado por el hecho y por cartera ══
def cargar_terceros(od, loader, part_ids, tipo_tercero):
    part_ids = [p for p in part_ids if p]
    if not part_ids:
        return
    partners = od.read("res.partner", part_ids, ["id", "name", "vat", "city", "state_id", "country_id"],
                       context=CTX_ALL)
    dt = pd.DataFrame([{
        "tercero_id": as_int(p["id"]), "nombre": p.get("name"), "identificacion": p.get("vat"),
        "tipo_cliente": tipo_tercero.get(p["id"]), "ciudad": p.get("city"),
        "departamento": m2o_nombre(p.get("state_id")), "pais": m2o_nombre(p.get("country_id")),
    } for p in partners])
    # tipo_cliente vía COALESCE: no borrar el existente si esta fuente no lo trae.
    upsert(loader, dt, "dim_tercero", "tercero_id", coalesce=["tipo_cliente"])


# ══ Refresco de dimensiones por su propio write_date (clientes/productos/vendedores) ══
# Cierra el gap: capta creados/modificados en Odoo aunque no tengan transacción nueva.
def refrescar_dimensiones(od, loader, full=False):
    specs = [
        ("res.partner", ["id", "name", "vat", "city", "state_id", "country_id"],
         "dim_tercero", "tercero_id",
         lambda r: {"tercero_id": as_int(r["id"]), "nombre": r.get("name"),
                    "identificacion": r.get("vat"), "ciudad": r.get("city"),
                    "departamento": m2o_nombre(r.get("state_id")),
                    "pais": m2o_nombre(r.get("country_id"))}),  # tipo_cliente no se toca (viene del asiento)
        ("product.product", ["id", "default_code", "name", "categ_id"],
         "dim_producto", "producto_id",
         lambda r: {"producto_id": as_int(r["id"]), "codigo": r.get("default_code"),
                    "nombre": r.get("name"), "categoria": m2o_nombre(r.get("categ_id"))}),
        ("res.users", ["id", "name"], "dim_vendedor", "vendedor_id",
         lambda r: {"vendedor_id": as_int(r["id"]), "nombre": r.get("name")}),
    ]
    for modelo, fields, tabla, pk, builder in specs:
        dom = []
        if not full:
            marca = get_watermark(loader, modelo)
            if marca:
                dom = [["write_date", ">", marca]]
        regs = od.search_read(modelo, dom, fields + ["write_date"], context=CTX_ALL)
        if not regs:
            logging.info(f"  dim {tabla}: sin cambios")
            continue
        upsert(loader, pd.DataFrame([builder(r) for r in regs]), tabla, pk)
        mw = max((str(r["write_date"]) for r in regs if r.get("write_date")), default=None)
        if mw:
            set_watermark(loader, modelo, mw, len(regs))
        logging.info(f"  dim {tabla}: {len(regs)} {'(full)' if full else '(cambios)'}")


# ══ tipo_cliente en dim_tercero por UPDATE (sin releer res.partner de Odoo) ══
def actualizar_tipo_cliente(loader, tipo_tercero):
    filas = [(pid, tc) for pid, tc in tipo_tercero.items() if pid and tc]
    if not filas:
        return
    with loader.get_connection() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            "UPDATE marts.dim_tercero t SET tipo_cliente = v.tc "
            "FROM (VALUES %s) AS v(id, tc) "
            "WHERE t.tercero_id = v.id AND t.tipo_cliente IS DISTINCT FROM v.tc",
            filas)
        conn.commit()


# ══ Dimensiones referenciadas por un lote (partners, products, vendedores) ══
def cargar_dims_lote(od, loader, moves, part_ids, prod_ids, catalogos_completos=False):
    # tipo de cliente por tercero (de la cabecera del asiento)
    tipo_tercero = {}
    for m in moves:
        pid = m2o_id(m.get("partner_id"))
        if pid and m.get("partner_type_id"):
            tipo_tercero[pid] = m2o_nombre(m.get("partner_type_id"))

    if catalogos_completos:
        # full/rebuild: dims ya cargadas por refrescar_dimensiones →
        # NO releer res.partner/product/res.users de Odoo (menos 502, más rápido).
        # Solo actualizar tipo_cliente (viene de la cabecera, no de res.partner).
        actualizar_tipo_cliente(loader, tipo_tercero)
        return

    # incremental: refrescar por lote los referenciados
    usuarios = {m2o_id(m.get("invoice_user_id")): m2o_nombre(m.get("invoice_user_id"))
                for m in moves if m.get("invoice_user_id")}
    if usuarios:
        dv = pd.DataFrame([{"vendedor_id": as_int(k), "nombre": v} for k, v in usuarios.items()])
        upsert(loader, dv, "dim_vendedor", "vendedor_id")

    cargar_terceros(od, loader, part_ids, tipo_tercero)

    if prod_ids:
        productos = od.read("product.product", prod_ids, ["id", "default_code", "name", "categ_id"],
                            context=CTX_ALL)
        dp = pd.DataFrame([{"producto_id": as_int(p["id"]), "codigo": p.get("default_code"),
                            "nombre": p.get("name"), "categoria": m2o_nombre(p.get("categ_id"))}
                           for p in productos])
        upsert(loader, dp, "dim_producto", "producto_id")


# ══ Construir filas del hecho para un lote de líneas ══
def construir_hecho(lineas, mv, an_plan, an_nombre, plan_rol):
    filas = []
    for ln in lineas:
        m = mv.get(m2o_id(ln.get("move_id")), {})
        mtype = m.get("move_type")
        dist = ln.get("analytic_distribution") or {}
        centro = canal = lprod = tprod = pais = None
        clave = clave_dominante(dist)
        if clave:
            for pid in str(clave).split(","):
                aid = as_int(pid)
                if aid is None:
                    continue
                rol = plan_rol.get(an_plan.get(aid))
                if rol == "centro":
                    centro = aid
                elif rol == "canal":
                    canal = an_nombre.get(aid)
                elif rol == "linea_producto":
                    lprod = an_nombre.get(aid)
                elif rol == "tipo_producto":
                    tprod = an_nombre.get(aid)
                elif rol == "pais_analitico":
                    pais = an_nombre.get(aid)
        filas.append({
            "linea_id": as_int(ln["id"]),
            "factura_id": m2o_id(ln.get("move_id")),
            "numero": m.get("name"),
            "referencia": ln.get("ref") or None,
            "estado": "posted",
            "tipo_movimiento": mtype,
            "es_venta": mtype in ("out_invoice", "out_refund"),
            "es_cxc": ln.get("account_type") == "asset_receivable",
            "estado_pago": m.get("payment_state"),
            "reversed_factura_id": m2o_id(m.get("reversed_entry_id")),
            "fecha_key": fecha_key(ln.get("date")),
            "fecha_factura_key": fecha_key(ln.get("invoice_date")),
            "fecha_vencimiento_key": fecha_key(ln.get("date_maturity")),
            "fecha": (str(ln.get("date"))[:10] if ln.get("date") else None),
            "fecha_factura": (str(ln.get("invoice_date"))[:10] if ln.get("invoice_date") else None),
            "fecha_vencimiento": (str(ln.get("date_maturity"))[:10] if ln.get("date_maturity") else None),
            "cuenta_id": m2o_id(ln.get("account_id")),
            "tercero_id": m2o_id(ln.get("partner_id")),
            "producto_id": m2o_id(ln.get("product_id")),
            "vendedor_id": m2o_id(m.get("invoice_user_id")),
            "diario_id": m2o_id(ln.get("journal_id")),
            "empresa_id": m2o_id(ln.get("company_id")),
            "centro_costo_id": centro,
            "canal": canal, "linea_producto": lprod, "tipo_producto": tprod, "pais_analitico": pais,
            "cantidad": ln.get("quantity"), "precio_unitario": ln.get("price_unit"),
            "subtotal": ln.get("price_subtotal"), "debito": ln.get("debit"),
            "credito": ln.get("credit"), "saldo": ln.get("balance"),
            "venta_neta": (ln.get("credit") or 0) - (ln.get("debit") or 0),
            "saldo_pendiente": ln.get("amount_residual"),
            "analytic_distribution": psycopg2.extras.Json(dist) if dist else None,
        })
    return pd.DataFrame(filas)


LINE_FIELDS = ["id", "move_id", "account_id", "account_type", "partner_id", "product_id",
               "journal_id", "company_id", "quantity", "price_unit", "price_subtotal",
               "debit", "credit", "balance", "amount_residual", "date", "invoice_date",
               "date_maturity", "ref", "analytic_distribution", "write_date"]
MOVE_FIELDS = ["id", "name", "move_type", "invoice_user_id", "partner_type_id", "partner_id",
               "payment_state", "reversed_entry_id"]


# ══ Bucle principal por lotes ══
def cargar_hecho(od, loader, domain, an_plan, an_nombre, plan_rol, catalogos_completos=False):
    offset, total, max_write = 0, 0, None
    while True:
        lineas = od.search_read("account.move.line", domain, LINE_FIELDS,
                                limit=PAGINA, offset=offset, order="id asc")
        if not lineas:
            break
        move_ids = [m2o_id(l.get("move_id")) for l in lineas]
        moves = od.read("account.move", move_ids, MOVE_FIELDS)
        mv = {m["id"]: m for m in moves}

        cargar_dims_lote(od, loader, moves,
                         [m2o_id(l.get("partner_id")) for l in lineas],
                         [m2o_id(l.get("product_id")) for l in lineas],
                         catalogos_completos=catalogos_completos)

        dfh = construir_hecho(lineas, mv, an_plan, an_nombre, plan_rol)
        upsert(loader, dfh, "fact_movimiento_contable", "linea_id")

        for l in lineas:
            wd = l.get("write_date")
            if wd and (max_write is None or str(wd) > max_write):
                max_write = str(wd)
        total += len(lineas)
        offset += len(lineas)
        logging.info(f"  lote hecho: +{len(lineas)} (acumulado {total})")
        if len(lineas) < PAGINA:
            break
    return total, max_write


def _desde_key(desde):
    return int(desde.replace("-", "")[:8]) if desde else None


PISO_ANIO = 2018  # año más antiguo a considerar en full sin --desde


def _anios_desc(desde, hasta=None):
    """Genera (anio, fecha_ini, fecha_fin) de más reciente a más antiguo, en [desde..hasta]."""
    y_hi = int(hasta[:4]) if hasta else date.today().year
    y_lo = int(desde[:4]) if desde else PISO_ANIO
    for y in range(y_hi, y_lo - 1, -1):
        ini = desde if (desde and y == y_lo) else f"{y}-01-01"
        fin = hasta if (hasta and y == y_hi) else f"{y}-12-31"
        yield y, ini, fin


# ══ Marcar reversos totales (excluidos de ventas) ══
# es_reverso = factura con payment_state='reversed' O nota crédito que reversa una de ellas.
# Las devoluciones PARCIALES (factura 'paid') NO se marcan: restan vía venta_neta.
_COND_REVERSO = """
    (estado_pago = 'reversed')
 OR (tipo_movimiento = 'out_refund' AND reversed_factura_id IN
        (SELECT factura_id FROM marts.fact_movimiento_contable WHERE estado_pago = 'reversed'))
"""


def marcar_reversos(loader):
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE marts.fact_movimiento_contable SET es_reverso = TRUE "
                    f"WHERE es_reverso IS NOT TRUE AND ({_COND_REVERSO});")
        n_true = cur.rowcount
        cur.execute(f"UPDATE marts.fact_movimiento_contable SET es_reverso = FALSE "
                    f"WHERE es_reverso IS TRUE AND NOT ({_COND_REVERSO});")
        conn.commit()
    logging.info(f"Reversos marcados: +{n_true} líneas es_reverso=TRUE")


def aplicar_correcciones(loader):
    df = loader.consultar("SELECT tabla, pk_col, pk_val, campo, valor_nuevo "
                          "FROM marts.correcciones WHERE activo IS TRUE")
    if df is None or df.empty:
        return
    with loader.get_connection() as conn:
        cur = conn.cursor()
        n = 0
        for _, r in df.iterrows():
            try:
                cur.execute(
                    f"UPDATE marts.{r['tabla']} SET {r['campo']} = %s WHERE {r['pk_col']} = %s",
                    (r["valor_nuevo"], int(r["pk_val"])))
                n += cur.rowcount
            except psycopg2.Error as e:
                conn.rollback()
                logging.error(f"Corrección fallida ({r['tabla']}.{r['campo']} id={r['pk_val']}): {e}")
        conn.commit()
    logging.info(f"Correcciones aplicadas: {n} filas")


# ══ Canonicalización PUC (no destructivo): unifica códigos 8 vs 9 díg de la MISMA cuenta ══
# Canónico = variante más usada en el hecho dentro de (subcuenta 6 díg + nombre normalizado).
# El hecho conserva el cuenta_id real de Odoo; solo se pueblan columnas en dim_cuenta.
# Requiere el hecho ya cargado (usa conteos de uso). Ver sql/marts/11_puc_canonico.sql.
_SQL_PUC_CANONICO = """
ALTER TABLE marts.dim_cuenta
    ADD COLUMN IF NOT EXISTS cuenta_canonica_id BIGINT,
    ADD COLUMN IF NOT EXISTS codigo_canonico    VARCHAR(20),
    ADD COLUMN IF NOT EXISTS nombre_canonico    TEXT;

WITH usos AS (
    SELECT cuenta_id, COUNT(*) AS n FROM marts.fact_movimiento_contable GROUP BY cuenta_id
),
base AS (
    SELECT c.cuenta_id, c.codigo, left(c.codigo,6) AS p6, upper(trim(c.nombre)) AS nom,
           COALESCE(u.n,0) AS usos
    FROM marts.dim_cuenta c LEFT JOIN usos u ON u.cuenta_id=c.cuenta_id
    WHERE c.codigo IS NOT NULL AND c.nombre IS NOT NULL
),
canon AS (
    SELECT p6, nom,
           (array_agg(cuenta_id ORDER BY usos DESC, length(codigo) ASC, cuenta_id ASC))[1] AS canon_id
    FROM base GROUP BY p6, nom
)
UPDATE marts.dim_cuenta d
   SET cuenta_canonica_id = cc.cuenta_id, codigo_canonico = cc.codigo, nombre_canonico = cc.nombre
FROM base b
JOIN canon k ON k.p6=b.p6 AND k.nom=b.nom
JOIN marts.dim_cuenta cc ON cc.cuenta_id=k.canon_id
WHERE d.cuenta_id=b.cuenta_id;

UPDATE marts.dim_cuenta
   SET cuenta_canonica_id = cuenta_id, codigo_canonico = codigo, nombre_canonico = nombre
 WHERE cuenta_canonica_id IS NULL;
"""


def canonicalizar_puc(loader):
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SQL_PUC_CANONICO)
        conn.commit()
    logging.info("Canonicalización PUC aplicada (dim_cuenta.codigo_canonico).")


def main(modo, desde, hasta=None):
    db, uid, pw, models = conectar_odoo()
    od = Odoo(db, uid, pw, models)
    loader = DBLoader()

    an_plan, an_nombre, plan_rol = cargar_catalogos_pequenos(od, loader)

    # Refresco de dimensiones (clientes/productos/vendedores) por su propio write_date.
    # full/rebuild → refresco total; incremental/dims → solo cambios.
    refrescar_dimensiones(od, loader, full=(modo in ("full", "rebuild")))
    if modo == "dims":
        logging.info("OK DIMS: catálogos y dimensiones refrescados.")
        return

    if modo == "incremental":
        marca_l = get_watermark(loader, "account.move.line")
        dom = [["parent_state", "=", "posted"]]
        if marca_l:
            dom.append(["write_date", ">", marca_l])
        logging.info(f"INCREMENTAL (líneas > {marca_l})")
        total_h, mw_h = cargar_hecho(od, loader, dom, an_plan, an_nombre, plan_rol)
    else:
        # full / rebuild: cargar por AÑO, más reciente primero (2026 se completa antes).
        if modo == "rebuild":
            desde = desde or f"{date.today().year}-01-01"   # por defecto: año actual
            with loader.get_connection() as conn:
                cur = conn.cursor()
                if hasta:
                    cur.execute("DELETE FROM marts.fact_movimiento_contable "
                                "WHERE fecha_key BETWEEN %s AND %s;",
                                (_desde_key(desde), _desde_key(hasta)))
                else:
                    cur.execute("DELETE FROM marts.fact_movimiento_contable WHERE fecha_key >= %s;",
                                (_desde_key(desde),))
                conn.commit()
            logging.info(f"REBUILD {desde}..{hasta or 'hoy'}: rango borrado; recarga por año.")
        else:
            logging.info(f"FULL{' desde ' + desde if desde else ' (histórico completo)'}")
        total_h, mw_h = 0, None
        for anio, ini, fin in _anios_desc(desde, hasta):
            dom = [["parent_state", "=", "posted"], ["date", ">=", ini], ["date", "<=", fin]]
            t, mw = cargar_hecho(od, loader, dom, an_plan, an_nombre, plan_rol, catalogos_completos=True)
            total_h += t
            if mw and (mw_h is None or mw > mw_h):
                mw_h = mw
            logging.info(f"── Año {anio}: {t} líneas (acumulado {total_h}) ──")

    if mw_h:
        set_watermark(loader, "account.move.line", mw_h, total_h)

    marcar_reversos(loader)      # ventas: excluir reversos totales
    aplicar_correcciones(loader)  # limpieza de datos mal registrados en Odoo
    canonicalizar_puc(loader)     # unifica códigos 8 vs 9 díg de la misma cuenta (no destructivo)

    logging.info(f"OK {modo.upper()} completado: hecho={total_h} líneas.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--full", action="store_true", help="carga histórica completa (sin truncar)")
    g.add_argument("--incremental", action="store_true", help="solo cambios (write_date > marca)")
    g.add_argument("--rebuild", action="store_true",
                   help="recreación por rango: DELETE + recarga (por defecto el año actual)")
    g.add_argument("--dims", action="store_true",
                   help="solo refrescar catálogos y dimensiones (sin hechos)")
    ap.add_argument("--desde", default=None,
                    help="fecha mínima YYYY-MM-DD (--rebuild: default año actual; --full: opcional)")
    ap.add_argument("--hasta", default=None,
                    help="fecha máxima YYYY-MM-DD (acota el rango en --rebuild/--full)")
    args = ap.parse_args()
    modo = ("rebuild" if args.rebuild else "full" if args.full
            else "dims" if args.dims else "incremental")
    main(modo, args.desde, args.hasta)
