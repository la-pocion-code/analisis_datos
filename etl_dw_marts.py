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
import http.client
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
PLAN_ROLES = {"canal", "linea_producto", "tipo_producto", "pais_analitico", "centro", "cliente_analitico"}
# nombre de plan normalizado (sin acentos, minúsculas) -> rol
PLAN_NOMBRE_A_ROL = {
    "pais": "pais_analitico",
    "canal": "canal",
    "cliente": "cliente_analitico",   # plan 22 "Cliente": atribuye ventas/gastos al cliente (export + clave)
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
        # http.client.HTTPException cubre IncompleteRead/BadStatusLine (respuesta cortada a medias):
        # hereda de Exception, NO de OSError, así que hay que nombrarla explícitamente.
        for intento in range(1, reintentos + 1):
            try:
                return self.m.execute_kw(self.db, self.uid, self.pw, modelo, metodo, args, kwargs or {})
            except (xmlrpc.client.ProtocolError, http.client.HTTPException,
                    ConnectionError, OSError, TimeoutError) as e:
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


# ── Etiquetas de terceros (res.partner.category, many2many) ──────────────────
# Odoo devuelve category_id como lista de IDs (m2m); se resuelve a nombres con un mapa
# id→nombre cargado UNA sola vez por proceso (la tabla de categorías es pequeña).
_CAT_TERCERO = None


def cat_map_tercero(od):
    """Mapa id→nombre de res.partner.category (etiquetas de terceros); cacheado por proceso."""
    global _CAT_TERCERO
    if _CAT_TERCERO is None:
        cats = od.search_read("res.partner.category", [], ["id", "name"], context=CTX_ALL)
        _CAT_TERCERO = {c["id"]: c.get("name") for c in cats}
    return _CAT_TERCERO


def etiquetas_nombres(cat_ids, cat_map):
    """Lista de IDs de category_id (m2m) -> 'Etiqueta A; Etiqueta B' (o None)."""
    if not cat_ids:
        return None
    nombres = [cat_map.get(i) for i in cat_ids if cat_map.get(i)]
    return "; ".join(nombres) if nombres else None


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
def upsert(loader, df, tabla, pk, schema="marts", coalesce=None, reemplazar=False):
    """`reemplazar=True` hace TRUNCATE + INSERT en la MISMA transacción: la tabla nunca se ve vacía
    desde fuera. Se usa en las tablas puente (map_nc_factura/map_nd_factura), que se reconstruyen
    enteras en cada corrida y que leen en vivo la intranet y Power BI: con el TRUNCATE en su propia
    transacción había una ventana en la que las NC no restaban en el mes de su factura."""
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
                    if reemplazar:   # atómico: nadie ve la tabla vacía (mismo commit que el INSERT)
                        cur.execute(f"TRUNCATE {schema}.{tabla};")
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
# Campos leídos de Odoo por catálogo (compartidos por la carga masiva y el self-heal por-id).
CUENTA_FIELDS = ["id", "code", "name", "account_type"]
DIARIO_FIELDS = ["id", "code", "name", "type"]
CENTRO_FIELDS = ["id", "name", "code", "plan_id", "root_plan_id", "company_id", "active"]


# ── Row-builders compartidos: mismos registros de Odoo → filas de la dim ──
def _filas_cuentas(cuentas, clasificar, nombre_puc):
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
    return filas


def _filas_diarios(diarios):
    return [{"diario_id": as_int(d["id"]), "codigo": d.get("code"),
             "nombre": d.get("name"), "tipo": d.get("type")} for d in diarios]


def _filas_empresas(empresas):
    return [{"empresa_id": as_int(e["id"]), "nombre": e.get("name")} for e in empresas]


def _filas_centros(aa, plan_rol):
    # dim_centro_costo 100% Odoo (account.analytic.account); solo planes con rol 'centro'.
    return [{"centro_costo_id": as_int(a["id"]), "codigo": a.get("code"), "nombre": a.get("name"),
             "plan": m2o_nombre(a.get("plan_id")), "activo": bool(a.get("active")),
             "empresa_id": m2o_id(a.get("company_id"))}
            for a in aa if plan_rol.get(m2o_id(a.get("root_plan_id"))) == "centro"]


# ── Loaders por-id (para el self-heal): traen de Odoo solo los ids indicados ──
def cargar_cuentas(od, loader, ids, clasificar, nombre_puc):
    ids = [i for i in set(ids) if i]
    if not ids:
        return
    filas = _filas_cuentas(od.read("account.account", ids, CUENTA_FIELDS, context=CTX_ALL),
                           clasificar, nombre_puc)
    if filas:
        upsert(loader, pd.DataFrame(filas), "dim_cuenta", "cuenta_id")


def cargar_diarios(od, loader, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return
    filas = _filas_diarios(od.read("account.journal", ids, DIARIO_FIELDS, context=CTX_ALL))
    if filas:
        upsert(loader, pd.DataFrame(filas), "dim_diario", "diario_id")


def cargar_empresas(od, loader, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return
    filas = _filas_empresas(od.read("res.company", ids, ["id", "name"], context=CTX_ALL))
    if filas:
        upsert(loader, pd.DataFrame(filas), "dim_empresa", "empresa_id")


def cargar_centros(od, loader, ids, plan_rol):
    ids = [i for i in set(ids) if i]
    if not ids:
        return
    filas = _filas_centros(od.read("account.analytic.account", ids, CENTRO_FIELDS, context=CTX_ALL),
                           plan_rol)
    if filas:
        upsert(loader, pd.DataFrame(filas), "dim_centro_costo", "centro_costo_id")


def cargar_catalogos_pequenos(od, loader):
    # Clasificación de estados financieros (seccion/concepto/nivel_movimiento) derivada de los
    # reportes de Odoo (account.report), y nombres de la jerarquía PUC desde account.group (es_CO).
    # Todo fiel a Odoo; sin diccionarios manuales. Devuelve las clausuras clasificar/nombre_puc
    # para que el self-heal (asegurar_dims_hecho) pueda cargar cuentas faltantes con igual criterio.
    clasificar = cargar_clasificacion_reportes(od)
    nombre_puc = cargar_puc_nombres(od)
    cuentas = od.search_read("account.account", [], CUENTA_FIELDS, context=CTX_ALL)
    dc = pd.DataFrame(_filas_cuentas(cuentas, clasificar, nombre_puc))
    upsert(loader, dc, "dim_cuenta", "cuenta_id")

    diarios = od.search_read("account.journal", [], DIARIO_FIELDS, context=CTX_ALL)
    dd = pd.DataFrame(_filas_diarios(diarios))
    upsert(loader, dd, "dim_diario", "diario_id")

    # Rol de cada plan analítico derivado del NOMBRE del plan en Odoo (no IDs fijos).
    planes = od.search_read("account.analytic.plan", [], ["id", "name"], context=CTX_ALL)
    plan_rol = derivar_plan_rol(planes)

    aa = od.search_read("account.analytic.account", [], CENTRO_FIELDS, context=CTX_ALL)
    an_plan = {a["id"]: m2o_id(a.get("root_plan_id")) for a in aa}
    an_nombre = {a["id"]: a.get("name") for a in aa}
    dcc = pd.DataFrame(_filas_centros(aa, plan_rol))
    upsert(loader, dcc, "dim_centro_costo", "centro_costo_id")

    empresas = od.search_read("res.company", [], ["id", "name"], context=CTX_ALL)
    de = pd.DataFrame(_filas_empresas(empresas))
    upsert(loader, de, "dim_empresa", "empresa_id")

    logging.info(f"Catálogos: {len(dc)} cuentas, {len(dd)} diarios, {len(dcc)} centros de costo, "
                 f"{len(de)} empresas")
    return an_plan, an_nombre, plan_rol, clasificar, nombre_puc


# ══ Terceros (dim_tercero) — usado por el hecho y por cartera ══
def cargar_terceros(od, loader, part_ids, tipo_tercero):
    part_ids = [p for p in part_ids if p]
    if not part_ids:
        return
    cmap = cat_map_tercero(od)
    # Sin team_id: el equipo de ventas va en el hecho (fact.equipo), no en el tercero.
    partners = od.read("res.partner", part_ids,
                       ["id", "name", "vat", "city", "state_id", "country_id",
                        "phone", "mobile", "email", "category_id", "commercial_partner_id"],
                       context=CTX_ALL)
    dt = pd.DataFrame([{
        "tercero_id": as_int(p["id"]), "nombre": p.get("name"), "identificacion": p.get("vat"),
        "tipo_cliente": tipo_tercero.get(p["id"]), "ciudad": p.get("city"),
        "departamento": m2o_nombre(p.get("state_id")), "pais": m2o_nombre(p.get("country_id")),
        "telefono": p.get("phone") or p.get("mobile"), "email": p.get("email"),
        "etiqueta": etiquetas_nombres(p.get("category_id"), cmap),
        "cliente_padre_id": m2o_id(p.get("commercial_partner_id")),
        "cliente_padre": m2o_nombre(p.get("commercial_partner_id")),
    } for p in partners])
    # tipo_cliente vía COALESCE: no borrar el existente si esta fuente no lo trae.
    upsert(loader, dt, "dim_tercero", "tercero_id", coalesce=["tipo_cliente"])


def cargar_productos(od, loader, prod_ids):
    # es_kit lo fija cargar_kits (BOM phantom); aquí no, para no pisarlo con bom_count.
    prod_ids = [p for p in prod_ids if p]
    if not prod_ids:
        return
    productos = od.read("product.product", prod_ids,
                        ["id", "default_code", "name", "categ_id"], context=CTX_ALL)
    dp = pd.DataFrame([{"producto_id": as_int(p["id"]), "codigo": p.get("default_code"),
                        "nombre": p.get("name"), "categoria": m2o_nombre(p.get("categ_id"))}
                       for p in productos])
    upsert(loader, dp, "dim_producto", "producto_id")


# ══ Refresco de dimensiones por su propio write_date (clientes/productos/vendedores) ══
# Cierra el gap: capta creados/modificados en Odoo aunque no tengan transacción nueva.
def refrescar_dimensiones(od, loader, full=False):
    cmap = cat_map_tercero(od)  # etiquetas de terceros (m2m id→nombre), cargado una vez
    specs = [
        # OJO: nada de team_id aquí — el equipo de ventas vive en el asiento (fact.equipo), no en
        # el tercero (res.partner.team_id está vacío en este Odoo).
        ("res.partner", ["id", "name", "vat", "city", "state_id", "country_id",
                         "phone", "mobile", "email", "category_id", "commercial_partner_id"],
         "dim_tercero", "tercero_id",
         lambda r: {"tercero_id": as_int(r["id"]), "nombre": r.get("name"),
                    "identificacion": r.get("vat"), "ciudad": r.get("city"),
                    "departamento": m2o_nombre(r.get("state_id")),
                    "pais": m2o_nombre(r.get("country_id")),
                    "telefono": r.get("phone") or r.get("mobile"), "email": r.get("email"),
                    "etiqueta": etiquetas_nombres(r.get("category_id"), cmap),
                    "cliente_padre_id": m2o_id(r.get("commercial_partner_id")),
                    "cliente_padre": m2o_nombre(r.get("commercial_partner_id"))}),
        # tipo_cliente no se toca (viene del asiento)
        # es_kit NO se setea aquí: lo fija cargar_kits desde dim_kit_componente (BOM phantom).
        # `bom_count > 0` marcaría también los productos FABRICADOS, que no son kits.
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
        # POR PÁGINAS: un search_read sin límite de ~206k terceros (con contacto/etiqueta/padre) hace
        # que Odoo corte la respuesta a medias → http.client.IncompleteRead. Paginar acota el payload
        # y la memoria. El order por defecto ('id asc') hace estable el offset.
        offset, total, mw = 0, 0, None
        while True:
            regs = od.search_read(modelo, dom, fields + ["write_date"],
                                  limit=PAGINA, offset=offset, context=CTX_ALL)
            if not regs:
                break
            upsert(loader, pd.DataFrame([builder(r) for r in regs]), tabla, pk)
            m = max((str(r["write_date"]) for r in regs if r.get("write_date")), default=None)
            if m and (mw is None or m > mw):
                mw = m
            total += len(regs)
            offset += len(regs)
        if not total:
            logging.info(f"  dim {tabla}: sin cambios")
            continue
        if mw:
            set_watermark(loader, modelo, mw, total)
        logging.info(f"  dim {tabla}: {total} {'(full)' if full else '(cambios)'}")


# ══ Kits: explosión de BOM phantom (mrp.bom) → dim_kit_componente ══
# Refresco TOTAL (los kits son pocos): TRUNCATE + insert, así refleja componentes removidos.
def cargar_kits(od, loader):
    """dim_kit_componente = kit (product.product) → componentes (product.product) con cantidad del BOM.
    Solo BOM tipo 'phantom' (los que se venden como kit y se explotan)."""
    boms = od.search_read("mrp.bom", [["type", "=", "phantom"]],
                          ["id", "product_tmpl_id", "product_id", "product_qty"], context=CTX_ALL)
    if not boms:
        logging.info("  kits: sin BOM phantom en Odoo")
        return
    # product.product del kit: product_id directo del BOM, o las variantes de su product_tmpl_id.
    tmpl_ids = {m2o_id(b.get("product_tmpl_id")) for b in boms if not m2o_id(b.get("product_id"))}
    tmpl_ids.discard(None)
    tmpl_a_prod = {}
    if tmpl_ids:
        variantes = od.search_read("product.product", [["product_tmpl_id", "in", list(tmpl_ids)]],
                                   ["id", "product_tmpl_id"], context=CTX_ALL)
        for v in variantes:
            tmpl_a_prod.setdefault(m2o_id(v.get("product_tmpl_id")), []).append(as_int(v["id"]))
    # Componentes (mrp.bom.line) por BOM.
    lineas = od.search_read("mrp.bom.line", [["bom_id", "in", [b["id"] for b in boms]]],
                            ["bom_id", "product_id", "product_qty"], context=CTX_ALL)
    comps_por_bom = {}
    for ln in lineas:
        comps_por_bom.setdefault(m2o_id(ln.get("bom_id")), []).append(
            (as_int(m2o_id(ln.get("product_id"))), float(ln.get("product_qty") or 0)))
    # ⚠ En Odoo hay VARIAS BOM phantom por kit (77 BOMs para 39 kits: 38 templates con 2 cada uno).
    # Sumarlas duplicaba la cantidad (guardaba 2.0 donde la BOM dice 1.0) y la explosión daba el doble
    # de unidades por componente. Se toma UNA sola BOM por kit: la de `id` más alto (la más reciente).
    bom_por_kit = {}   # kit_producto_id -> BOM elegida
    for b in boms:
        pid = m2o_id(b.get("product_id"))
        kit_ids = [as_int(pid)] if pid else tmpl_a_prod.get(m2o_id(b.get("product_tmpl_id")), [])
        for kit_id in kit_ids:
            if kit_id is None:
                continue
            elegida = bom_por_kit.get(kit_id)
            if elegida is None or b["id"] > elegida["id"]:
                bom_por_kit[kit_id] = b
    filas = []
    for kit_id, b in bom_por_kit.items():
        # cantidad POR UNIDAD DE KIT: la BOM puede estar definida por lote (product_qty > 1).
        lote = float(b.get("product_qty") or 1) or 1
        for comp_id, qty in comps_por_bom.get(b["id"], []):
            if comp_id is not None:
                filas.append({"kit_producto_id": kit_id, "componente_id": comp_id,
                              "cantidad": qty / lote})
    if not filas:
        logging.info("  kits: BOM phantom sin líneas de componente")
        return
    df = (pd.DataFrame(filas)
          .groupby(["kit_producto_id", "componente_id"], as_index=False)["cantidad"].sum())
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("TRUNCATE marts.dim_kit_componente;")
        conn.commit()
    upsert(loader, df, "dim_kit_componente", ["kit_producto_id", "componente_id"])
    # es_kit = ser un KIT de verdad (BOM phantom con componentes), NO tener lista de materiales:
    # `bom_count > 0` también es TRUE para los productos FABRICADOS y marcaba 139 productos en vez de 39.
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE marts.dim_producto p
               SET es_kit = EXISTS (SELECT 1 FROM marts.dim_kit_componente k
                                     WHERE k.kit_producto_id = p.producto_id)
             WHERE p.es_kit IS DISTINCT FROM EXISTS (
                     SELECT 1 FROM marts.dim_kit_componente k
                      WHERE k.kit_producto_id = p.producto_id);""")
        n_kit = cur.rowcount
        conn.commit()
    logging.info(f"  kits: {len(df)} pares kit-componente ({len(bom_por_kit)} kits), "
                 f"es_kit corregido en {n_kit} productos")


# ══ Nombre COMERCIAL del producto (product.template.name en es_CO) → dim_producto.nombre_comercial ══
def enriquecer_nombre_comercial(od, loader):
    """dim_producto.nombre = product.product.name en el idioma BASE (p. ej. PCN19 = "Kit anticaída y
    crecimiento capilar"). El nombre por el que se reconoce comercialmente el producto (PCN19 =
    "DUTONIC (TONICO CAPILAR)") es la TRADUCCIÓN es_CO del `name` de la PLANTILLA product.template:
    ese campo es traducible y en es_CO trae el nombre comercial (verificado). Por eso se lee con
    context lang='es_CO' (no display_name, que trae el [código] delante). 100% Odoo, pocos miles."""
    ctx_es = {**CTX_ALL, "lang": "es_CO"}
    prods, off = [], 0
    while True:  # por páginas (patrón anti-IncompleteRead, como refrescar_dimensiones)
        page = od.search_read("product.product", [], ["id", "product_tmpl_id"],
                              limit=PAGINA, offset=off, context=CTX_ALL)
        if not page:
            break
        prods += page
        off += len(page)
    tmpl_ids = list({m2o_id(p["product_tmpl_id"]) for p in prods if p.get("product_tmpl_id")})
    if not tmpl_ids:
        logging.info("  nombre_comercial: sin plantillas")
        return
    nombre_tmpl = {t["id"]: t.get("name")
                   for t in od.read("product.template", tmpl_ids, ["name"], context=ctx_es)}
    filas = [{"producto_id": as_int(p["id"]),
              "nombre_comercial": nombre_tmpl.get(m2o_id(p["product_tmpl_id"]))}
             for p in prods if p.get("product_tmpl_id")]
    # upsert solo SETea las columnas del DataFrame → no pisa codigo/nombre/categoria/es_kit.
    n = upsert(loader, pd.DataFrame(filas), "dim_producto", "producto_id")
    logging.info(f"  nombre_comercial: {n} productos enriquecidos (product.template.name)")


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


# ══ Ids de un lote que NO están aún en su dimensión (para auto-sanar FKs) ══
def ids_faltantes(loader, tabla, pk, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return []
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {pk} FROM marts.{tabla} WHERE {pk} = ANY(%s);", (ids,))
        existentes = {r[0] for r in cur.fetchall()}
    return [i for i in ids if i not in existentes]


# ══ Generar filas de calendario (dim_fecha) faltantes ══
# dim_fecha es un calendario generado (2024-2034; ver 01_star_schema.sql). Guardia para el self-heal:
# si el hecho referencia una fecha fuera de ese rango, se crea su fila (mismo cálculo que el DDL).
def generar_fechas(loader, fkeys):
    fechas = []
    for k in fkeys:
        s = str(int(k))
        if len(s) == 8:
            fechas.append(f"{s[:4]}-{s[4:6]}-{s[6:8]}")
    if not fechas:
        return
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO marts.dim_fecha
            SELECT
                (EXTRACT(YEAR FROM d)*10000 + EXTRACT(MONTH FROM d)*100 + EXTRACT(DAY FROM d))::int,
                d::date,
                EXTRACT(YEAR FROM d)::smallint, EXTRACT(QUARTER FROM d)::smallint,
                EXTRACT(MONTH FROM d)::smallint, INITCAP(TO_CHAR(d,'TMMonth')),
                EXTRACT(DAY FROM d)::smallint, EXTRACT(ISODOW FROM d)::smallint,
                INITCAP(TO_CHAR(d,'TMDay')), EXTRACT(WEEK FROM d)::smallint,
                (EXTRACT(ISODOW FROM d) >= 6),
                (EXTRACT(YEAR FROM d)*100 + EXTRACT(MONTH FROM d))::int
            FROM unnest(%s::date[]) AS g(d)
            ON CONFLICT (fecha_key) DO NOTHING;
        """, (fechas,))
        conn.commit()


# ══ Auto-sanar TODAS las dims referenciadas por el hecho pero faltantes ══
# Las dims/catálogos se cargan UNA vez al inicio; algo CREADO en Odoo mientras corre el ETL (un
# tercero/producto nuevo, una cuenta/diario/centro nuevo…) no está en su dim → viola la FK del hecho.
# Aquí, con el DataFrame del hecho ya construido, se traen de Odoo SOLO los ids faltantes de CADA
# dimensión (el hueco normal es 0 → sin lecturas extra). Corre siempre (incremental y full/rebuild).
def _col_ids(dfh, col):
    if col not in dfh.columns:
        return []
    return [int(x) for x in dfh[col].dropna().unique().tolist()]


def asegurar_dims_hecho(od, loader, dfh, moves, clasificar, nombre_puc, plan_rol):
    if dfh.empty:
        return
    tipo_tercero, usuarios = {}, {}
    for m in moves:
        pid = m2o_id(m.get("partner_id"))
        if pid and m.get("partner_type_id"):
            tipo_tercero[pid] = m2o_nombre(m.get("partner_type_id"))
        uid = m2o_id(m.get("invoice_user_id"))
        if uid:
            usuarios[uid] = m2o_nombre(m.get("invoice_user_id"))

    ter = ids_faltantes(loader, "dim_tercero", "tercero_id", _col_ids(dfh, "tercero_id"))
    if ter:
        cargar_terceros(od, loader, ter, tipo_tercero)
    prod = ids_faltantes(loader, "dim_producto", "producto_id", _col_ids(dfh, "producto_id"))
    if prod:
        cargar_productos(od, loader, prod)
    ven = ids_faltantes(loader, "dim_vendedor", "vendedor_id", _col_ids(dfh, "vendedor_id"))
    if ven:
        dv = pd.DataFrame([{"vendedor_id": as_int(k), "nombre": usuarios.get(k)} for k in ven])
        upsert(loader, dv, "dim_vendedor", "vendedor_id")
    cta = ids_faltantes(loader, "dim_cuenta", "cuenta_id", _col_ids(dfh, "cuenta_id"))
    if cta:
        cargar_cuentas(od, loader, cta, clasificar, nombre_puc)
    dia = ids_faltantes(loader, "dim_diario", "diario_id", _col_ids(dfh, "diario_id"))
    if dia:
        cargar_diarios(od, loader, dia)
    emp = ids_faltantes(loader, "dim_empresa", "empresa_id", _col_ids(dfh, "empresa_id"))
    if emp:
        cargar_empresas(od, loader, emp)
    cen = ids_faltantes(loader, "dim_centro_costo", "centro_costo_id", _col_ids(dfh, "centro_costo_id"))
    if cen:
        cargar_centros(od, loader, cen, plan_rol)
    fkeys = set()
    for col in ("fecha_key", "fecha_factura_key", "fecha_vencimiento_key"):
        fkeys.update(_col_ids(dfh, col))
    fec = ids_faltantes(loader, "dim_fecha", "fecha_key", list(fkeys))
    if fec:
        generar_fechas(loader, fec)

    sanadas = [(ter, "terceros"), (prod, "productos"), (ven, "vendedores"), (cta, "cuentas"),
               (dia, "diarios"), (emp, "empresas"), (cen, "centros"), (fec, "fechas")]
    if any(g for g, _ in sanadas):
        logging.info("  dims auto-sanadas: " + ", ".join(f"{len(g)} {n}" for g, n in sanadas if g))


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
        # Lo CREADO en Odoo durante la corrida lo auto-sana asegurar_dims_hecho (sobre el hecho ya
        # construido, cubre TODAS las dims), justo antes del upsert del hecho.
        actualizar_tipo_cliente(loader, tipo_tercero)
        return

    # incremental: refrescar por lote los referenciados
    usuarios = {m2o_id(m.get("invoice_user_id")): m2o_nombre(m.get("invoice_user_id"))
                for m in moves if m.get("invoice_user_id")}
    if usuarios:
        dv = pd.DataFrame([{"vendedor_id": as_int(k), "nombre": v} for k, v in usuarios.items()])
        upsert(loader, dv, "dim_vendedor", "vendedor_id")

    cargar_terceros(od, loader, part_ids, tipo_tercero)
    cargar_productos(od, loader, prod_ids)


# ══ Construir filas del hecho para un lote de líneas ══
def construir_hecho(lineas, mv, an_plan, an_nombre, plan_rol):
    filas = []
    for ln in lineas:
        m = mv.get(m2o_id(ln.get("move_id")), {})
        mtype = m.get("move_type")
        dist = ln.get("analytic_distribution") or {}
        centro = canal = lprod = tprod = pais = cliente = None
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
                elif rol == "cliente_analitico":
                    cliente = an_nombre.get(aid)
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
            "equipo": m2o_nombre(m.get("team_id")),   # equipo de ventas (del asiento, no del tercero)
            "diario_id": m2o_id(ln.get("journal_id")),
            "empresa_id": m2o_id(ln.get("company_id")),
            "centro_costo_id": centro,
            "canal": canal, "linea_producto": lprod, "tipo_producto": tprod, "pais_analitico": pais,
            "cliente_analitico": cliente,
            # ⚠ precio_unitario / subtotal / total_con_impuesto vienen EN LA MONEDA DE LA FACTURA
            # (las exportaciones se facturan en USD) y precio_unitario además INCLUYE IVA. Los
            # importes en COP son debito/credito/saldo/venta_neta. Por eso el valor con IVA se
            # calcula como RAZÓN (total_con_impuesto/subtotal) en las vistas, nunca sumando
            # total_con_impuesto. Medición y detalle: sql/marts/32_iva_ventas.sql.
            "cantidad": ln.get("quantity"), "precio_unitario": ln.get("price_unit"),
            "subtotal": ln.get("price_subtotal"), "debito": ln.get("debit"),
            "total_con_impuesto": ln.get("price_total"),
            "moneda": m2o_nombre(ln.get("currency_id")),
            "credito": ln.get("credit"), "saldo": ln.get("balance"),
            "venta_neta": (ln.get("credit") or 0) - (ln.get("debit") or 0),
            "saldo_pendiente": ln.get("amount_residual"),
            "analytic_distribution": psycopg2.extras.Json(dist) if dist else None,
        })
    return pd.DataFrame(filas)


LINE_FIELDS = ["id", "move_id", "account_id", "account_type", "partner_id", "product_id",
               "journal_id", "company_id", "quantity", "price_unit", "price_subtotal",
               "price_total", "currency_id",
               "debit", "credit", "balance", "amount_residual", "date", "invoice_date",
               "date_maturity", "ref", "analytic_distribution", "write_date"]
MOVE_FIELDS = ["id", "name", "move_type", "invoice_user_id", "partner_type_id", "partner_id",
               "payment_state", "reversed_entry_id", "team_id"]


# ══ Bucle principal por lotes ══
def cargar_hecho(od, loader, domain, an_plan, an_nombre, plan_rol, clasificar, nombre_puc,
                 catalogos_completos=False):
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
        # Auto-sanar TODA dim referenciada por el hecho pero aún ausente (evita FK; normalmente 0).
        asegurar_dims_hecho(od, loader, dfh, moves, clasificar, nombre_puc, plan_rol)
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
# es_reverso = ANULACIÓN real = factura + su(s) NC de reversión que la cubren ≥99% (por clase 4).
# NO se usa `estado_pago='reversed'`: en este Odoo ese estado lo pone también el FACTORING y las NC
# PARCIALES (una factura pagada por factoring o con una NC parcial NO está anulada, es venta real).
# Las anulaciones reales igual se netearían solas (factura +X y NC −X suman 0); marcarlas es solo por
# claridad. Las devoluciones/NC parciales NO se marcan: restan vía venta_neta (factura +X, NC −Y).
# Ratio ≥0.99 = la NC reversa el total de la factura (clase 4). Ver docs/GUIA_OPERACION.md §7.
#
# ⚠⚠ `nc_muerta` — LA SIMETRÍA DE LA EXCLUSIÓN (añadido 2026-08-01, caso FVX1).
# docs/guia_bi_ventas.md §6.5 fija el invariante: una nota débito que el pipeline no puede
# enlazar queda fuera de ventas, y su NC "tampoco restaba ⇒ la exclusión es simétrica".
# NO era simétrica: la NC quedaba fuera pero su EFECTO COLATERAL —haber declarado anulada a
# su factura— sobrevivía aquí, en `ncr`.
#   Caso real: FVX1 (12-jun-2024, LEOPHARMA, +159.225.366) reversada por RFEX/2025/0001 y
#   RFEX/2025/0002 (−174.115.446 c/u), que a su vez están canceladas al peso por NDEXP1 y
#   NDEXP2 del mismo día. Los cuatro documentos de enero-2025 netean CERO y la factura nunca
#   se anuló — pero `ncr` sumaba −348.230.892 (cobertura 2,187 ≥ 0,99) y la excluía. Junio-2024
#   en exportación daba −46.788.256 en vez de +112.437.110. Diagnóstico completo en
#   `proyecto pocion/intranet/docs/dashboards/reversos-mal-marcados.md`.
#
# ⚠ Se restringe a las ND **SIN `referencia`** a propósito. Con `referencia` el enlace
# documental manda (lo usa `enlazar_notas_debito`), y emparejar a ciegas por
# tercero+fecha+importe discrepa de él en 4 de 15 casos comprobables (27 % de falsos
# positivos). Sin `referencia` no hay otra vía, y hoy son solo 3 ND: NDEXP1, NDEXP2 y NDY1.
#
# ⚠ NO poner cota superior a la cobertura (una banda [0,99 , 1,01]): hay 8 facturas con la
# reversión DUPLICADA en Odoo (cobertura ~2,0 sin ND que las cancele) que hoy se excluyen
# junto con sus dos NC y netean 0, que es lo correcto. Con banda dejarían de excluirse y
# restarían el valor de la factura una vez de más.
_SQL_REVERSOS = """
WITH inv AS (
    SELECT f.factura_id, SUM(f.credito - f.debito) m
    FROM marts.fact_movimiento_contable f JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
    WHERE f.tipo_movimiento = 'out_invoice' AND c.clase_codigo = '4' GROUP BY 1
),
-- Documentos de clase 4 agregados, para emparejar ND ↔ NC.
doc AS (
    SELECT f.factura_id,
           MIN(f.tipo_movimiento)  AS tipo,
           MIN(f.tercero_id)       AS tercero_id,
           MIN(f.fecha_factura)    AS fecha_factura,
           MIN(dj.codigo)          AS diario,
           MIN(f.referencia)       AS referencia,
           SUM(f.credito - f.debito) AS m
    FROM marts.fact_movimiento_contable f
    JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
    LEFT JOIN marts.dim_diario dj ON dj.diario_id = f.diario_id
    WHERE c.clase_codigo = '4' AND f.es_venta IS TRUE
    GROUP BY f.factura_id
),
-- Notas débito sin `referencia`: el pipeline no puede enlazarlas, así que están fuera de
-- ventas (guia_bi_ventas.md §6.5). El `row_number` es para que el pareo sea UNO A UNO:
-- NDEXP1 y NDEXP2 son idénticas y sin él las dos casarían con la misma reversión.
nd_sin_ref AS (
    SELECT factura_id, tercero_id, fecha_factura, m,
           row_number() OVER (PARTITION BY tercero_id, fecha_factura, m
                              ORDER BY factura_id) AS rn
    FROM doc
    WHERE diario IN ('NDY', 'NDEXP') AND referencia IS NULL AND m > 0
),
rev AS (
    SELECT factura_id, tercero_id, fecha_factura, m,
           row_number() OVER (PARTITION BY tercero_id, fecha_factura, m
                              ORDER BY factura_id) AS rn
    FROM doc
    WHERE tipo = 'out_refund'
),
nc_muerta AS (
    SELECT r.factura_id
    FROM rev r
    JOIN nd_sin_ref d
      ON d.tercero_id = r.tercero_id
     AND d.fecha_factura = r.fecha_factura
     AND abs(d.m + r.m) <= 1        -- importe exactamente opuesto (1 peso de holgura)
     AND d.rn = r.rn                -- uno a uno
),
ncr AS (
    SELECT f.reversed_factura_id fid, SUM(f.debito - f.credito) m
    FROM marts.fact_movimiento_contable f JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
    WHERE f.tipo_movimiento = 'out_refund' AND f.reversed_factura_id IS NOT NULL
      AND c.clase_codigo = '4'
      -- Una NC ya cancelada por su ND no puede declarar anulada a su factura.
      AND f.factura_id NOT IN (SELECT factura_id FROM nc_muerta)
    GROUP BY 1
),
anul AS (
    SELECT i.factura_id FROM inv i JOIN ncr n ON n.fid = i.factura_id
    WHERE i.m > 0 AND n.m >= 0.99 * i.m           -- NC total (≥99%) = anulación real
)
-- La 3ª rama es la que cierra la simetría: la NC muerta sale de ventas igual que su ND,
-- aunque su factura ya NO esté anulada. Sin ella, FVX1 volvería con sus dos reversiones
-- vivas y restaría −348 M.
UPDATE marts.fact_movimiento_contable f
SET es_reverso = (
        (f.tipo_movimiento = 'out_invoice' AND f.factura_id        IN (SELECT factura_id FROM anul))
     OR (f.tipo_movimiento = 'out_refund'  AND f.reversed_factura_id IN (SELECT factura_id FROM anul))
     OR (f.tipo_movimiento = 'out_refund'  AND f.factura_id        IN (SELECT factura_id FROM nc_muerta)))
WHERE f.es_reverso IS DISTINCT FROM (
        (f.tipo_movimiento = 'out_invoice' AND f.factura_id        IN (SELECT factura_id FROM anul))
     OR (f.tipo_movimiento = 'out_refund'  AND f.reversed_factura_id IN (SELECT factura_id FROM anul))
     OR (f.tipo_movimiento = 'out_refund'  AND f.factura_id        IN (SELECT factura_id FROM nc_muerta)));
"""


def marcar_reversos(loader):
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SQL_REVERSOS)
        n = cur.rowcount
        conn.commit()
    logging.info(f"Reversos (anulaciones reales) marcados: {n} líneas cambiadas")


# ══ 2ª pasada de reversos: anulaciones que Odoo NO enlazó con `reversed_entry_id` ══
# `_SQL_REVERSOS` solo puede parear por `reversed_factura_id`; cuando Odoo lo deja NULL, la anulación
# total queda SIN marcar y factura+NC se quedan dentro de v_ventas_producto. Netean bien (+X y −X),
# pero inflan el BRUTO y cuentan las unidades dos veces en ambos sentidos.
# Caso real: FE7301 (09-mar-2026, 662,2M) ↔ RINV254 (28-abr-2026, −662,2M) — mismas 18 líneas, sin
# `reversed_entry_id`; su gemela del mismo día (FE7281 ↔ RINV/2026/0062) sí lo trae y sí se marcaba.
# Aquí el pareo lo aporta el puente `map_nc_factura` (conciliación de CxC). Para no barrer
# DEVOLUCIONES totales legítimas se exigen 3 condiciones a la vez:
#   1. proporcion > 0.999  → la NC se atribuye por completo a esa única factura;
#   2. cobertura clase 4 en [0.99, 1.01]  → mismo umbral que `_SQL_REVERSOS`;
#   3. firma de líneas idéntica (mismo set producto:cantidad) → es la MISMA factura al revés.
# ⚠ Debe correr DESPUÉS de `marcar_reversos` (que asigna es_reverso en ambos sentidos y desharía esto)
# y DESPUÉS de `enlazar_notas_credito` (necesita el puente ya construido). Solo marca TRUE, nunca FALSE.
_SQL_REVERSOS_PUENTE = """
WITH c4 AS (
    SELECT f.factura_id, f.tipo_movimiento, f.producto_id,
           SUM(f.credito - f.debito) AS val, SUM(f.cantidad) AS cant
    FROM marts.fact_movimiento_contable f JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
    WHERE c.clase_codigo = '4' AND f.es_venta IS TRUE
    GROUP BY 1, 2, 3
),
val AS (
    SELECT factura_id, tipo_movimiento, SUM(val) AS m FROM c4 GROUP BY 1, 2
),
firma AS (   -- huella del documento: producto:cantidad ordenado (solo líneas con producto)
    SELECT factura_id,
           string_agg(producto_id || ':' || ROUND(ABS(cant)::numeric, 3)::text, ',' ORDER BY producto_id) AS s
    FROM c4 WHERE producto_id IS NOT NULL GROUP BY 1
),
espejo AS (
    SELECT m.nc_factura_id AS nc_id, m.factura_id AS fa_id
    FROM marts.map_nc_factura m
    JOIN val   i ON i.factura_id = m.factura_id    AND i.tipo_movimiento = 'out_invoice'
    JOIN val   n ON n.factura_id = m.nc_factura_id AND n.tipo_movimiento = 'out_refund'
    JOIN firma fi ON fi.factura_id = m.factura_id
    JOIN firma fn ON fn.factura_id = m.nc_factura_id
    WHERE m.proporcion > 0.999
      AND i.m > 0
      AND (-n.m) BETWEEN 0.99 * i.m AND 1.01 * i.m   -- la NC cubre el total de la factura
      AND fi.s = fn.s                                -- mismas líneas: es la factura al revés
),
docs AS (
    SELECT nc_id AS factura_id FROM espejo UNION SELECT fa_id FROM espejo
)
UPDATE marts.fact_movimiento_contable f
SET es_reverso = TRUE
WHERE f.es_reverso IS NOT TRUE
  AND f.factura_id IN (SELECT factura_id FROM docs);
"""


def marcar_reversos_puente(loader):
    """2ª pasada: anulaciones totales que Odoo dejó sin `reversed_entry_id`, pareadas vía
    marts.map_nc_factura. Correr después de marcar_reversos() y de enlazar_notas_credito()."""
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SQL_REVERSOS_PUENTE)
        n = cur.rowcount
        conn.commit()
    logging.info(f"Reversos sin reversed_entry_id (vía puente NC): {n} líneas marcadas")


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


# ══ CATEGORÍA (tipo de cliente) consolidada en fact.categoria ══
# Dos fuentes de Odoo, ninguna basta sola (ver sql/marts/17_categoria.sql):
#   · dim_tercero.tipo_cliente (partner_type_id de la cabecera) → MANDA cuando existe.
#   · fact.canal (plan analítico 21 "Canal" = x_plan21_id)      → RELLENA cuando falta.
#     El analítico existe porque hay gastos de un cliente cargados a TERCEROS: sin él, esas líneas
#     (clases 5/6) se quedarían sin categoría y desaparecerían del análisis por cliente.
# Luego se replican las reglas de respaldo de ReportClassNew.transformar_base() EN SU MISMO ORDEN:
# el país extranjero pisa todo (incluso SHOPIFY), y CLIENTE→CALL CENTER se evalúa DESPUÉS del equipo
# (por eso Shopify gana sobre CLIENTE). Cierra con el default 'CALL CENTER' del Excel.
# Al final se normaliza el vocabulario con marts.map_categoria (editable sin tocar código).
# Nota: dim_tercero.pais guarda el NOMBRE del país ("Colombia"), no el código 'CO' del Excel.
_SQL_CATEGORIA = r"""
-- Lookups del país por NOMBRE del cliente (marts.map_cliente_pais). Se resuelven UNA vez sobre
-- conjuntos pequeños (terceros y valores distintos del analítico) para no hacer un ILIKE por cada
-- una de las millones de líneas del hecho, y para que el LEFT JOIN no multiplique filas.
WITH cpais_t AS (            -- tercero_id → país (por el nombre del tercero)
    SELECT t.tercero_id, MIN(m.pais) AS pais
    FROM marts.dim_tercero t
    JOIN marts.map_cliente_pais m ON t.nombre ILIKE m.cliente_patron
    GROUP BY t.tercero_id
),
cpais_a AS (                 -- cliente_analitico → país (por el nombre dentro del analítico)
    SELECT d.cliente_analitico, MIN(m.pais) AS pais
    FROM (SELECT DISTINCT cliente_analitico FROM marts.fact_movimiento_contable
          WHERE cliente_analitico IS NOT NULL) d
    JOIN marts.map_cliente_pais m ON d.cliente_analitico ILIKE m.cliente_patron
    GROUP BY d.cliente_analitico
),
base AS (
    SELECT f.linea_id, f.es_venta,
           COALESCE(t.tipo_cliente, f.canal) AS cat0,   -- tipo_cliente manda; el analítico rellena
           t.tipo_cliente, t.etiqueta, t.pais, f.equipo,
           cc.codigo AS centro_codigo, f.cliente_analitico,
           -- País del CLIENTE de la línea: el NOMBRE manda sobre el código del plan 22, así un código
           -- mal puesto en -CO no saca la línea de exportaciones (al inicio el país quedaba en Colombia).
           COALESCE(
               ca.pais, ct.pais,
               CASE substring(f.cliente_analitico from '\[CLI-[A-Z]+-([A-Z]{2})[0-9]*\]')
                    WHEN 'EC' THEN 'Ecuador'
                    WHEN 'PE' THEN 'Peru'
                    WHEN 'US' THEN 'United States'
                    WHEN 'DO' THEN 'República Dominicana'
                    WHEN 'CO' THEN 'Colombia'
               END
           ) AS pais_cliente
    FROM marts.fact_movimiento_contable f
    LEFT JOIN marts.dim_tercero      t  ON t.tercero_id      = f.tercero_id
    LEFT JOIN marts.dim_centro_costo cc ON cc.centro_costo_id = f.centro_costo_id
    LEFT JOIN cpais_t ct ON ct.tercero_id        = f.tercero_id
    LEFT JOIN cpais_a ca ON ca.cliente_analitico = f.cliente_analitico
),
resuelta AS (
    SELECT linea_id, pais,
           CASE
               -- EXPORTACION = VENTAS a clientes EXTERIOR + gastos de exportación (centros [EXPO])
               -- + TODO lo marcado con el plan 22 "Cliente" de un cliente del exterior (sufijo <> CO):
               -- así entran los costos y los gastos de terceros (logística, proveedores) que la
               -- distribución analítica asocia a esa exportación, aunque el tercero sea colombiano.
               -- es_venta evita meter gastos de PROVEEDORES extranjeros (AWS, Odoo Inc, agencias de
               -- marketing) que también están etiquetados EXTERIOR pero no son exportación.
               -- Línea marcada con el plan 22: es exportación si el país del cliente no es Colombia.
               -- El alcance lo sigue dando el analítico (no el tercero) para no arrastrar reembolsos,
               -- diferencia en cambio, etc.; el NOMBRE solo decide el PAÍS (blindaje del código).
               WHEN cliente_analitico IS NOT NULL AND pais_cliente IS NOT NULL
                    AND pais_cliente <> 'Colombia' THEN 'EXPORTACION'
               WHEN es_venta AND (tipo_cliente = 'EXTERIOR' OR etiqueta ILIKE '%EXTERIOR%') THEN 'EXPORTACION'
               WHEN centro_codigo = 'EXPO'                  THEN 'EXPORTACION'
               WHEN equipo = 'Shopify'                      THEN 'SHOPIFY'
               WHEN equipo = 'Punto de venta'               THEN 'CALL CENTER'
               WHEN cat0   = 'CLIENTE'                      THEN 'CALL CENTER'
               WHEN cat0 IS NOT NULL                        THEN cat0
               ELSE 'CALL CENTER'
           END AS cat
    FROM base
)
UPDATE marts.fact_movimiento_contable f
SET categoria = COALESCE(m.categoria_bi, r.cat),         -- normalización (map_categoria)
    pais      = r.pais                                   -- país estricto de la línea (dim_tercero.pais)
FROM resuelta r
LEFT JOIN marts.map_categoria m ON m.categoria_origen = r.cat
WHERE f.linea_id = r.linea_id
  AND (f.categoria IS DISTINCT FROM COALESCE(m.categoria_bi, r.cat)
       OR f.pais   IS DISTINCT FROM r.pais);
"""


# ══ Backfill de cliente_analitico (plan 22 "Cliente") desde account.analytic.line ══
# El plan 22 vive en la columna x_plan22_id de la línea analítica (una por distribución). Enlaza con
# la línea contable por move_line_id → UPDATE directo del hecho. Barato (~4k filas) y idempotente.
# Hacia adelante lo captura construir_hecho; esto rellena lo ya cargado (patrón del backfill de equipo).
def backfill_cliente_analitico(od, loader):
    pares, off = [], 0
    while True:
        al = od.search_read("account.analytic.line", [["x_plan22_id", "!=", False]],
                            ["move_line_id", "x_plan22_id"], limit=20000, offset=off)
        if not al:
            break
        pares += [(m2o_id(a.get("move_line_id")), m2o_nombre(a.get("x_plan22_id")))
                  for a in al if m2o_id(a.get("move_line_id"))]
        off += len(al)
    if not pares:
        logging.info("  cliente_analitico: sin líneas plan 22")
        return
    with loader.get_connection() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            "UPDATE marts.fact_movimiento_contable f SET cliente_analitico = v.cli "
            "FROM (VALUES %s) AS v(id, cli) "
            "WHERE f.linea_id = v.id AND f.cliente_analitico IS DISTINCT FROM v.cli",
            pares, page_size=5000)
        n = cur.rowcount
        conn.commit()
    logging.info(f"  cliente_analitico (plan 22): {len(pares)} líneas, {n} actualizadas")


def backfill_total_con_impuesto(od, loader, solo_faltantes=True):
    """
    Rellena `total_con_impuesto` (price_total) y `moneda` en las líneas YA cargadas del hecho.

    Hace falta porque el watermark del incremental solo vuelve a escribir las líneas cuyo
    `write_date` cambió: las históricas no se tocan nunca y se quedarían con la columna en NULL.
    Las líneas NUEVAS ya llegan completas (ver LINE_FIELDS / construir_hecho).

    ⚠ ALCANCE: solo `es_venta` + clase 4 (las que consumen v_ventas_producto / v_ventas_explotada)
    = 585.541 de las 4.414.170 del hecho. En las demás (impuesto, CxC, asientos) Odoo devuelve
    price_total = 0: rellenarlas serían 3,8 M de UPDATEs para guardar ceros. Quedan NULL a
    propósito, y las vistas de ventas nunca las miran.

    ⚠ NO se llama desde main(): es de UNA SOLA VEZ, por `--backfill-iva`. En el cron haría que
    cada tick de 15 min releyera ~585 k líneas de Odoo para no cambiar nada.

    `solo_faltantes=False` fuerza el re-relleno de todas (por si Odoo corrigió un impuesto).
    """
    filtro = "AND f.total_con_impuesto IS NULL" if solo_faltantes else ""
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT f.linea_id
            FROM marts.fact_movimiento_contable f
            JOIN marts.dim_cuenta c ON c.cuenta_id = f.cuenta_id
            WHERE f.es_venta IS TRUE AND c.clase_codigo = '4' {filtro}
            ORDER BY f.linea_id
        """)
        ids = [r[0] for r in cur.fetchall()]

    logging.info(f"backfill total_con_impuesto: {len(ids):,} líneas de venta por rellenar")
    if not ids:
        return 0

    actualizadas, leidas = 0, 0
    for i in range(0, len(ids), PAGINA):
        chunk = ids[i:i + PAGINA]
        lns = od.search_read("account.move.line", [["id", "in", chunk]],
                             ["id", "price_total", "currency_id"], limit=len(chunk))
        leidas += len(lns)
        pares = [(as_int(l["id"]), l.get("price_total"), m2o_nombre(l.get("currency_id")))
                 for l in lns if as_int(l.get("id"))]
        if not pares:
            continue
        with loader.get_connection() as conn:
            cur = conn.cursor()
            psycopg2.extras.execute_values(
                cur,
                "UPDATE marts.fact_movimiento_contable f "
                "SET total_con_impuesto = v.total::numeric, "
                "    moneda             = COALESCE(v.mon::varchar, f.moneda) "
                "FROM (VALUES %s) AS v(id, total, mon) "
                "WHERE f.linea_id = v.id::bigint",
                pares, page_size=5000)
            actualizadas += cur.rowcount
            conn.commit()
        logging.info(f"  {min(i + PAGINA, len(ids)):,}/{len(ids):,} pedidas · "
                     f"{leidas:,} leídas de Odoo · {actualizadas:,} actualizadas")

    if leidas < len(ids):
        # No es un error: una línea puede haberse borrado en Odoo y seguir en el hecho (el
        # watermark no ve los hard-delete; por eso el DW se recrea ~2×/mes con --rebuild).
        logging.warning(f"  {len(ids) - leidas:,} líneas del hecho ya no existen en Odoo "
                        f"(quedan con total_con_impuesto NULL)")
    logging.info(f"backfill total_con_impuesto: {actualizadas:,} líneas actualizadas")
    return actualizadas


# ══ Puente NOTA CRÉDITO → FACTURA original (para que la NC reste en el mes de la factura) ══
# CASCADA DE EVIDENCIA (de la más fuerte a la más débil). El método usado queda en `metodo_enlace`:
#   1. `reversed_entry_id`  — Odoo dice explícitamente qué factura reversa. Evidencia directa.
#   2. `ref`                — el número de la factura aparece en la referencia de la NC (mismo cliente
#                             y un único candidato válido; si hay varios es ambiguo → se pasa al 3).
#   3. `conciliacion`       — `account.partial.reconcile`. Es lo único disponible para la mayoría
#                             (NCR1858 tiene `ref` y `reversed_entry_id` NULL y aun así concilia
#                             49.944.031 contra FEVY80693), pero es el más débil: conciliar significa
#                             "se aplicó contra" y no siempre "corrige a" (una NC puede abonarse a la
#                             factura abierta más antigua). Con la cascada, ese riesgo queda acotado a
#                             las NC sin ninguna otra evidencia y es auditable por `metodo_enlace`.
# Una NC puede corregir varias facturas: se reparte por `proporcion` (suma 1 por NC); los métodos 1 y 2
# apuntan a una sola factura → proporcion 1. Ver sql/marts/19_nc_factura.sql.
_RE_NUM_DOC = re.compile(r"[A-Za-z]{1,6}[0-9][A-Za-z0-9/\-]*")


def _facturas_destino(od, move_ids):
    """Lee los account.move dados y devuelve solo los válidos como FACTURA destino de una NC:
    `out_invoice`, con `invoice_date` y que NO sean NOTA DÉBITO.
    ⚠ Las notas débito también son `out_invoice`: solo se distinguen por el DIARIO ("Nota Debito
    Nacional Yumbo", "Nota Debito Exportacion"). Se excluyen para que la NC se atribuya a la FACTURA
    real (ej. NCR1858 concilia 49,9M con FEVY80693 y 2,3M con NDY21)."""
    move_ids = [i for i in move_ids if i]
    if not move_ids:
        return {}
    moves = {m["id"]: m for m in od.read("account.move", list(set(move_ids)),
                                        ["move_type", "invoice_date", "journal_id", "partner_id"])}
    diarios = {jid for jid in {m2o_id(m.get("journal_id")) for m in moves.values()} if jid}
    if diarios:
        nd = od.read("account.journal", list(diarios), ["name"])
        diarios = {j["id"] for j in nd if _norm(j.get("name")).startswith("nota debito")}
    return {i: m for i, m in moves.items()
            if m.get("move_type") == "out_invoice" and m.get("invoice_date")
            and m2o_id(m.get("journal_id")) not in diarios}


def enlazar_notas_credito(od, loader, desde="2024-01-01"):
    ncs, off = [], 0
    while True:
        page = od.search_read("account.move",
                              [["move_type", "=", "out_refund"], ["state", "=", "posted"],
                               ["invoice_date", ">=", desde]],
                              ["id", "invoice_date", "reversed_entry_id", "ref", "partner_id"],
                              limit=5000, offset=off, context=CTX_ALL)
        if not page:
            break
        ncs += page
        off += len(page)
    if not ncs:
        logging.info("  nc->factura: sin notas crédito de venta")
        return
    nc_ids = [n["id"] for n in ncs]
    enlace = {}   # nc_id -> {factura_id: peso}
    metodo = {}   # nc_id -> 'reversed_entry' | 'ref' | 'conciliacion'

    # ── Método 1: `reversed_entry_id` (Odoo dice qué factura reversa) ───────────────────────────
    rev = {n["id"]: m2o_id(n.get("reversed_entry_id")) for n in ncs if m2o_id(n.get("reversed_entry_id"))}
    val_rev = _facturas_destino(od, list(rev.values()))
    for nc_id, fid in rev.items():
        if fid in val_rev:
            enlace[nc_id] = {fid: 1.0}
            metodo[nc_id] = "reversed_entry"

    # ── Método 2: número de factura dentro de `ref` (mismo cliente, candidato único) ────────────
    por_token, pendientes = {}, [n for n in ncs if n["id"] not in enlace]
    for n in pendientes:
        for tok in set(_RE_NUM_DOC.findall(n.get("ref") or "")):
            por_token.setdefault(tok, []).append(n["id"])
    if por_token:
        cand, off = [], 0
        toks = list(por_token)
        while off < len(toks):                      # por lotes: el dominio `in` no debe crecer sin fin
            lote = toks[off:off + 500]
            cand += od.search_read("account.move",
                                   [["name", "in", lote], ["move_type", "=", "out_invoice"],
                                    ["state", "=", "posted"]],
                                   ["id", "name", "partner_id"], context=CTX_ALL)
            off += len(lote)
        val_ref = _facturas_destino(od, [c["id"] for c in cand])
        por_nombre = {}
        for c in cand:
            if c["id"] in val_ref:
                por_nombre.setdefault(c["name"], []).append(c["id"])
        cliente_nc = {n["id"]: m2o_id(n.get("partner_id")) for n in ncs}
        halla = {}                                  # nc_id -> set de facturas candidatas
        for tok, ncs_tok in por_token.items():
            for fid in por_nombre.get(tok, []):
                for nc_id in ncs_tok:
                    if m2o_id(val_ref[fid].get("partner_id")) == cliente_nc.get(nc_id):
                        halla.setdefault(nc_id, set()).add(fid)
        for nc_id, fids in halla.items():
            if len(fids) == 1:                      # ambiguo (>1) → se deja para la conciliación
                enlace[nc_id] = {next(iter(fids)): 1.0}
                metodo[nc_id] = "ref"

    # ── Método 3: conciliación de CxC (lo único disponible para la mayoría de las NC) ───────────
    fechas = {fid: str(m["invoice_date"])[:10] for fid, m in val_rev.items()}
    if por_token:
        fechas.update({fid: str(m["invoice_date"])[:10] for fid, m in val_ref.items()})

    restantes = [i for i in nc_ids if i not in enlace]
    if restantes:
        # Líneas de CxC de las NC y sus conciliaciones (matched_debit_ids = contra qué débito concilian).
        lns, off = [], 0
        while True:
            page = od.search_read("account.move.line",
                                  [["move_id", "in", restantes], ["account_type", "=", "asset_receivable"]],
                                  ["move_id", "matched_debit_ids"], limit=20000, offset=off, context=CTX_ALL)
            if not page:
                break
            lns += page
            off += len(page)
        linea_a_nc = {l["id"]: m2o_id(l.get("move_id")) for l in lns}
        pr_ids = [p for l in lns for p in (l.get("matched_debit_ids") or [])]
        if pr_ids:
            pr = od.read("account.partial.reconcile", pr_ids,
                         ["amount", "debit_move_id", "credit_move_id"])
            # La contrapartida (débito) es una LÍNEA: hay que subir a su move y validarlo como factura.
            deb_ids = list({m2o_id(p["debit_move_id"]) for p in pr})
            deb_move = {l["id"]: m2o_id(l.get("move_id"))
                        for l in od.read("account.move.line", deb_ids, ["move_id"])}
            facturas = _facturas_destino(od, list(deb_move.values()))
            fechas.update({fid: str(m["invoice_date"])[:10] for fid, m in facturas.items()})
            acum = {}   # nc_id -> {factura_id: monto conciliado}
            for p in pr:
                nc_id = linea_a_nc.get(m2o_id(p["credit_move_id"]))
                fid = deb_move.get(m2o_id(p["debit_move_id"]))
                if nc_id and fid in facturas:
                    acum.setdefault(nc_id, {})[fid] = \
                        acum.setdefault(nc_id, {}).get(fid, 0) + (p.get("amount") or 0)
            for nc_id, facs in acum.items():
                total = sum(facs.values())
                if total > 0:
                    enlace[nc_id] = {fid: monto / total for fid, monto in facs.items()}
                    metodo[nc_id] = "conciliacion"

    if not enlace:
        logging.info("  nc->factura: ninguna NC se pudo enlazar a una factura de venta")
        return
    filas = [{"nc_factura_id": nc_id, "factura_id": fid, "proporcion": peso,
              "fecha_venta": fechas[fid], "metodo_enlace": metodo[nc_id]}
             for nc_id, facs in enlace.items() for fid, peso in facs.items() if fid in fechas]
    upsert(loader, pd.DataFrame(filas), "map_nc_factura", ["nc_factura_id", "factura_id"],
           reemplazar=True)   # TRUNCATE+INSERT atómico: el puente nunca se ve vacío
    por_metodo = pd.Series([metodo[n] for n in enlace]).value_counts().to_dict()
    logging.info(f"  nc->factura: {len(enlace)} notas crédito enlazadas ({len(filas)} pares) "
                 f"por método {por_metodo}")


# ══ Puente NOTA DÉBITO → FACTURA que revive ══
# Una ND NO es venta ("ventas menos devoluciones"), SALVO cuando ANULA una NOTA CRÉDITO: si la
# devolución se anuló, no hubo devolución → hay que reponer ese valor EN EL MES DE LA FACTURA original.
# Cadena ND → NC → FACTURA, vía el `ref` de la ND ("<numero_documento>, <motivo>", formato fijo):
#   FE7281 (09-mar-2026) ← RINV/2026/0062 la anula ← NDY1 (24-abr) anula la NC → NDY1 va a MARZO.
# Las ND que apuntan a una FACTURA (cargo extra, ej. NDY4 "FE7281, Ajuste por precio") o sin `ref` NO
# entran → quedan fuera de ventas. Ver sql/marts/25_nd_factura.sql.
CODIGOS_ND = ("NDY", "NDEXP")


def enlazar_notas_debito(od, loader):
    """Puebla marts.map_nd_factura. Correr DESPUÉS de enlazar_notas_credito (usa map_nc_factura)."""
    diarios = od.search_read("account.journal", [["code", "in", list(CODIGOS_ND)]], ["id"])
    if not diarios:
        logging.info("  nd->factura: no hay diarios de nota débito")
        return
    nds, off = [], 0
    while True:
        page = od.search_read("account.move",
                              [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
                               ["journal_id", "in", [d["id"] for d in diarios]]],
                              ["id", "ref", "partner_id", "company_id"],
                              limit=5000, offset=off, context=CTX_ALL)
        if not page:
            break
        nds += page
        off += len(page)
    # Token referenciado = lo anterior a la primera coma del `ref`.
    ref_de = {n["id"]: (n.get("ref") or "").split(",")[0].strip() for n in nds}
    tokens = sorted({t for t in ref_de.values() if t})
    if not tokens:
        logging.info("  nd->factura: ninguna nota débito trae `ref`")
        return

    # Resolver los tokens a NOTAS CRÉDITO (out_refund). Si el token es una factura → NO es revivir.
    ncs, off = [], 0
    while off < len(tokens):
        lote = tokens[off:off + 500]
        ncs += od.search_read("account.move",
                              [["name", "in", lote], ["move_type", "=", "out_refund"],
                               ["state", "=", "posted"]],
                              ["id", "name", "partner_id", "company_id", "reversed_entry_id"],
                              context=CTX_ALL)
        off += len(lote)
    if not ncs:
        logging.info("  nd->factura: ninguna nota débito anula una nota crédito")
        return
    por_nombre = {}
    for c in ncs:
        por_nombre.setdefault(c["name"], []).append(c)

    # Factura de cada NC: 1º el puente NC (la de mayor proporcion), 2º su propio reversed_entry_id.
    puente = loader.consultar("""
        SELECT DISTINCT ON (nc_factura_id) nc_factura_id, factura_id, fecha_venta
        FROM marts.map_nc_factura ORDER BY nc_factura_id, proporcion DESC
    """)
    del_puente = {} if puente is None else {
        int(r.nc_factura_id): (int(r.factura_id), str(r.fecha_venta)[:10])
        for r in puente.itertuples()}
    faltan = [m2o_id(c["reversed_entry_id"]) for c in ncs
              if c["id"] not in del_puente and m2o_id(c["reversed_entry_id"])]
    val_rev = _facturas_destino(od, faltan)

    filas = []
    for nd in nds:
        tok = ref_de[nd["id"]]
        for c in por_nombre.get(tok, []):
            # Mismo cliente y misma empresa: el número de documento no es único entre empresas.
            if (m2o_id(c.get("partner_id")) != m2o_id(nd.get("partner_id"))
                    or m2o_id(c.get("company_id")) != m2o_id(nd.get("company_id"))):
                continue
            if c["id"] in del_puente:
                fid, fecha = del_puente[c["id"]]
                met = "puente_nc"
            else:
                fid = m2o_id(c["reversed_entry_id"])
                if fid not in val_rev:
                    continue
                fecha, met = str(val_rev[fid]["invoice_date"])[:10], "reversed_entry"
            filas.append({"nd_factura_id": nd["id"], "nc_factura_id": c["id"],
                          "factura_id": fid, "fecha_venta": fecha, "metodo_enlace": met})
            break
    if not filas:
        logging.info("  nd->factura: ninguna nota débito se pudo enlazar a una factura")
        return
    upsert(loader, pd.DataFrame(filas), "map_nd_factura", ["nd_factura_id"],
           reemplazar=True)   # TRUNCATE+INSERT atómico: el puente nunca se ve vacío
    por_metodo = pd.Series([f["metodo_enlace"] for f in filas]).value_counts().to_dict()
    logging.info(f"  nd->factura: {len(filas)} notas débito son venta (de {len(nds)}) "
                 f"por método {por_metodo}; el resto queda FUERA de ventas")


def consolidar_categoria(loader):
    """Puebla fact.categoria (tipo de cliente) desde tipo_cliente + analítico plan 21, con las
    reglas de respaldo del Excel y normalizado por marts.map_categoria. En el mismo UPDATE denormaliza
    fact.pais = dim_tercero.pais (país estricto por línea)."""
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SQL_CATEGORIA)
        n = cur.rowcount
        conn.commit()
    logging.info(f"Categoría consolidada: {n} líneas actualizadas")


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


def main(modo, desde, hasta=None, cierre=True):
    """`cierre=False` = corrida LIGERA: solo lo que escala con el delta (dimensiones por watermark +
    cargar_hecho). Salta los pasos cuyo coste es FIJO (full scans del hecho, TRUNCATE+rebuild de los
    puentes NC/ND y de dim_kit_componente, full scan de product.product en Odoo), que no aportan nada
    si se repiten cada 15 minutos. Lo usa run_dw.py: completa en el tick de la hora, ligera en los otros.
    ⚠ En una corrida ligera las líneas nuevas quedan SIN `categoria`, sin `es_reverso` y sin puente
    NC/ND resueltos hasta el siguiente cierre."""
    db, uid, pw, models = conectar_odoo()
    od = Odoo(db, uid, pw, models)
    loader = DBLoader()

    an_plan, an_nombre, plan_rol, clasificar, nombre_puc = cargar_catalogos_pequenos(od, loader)

    # Refresco de dimensiones (clientes/productos/vendedores) por su propio write_date.
    # full/rebuild → refresco total; incremental/dims → solo cambios.
    refrescar_dimensiones(od, loader, full=(modo in ("full", "rebuild")))
    if cierre:
        cargar_kits(od, loader)   # dim_kit_componente (BOM phantom) para v_ventas_explotada
        enriquecer_nombre_comercial(od, loader)   # dim_producto.nombre_comercial (product.template.name)
    if modo == "dims":
        logging.info("OK DIMS: catálogos y dimensiones refrescados.")
        return

    if modo == "incremental":
        marca_l = get_watermark(loader, "account.move.line")
        dom = [["parent_state", "=", "posted"]]
        if marca_l:
            dom.append(["write_date", ">", marca_l])
        logging.info(f"INCREMENTAL (líneas > {marca_l})")
        total_h, mw_h = cargar_hecho(od, loader, dom, an_plan, an_nombre, plan_rol,
                                     clasificar, nombre_puc)
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
            t, mw = cargar_hecho(od, loader, dom, an_plan, an_nombre, plan_rol,
                                 clasificar, nombre_puc, catalogos_completos=True)
            total_h += t
            if mw and (mw_h is None or mw > mw_h):
                mw_h = mw
            logging.info(f"── Año {anio}: {t} líneas (acumulado {total_h}) ──")

    if mw_h:
        set_watermark(loader, "account.move.line", mw_h, total_h)

    # ── Pasos de CIERRE. Coste FIJO (no dependen del delta) → solo en la corrida completa. ──
    if cierre:
        marcar_reversos(loader)      # ventas: excluir reversos totales
        aplicar_correcciones(loader)  # limpieza de datos mal registrados en Odoo
        canonicalizar_puc(loader)     # unifica códigos 8 vs 9 díg de la misma cuenta (no destructivo)
        backfill_cliente_analitico(od, loader)  # plan 22 "Cliente" en líneas ya cargadas
        enlazar_notas_credito(od, loader)       # NC → factura original (la NC resta en el mes de la factura)
        enlazar_notas_debito(od, loader)        # ⚠ tras el puente NC: ND que anula una NC = venta revivida
        marcar_reversos_puente(loader)          # ⚠ tras el puente: anulaciones sin reversed_entry_id
        consolidar_categoria(loader)  # categoría (incl. EXPORTACION) + país por línea
    else:
        logging.info("Corrida LIGERA: se omiten los pasos de cierre (van en el tick de la hora).")

    logging.info(f"OK {modo.upper()}{'' if cierre else ' (ligera)'} completado: hecho={total_h} líneas.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--full", action="store_true", help="carga histórica completa (sin truncar)")
    g.add_argument("--incremental", action="store_true", help="solo cambios (write_date > marca)")
    g.add_argument("--rebuild", action="store_true",
                   help="recreación por rango: DELETE + recarga (por defecto el año actual)")
    g.add_argument("--dims", action="store_true",
                   help="solo refrescar catálogos y dimensiones (sin hechos)")
    g.add_argument("--backfill-iva", action="store_true",
                   help="rellena total_con_impuesto/moneda en las líneas de venta YA cargadas "
                        "(price_total de Odoo). UNA SOLA VEZ: las líneas nuevas ya llegan con el "
                        "dato. No lo corre el cron.")
    ap.add_argument("--rehacer-iva", action="store_true",
                    help="con --backfill-iva: re-lee TODAS las líneas de venta, no solo las que "
                         "tienen total_con_impuesto en NULL.")
    ap.add_argument("--desde", default=None,
                    help="fecha mínima YYYY-MM-DD (--rebuild: default año actual; --full: opcional)")
    ap.add_argument("--hasta", default=None,
                    help="fecha máxima YYYY-MM-DD (acota el rango en --rebuild/--full)")
    ap.add_argument("--sin-cierre", action="store_true",
                    help="corrida LIGERA: solo dimensiones y hecho nuevo, sin los pasos de cierre "
                         "(reversos, puentes NC/ND, categoría, PUC). Es lo que corre el cron en los "
                         "ticks que no son la hora en punto.")
    args = ap.parse_args()
    if args.backfill_iva:
        # Operación de una sola vez, aparte del pipeline: no toca dimensiones ni hecho nuevo.
        db, uid, pw, models = conectar_odoo()
        backfill_total_con_impuesto(Odoo(db, uid, pw, models), DBLoader(),
                                    solo_faltantes=not args.rehacer_iva)
    else:
        modo = ("rebuild" if args.rebuild else "full" if args.full
                else "dims" if args.dims else "incremental")
        main(modo, args.desde, args.hasta, cierre=not args.sin_cierre)
