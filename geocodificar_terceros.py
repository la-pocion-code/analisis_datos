"""
geocodificar_terceros.py — Coordenada, barrio y comuna de los clientes, con GEOAPIFY, acumulando en
una caché propia sin pasar del cupo gratis. Fase 3 de `docs/georreferenciacion_clientes.md`.

    python geocodificar_terceros.py --seco                 # cuenta pendientes y llamadas, SIN red
    python geocodificar_terceros.py --seco --muestra 20    # y enseña 20 consultas tal como saldrían
    python geocodificar_terceros.py --piloto 300           # 300 al azar (reproducible) + informe
    python geocodificar_terceros.py                        # lo pendiente, hasta el tope del día
    python geocodificar_terceros.py --segmento shopify2026 # amplía el universo (ver SEGMENTOS)
    python geocodificar_terceros.py --informe              # calidad de lo ya geocodificado, SIN red
    python geocodificar_terceros.py --reclasificar         # regla de estado nueva sobre lo guardado
    python geocodificar_terceros.py --segmento todos --sin-llamadas  # barrio ESCRITO para todos, gratis

⛔ POR QUÉ NO GOOGLE (2026-10-10). Sus términos solo dejan conservar la latitud/longitud de la
Geocoding API 30 días y prohíben armar una base propia con ella, sea cual sea el formato (también un
CSV local). Geoapify lo dice por escrito: «Our services are based on Open Data. So we do not
restrict you from storing results» — con la atribución a OpenStreetMap junto al dato.

CUPO: el plan gratis da 3.000 créditos/día (1 por consulta). El tope diario vive en
`marts.etl_control` (modelo `geocoder:geoapify:AAAA-MM-DD`) y se descuenta ANTES de llamar, así
que dos corridas el mismo día no lo pasan juntas. Por defecto se usan 2.900.

LO QUE SE PAGA ES LA DIRECCIÓN, NO EL CLIENTE: la caché `bi_geo_direccion` va por la consulta
normalizada, así que dos clientes con la misma dirección cuestan una llamada, y una corrida repetida
cuesta cero.

⚠ DATO PERSONAL: la coordenada de un consumidor se queda en el almacén. `v_lk_tercero_geo` solo la
publica para los listables, y la intranet agrega todo lo demás (ver 39_tercero_geo.sql).

⚠ VALIDACIÓN: un resultado que cae FUERA del municipio del cliente se guarda con
`estado='fuera_de_municipio'` y NO se usa: ubicar a alguien en otra ciudad es peor que no ubicarlo.
"""
import os
import re
import sys
import time
import math
import hashlib
import logging
import argparse
import unicodedata
from collections import Counter
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import requests
from dotenv import load_dotenv

from classes.db_loader import DBLoader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

PROVEEDOR = "geoapify"
URL = "https://api.geoapify.com/v1/geocode/search"
CUPO_DIARIO = 3000          # créditos del plan gratis
TOPE_POR_DEFECTO = 2900     # margen para pruebas a mano el mismo día
POR_SEGUNDO = 4             # el plan gratis admite 5/s
TIMEOUT_S = 15
REINTENTOS = 3
CONFIANZA_MINIMA = 0.5

# El universo, por prioridad. William eligió empezar por los recompradores (2026-10-10).
SEGMENTOS = {
    # Compraron por Shopify en 2 o más meses de 2026: 6.753 direcciones, el 29 % de esa venta.
    "recompradores": """
        SELECT tercero_id FROM marts.mv_clientes_geo_mes
        WHERE anio = EXTRACT(YEAR FROM CURRENT_DATE) AND upper(categoria) = 'SHOPIFY'
        GROUP BY tercero_id HAVING COUNT(DISTINCT periodo_aaaamm) >= 2""",
    # Todos los de Shopify del año en curso: ~45.700 direcciones.
    "shopify2026": """
        SELECT DISTINCT tercero_id FROM marts.mv_clientes_geo_mes
        WHERE anio = EXTRACT(YEAR FROM CURRENT_DATE) AND upper(categoria) = 'SHOPIFY'""",
    # Todo cliente con venta: ~115.800 direcciones.
    "todos": "SELECT DISTINCT tercero_id FROM marts.mv_clientes_geo_mes",
}

# El orden en que se gasta el cupo con `--segmento auto`: primero lo que más importa. Cuando los
# tres estén geocodificados, lo único pendiente de «todos» serán los clientes NUEVOS (~100-200 al
# día), así que el mismo cron pasa solo a mantenimiento sin tocar nada.
PRIORIDAD = ("recompradores", "shopify2026", "todos")


def por_prioridad(lo, geo, llaves):
    """Ordena las llaves pendientes: las de los recompradores, luego Shopify del año, luego el resto."""
    rango_tercero = {}
    for i, seg in enumerate(PRIORIDAD[:-1]):
        df = lo.consultar(SEGMENTOS[seg])
        for t in (df.tercero_id if df is not None and not df.empty else []):
            rango_tercero.setdefault(int(t), i)
    rango_llave = {}
    for r in geo.itertuples():
        rango = rango_tercero.get(int(r.tercero_id), len(PRIORIDAD) - 1)
        rango_llave[r.llave] = min(rango, rango_llave.get(r.llave, rango))
    return sorted(llaves, key=lambda k: (rango_llave.get(k, len(PRIORIDAD) - 1), k))


# ── Normalización de la dirección colombiana ──────────────────────────────────

_TIPOS = [
    (r"AV(?:ENIDA)?\s*(?:CRA|CR|KR|CARRERA)|AK", "Avenida Carrera"),
    (r"AV(?:ENIDA)?\s*(?:CL|CLL|CALLE)|AC", "Avenida Calle"),
    (r"CALLE|CLL|CL", "Calle"),
    (r"CARRERA|CARERA|CRA|KRA|CR|KR|K", "Carrera"),
    (r"TRANSVERSAL|TRANSV|TV|TR", "Transversal"),
    (r"DIAGONAL|DIAG|DG", "Diagonal"),
    (r"AVENIDA|AV", "Avenida"),
    (r"CIRCULAR|CIRC|CQ", "Circular"),
]
_RE_TIPO = re.compile(r"^(" + "|".join(p for p, _ in _TIPOS) + r")\.?\s*(?=\d)", re.I)
_NUM = r"\d+\s?[A-Z]?(?:\s?BIS)?(?:\s?[A-Z](?=\s|-|#|$))?"
_RE_PLACA = re.compile(
    rf"^(?P<via>{_NUM})\s*(?:#|N[O°º]?\.?|NUM(?:ERO)?\.?)?\s*(?P<cruce>{_NUM})\s*[-\s]\s*(?P<placa>\d+)",
    re.I)

# Lo que viene detrás de la placa no ayuda al geocodificador (y algunas palabras lo confunden).
_CORTE = re.compile(
    r"\b(AP|APT|APTO|APARTAMENTO|TO|TORRE|INT|INTERIOR|CASA|CS|LOCAL|LC|PISO|ED|EDIF|EDIFICIO|"
    r"CONJ|CONJUNTO|BL|BLOQUE|MZ|MANZANA|LT|LOTE|URB|URBANIZACION|OF|OFICINA|BRR|BARRIO|BARR|"
    r"B/|ET|ETAPA|SECTOR|CC|CENTRO COMERCIAL)\b", re.I)

# El barrio que escribió el cliente: «… BRR CENTRO», «… BARRIO LA FLORA AP 2».
_RE_BARRIO = re.compile(
    r"(?:\bBRR|\bBARRIO|\bBARR|\bB/)\.?\s+(?P<b>[A-ZÁÉÍÓÚÑÜ0-9 .]+?)\s*(?=\b(?:AP|APT|APTO|"
    r"APARTAMENTO|TO|TORRE|INT|INTERIOR|CASA|CS|LOCAL|LC|PISO|ED|EDIF|EDIFICIO|CONJ|CONJUNTO|BL|"
    r"BLOQUE|MZ|MANZANA|LT|LOTE|ET|ETAPA|SECTOR|CC)\b|,|$)",
    re.I)


def _plano(s):
    """Minúsculas sin tildes ni puntuación. SOLO para comparar."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _sin_sufijo(depto):
    texto = str(depto or "")
    return texto[:texto.rfind(" (")] if texto.endswith(")") and " (" in texto else texto


def barrio_escrito(direccion):
    """El barrio que el cliente escribió en su dirección, o None. Medido: está en el 13,9 % de
    los clientes con venta (2026-10-10)."""
    m = _RE_BARRIO.search(" ".join(str(direccion or "").split()))
    if not m:
        return None
    b = " ".join(m.group("b").replace(".", " ").split()).strip()
    return b.title() if len(b) >= 3 and any(c.isalpha() for c in b) else None


def via_normalizada(direccion):
    """'CR 98 16F 09 AP 205 BRR FONTIBON' → 'Carrera 98 # 16F-09'. None si no hay placa reconocible
    (entonces la dirección no se geocodifica: daría el centro del municipio y parecería precisa)."""
    s = " ".join(str(direccion or "").upper().split())
    # «N», «NO», «N°» suelto entre dos números es «número», no la letra de la vía: sin esto,
    # 'CRA 7 N 7 BIS 26' salía como 'Carrera 7N' (medido en el piloto en seco).
    s = re.sub(r"(\d[A-Z]?)\s+N[O°º]?\.?\s+(?=\d)", r"\1 # ", s)
    m = _CORTE.search(s)
    if m:
        s = s[:m.start()].strip()
    t = _RE_TIPO.match(s)
    if not t:
        return None
    nombre = next(n for p, n in _TIPOS if re.fullmatch(p, t.group(1).replace(".", ""), re.I))
    resto = s[t.end():]
    p = _RE_PLACA.match(resto)
    if not p:
        return None

    def limpio(x):
        return re.sub(r"\s+", "", x).replace("BIS", " Bis")

    return f"{nombre} {limpio(p.group('via'))} # {limpio(p.group('cruce'))}-{p.group('placa')}"


def consulta(direccion, ciudad, departamento):
    """La consulta que se manda (y la LLAVE de la caché). None si la dirección no es geocodificable."""
    via = via_normalizada(direccion)
    if not via or not ciudad:
        return None
    partes = [via, str(ciudad).strip(), _sin_sufijo(departamento), "Colombia"]
    return ", ".join(p for p in partes if p)


def _md5(texto):
    return hashlib.md5(texto.encode("utf-8")).hexdigest()


def misma_ciudad(a, b):
    """¿Es el mismo municipio escrito distinto? Subconjunto de palabras en cualquier sentido
    ('BOGOTÁ, D.C.' ~ 'Bogotá'; 'CARTAGENA DE INDIAS' ~ 'Cartagena'). Mismo criterio que
    `diagnosticar_ciudad_terceros._misma_ciudad`."""
    ta, tb = set(_plano(a).split()) - {"d", "c"}, set(_plano(b).split()) - {"d", "c"}
    return bool(ta and tb) and (ta <= tb or tb <= ta)


# ── Base ──────────────────────────────────────────────────────────────────────

def candidatos(lo, segmento):
    """Los terceros del segmento con lo necesario para decidir si hay que llamar."""
    return lo.consultar(f"""
        WITH s AS ({SEGMENTOS[segmento]})
        SELECT t.tercero_id, t.direccion, t.direccion2, t.ciudad, t.departamento,
               t.codigo_dane_municipio, t.pais_codigo,
               g.hash_direccion AS hash_previo, g.llave AS llave_previa
        FROM s
        JOIN marts.dim_tercero t USING (tercero_id)
        LEFT JOIN marts.bi_geo_tercero g USING (tercero_id)
        WHERE COALESCE(t.pais_codigo, 'CO') = 'CO'
    """)


def preparar(df):
    """Añade consulta, llave, hash de la dirección y barrio escrito a cada candidato."""
    filas = []
    for r in df.itertuples():
        q = consulta(r.direccion, r.ciudad, r.departamento)
        h = _md5("|".join(str(x or "") for x in
                          (r.direccion, r.direccion2, r.codigo_dane_municipio, r.ciudad)))
        filas.append({"tercero_id": int(r.tercero_id), "consulta": q,
                      "llave": _md5(q) if q else None, "hash_direccion": h,
                      "barrio_escrito": barrio_escrito(r.direccion),
                      "cambio": h != r.hash_previo, "ciudad": r.ciudad,
                      "departamento": r.departamento})
    return pd.DataFrame(filas)


def llaves_en_cache(lo, llaves):
    if not llaves:
        return set()
    df = lo.consultar("SELECT llave FROM marts.bi_geo_direccion WHERE llave = ANY(%s)",
                      [list(llaves)])
    return set(df.llave) if df is not None and not df.empty else set()


def _modelo_cupo(dia=None):
    return f"geocoder:{PROVEEDOR}:{(dia or date.today()).isoformat()}"


def usado_hoy(lo):
    df = lo.consultar("SELECT filas FROM marts.etl_control WHERE modelo = %s", [_modelo_cupo()])
    return int(df.filas.iloc[0] or 0) if df is not None and not df.empty else 0


def reservar(lo, n):
    """Descuenta `n` llamadas del cupo del día ANTES de hacerlas (atómico en la base)."""
    with lo.get_connection() as c:
        cur = c.cursor()
        cur.execute("""
            INSERT INTO marts.etl_control (modelo, filas, actualizado) VALUES (%s, %s, now())
            ON CONFLICT (modelo) DO UPDATE
               SET filas = marts.etl_control.filas + EXCLUDED.filas, actualizado = now()
            RETURNING filas""", (_modelo_cupo(), n))
        total = cur.fetchone()[0]
        c.commit()
    return total


# ── Geoapify ──────────────────────────────────────────────────────────────────

def _llamar(sesion, texto, clave):
    """Una consulta, con timeout explícito y reintento ante 429/5xx. Devuelve el JSON o None."""
    params = {"text": texto, "filter": "countrycode:co", "lang": "es", "limit": 1,
              "format": "json", "apiKey": clave}
    for intento in range(1, REINTENTOS + 1):
        try:
            r = sesion.get(URL, params=params, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            logging.warning("geoapify: %s (intento %s)", type(e).__name__, intento)
            time.sleep(2 ** intento)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** intento)
            continue
        # 401/403: clave mala o cupo agotado — no tiene sentido seguir.
        raise RuntimeError(f"geoapify respondió {r.status_code}: {r.text[:200]}")
    return None


def _diagonal_m(bbox):
    """Diagonal del rectángulo de lo que coincidió, en metros (haversine). None si no viene."""
    try:
        lat1, lon1 = math.radians(float(bbox["lat1"])), math.radians(float(bbox["lon1"]))
        lat2, lon2 = math.radians(float(bbox["lat2"])), math.radians(float(bbox["lon2"]))
    except (TypeError, KeyError, ValueError):
        return None
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return int(round(2 * 6_371_000 * math.asin(math.sqrt(a))))


# ⚠ La placa colombiana («# 28-66») casi nunca está en OpenStreetMap: lo normal es que coincida la
# CALLE y la coordenada sea su punto medio (medido en la consulta de prueba del 2026-10-13:
# `match_by_street`, confianza 0,5). En una calle corta eso ubica bien el barrio; en una que cruza
# media ciudad, no. La incertidumbre es la mitad de la diagonal del `bbox` de lo que coincidió.
INCERTIDUMBRE_MAX_M = 500
TIPOS_UBICABLES = ("building", "amenity", "street")


def interpretar(respuesta, ciudad_cliente):
    """La fila de `bi_geo_direccion` (sin llave/consulta) a partir de la respuesta."""
    res = (respuesta or {}).get("results") or []
    if not res:
        return {"estado": "sin_resultado"}
    x = res[0]
    rank = x.get("rank") or {}
    ciudad = x.get("city") or x.get("county") or x.get("municipality")
    diagonal = _diagonal_m(x.get("bbox"))
    fila = {
        "latitud": round(float(x["lat"]), 6) if x.get("lat") is not None else None,
        "longitud": round(float(x["lon"]), 6) if x.get("lon") is not None else None,
        "tipo_resultado": x.get("result_type"),
        "confianza": rank.get("confidence"),
        "tipo_coincidencia": rank.get("match_type"),
        "barrio": x.get("suburb") or x.get("neighbourhood") or x.get("quarter"),
        "comuna_localidad": x.get("district") or x.get("city_district"),
        "ciudad": ciudad,
        "departamento": x.get("state"),
        "codigo_postal": x.get("postcode"),
        "place_id": x.get("place_id"),
        "incertidumbre_m": diagonal // 2 if diagonal is not None else None,
    }
    fila["estado"] = clasificar(fila, ciudad, ciudad_cliente)
    return fila


def clasificar(fila, ciudad=None, ciudad_cliente=None):
    """El estado de un resultado. ⚠ La MISMA regla está en SQL en `RECLASIFICAR` (para aplicarla a
    lo ya guardado sin volver a pagar llamadas): si cambia una, cambia la otra."""
    tipo, conf, inc = fila.get("tipo_resultado"), fila.get("confianza") or 0, fila.get("incertidumbre_m")
    if ciudad and ciudad_cliente and not misma_ciudad(ciudad, ciudad_cliente):
        return "fuera_de_municipio"
    if tipo not in TIPOS_UBICABLES or (inc or 0) > INCERTIDUMBRE_MAX_M:
        # Coincidió solo el barrio o la ciudad, o una calle larga: se guarda (no se vuelve a pagar)
        # pero no ubica a nadie.
        return "impreciso"
    if conf >= CONFIANZA_MINIMA:
        return "ok"
    # ⭐ Decisión de William tras el piloto (2026-10-13): una CALLE con confianza ≥ 0,4 y ≤ 250 m de
    # incertidumbre también ubica. Medido: 58 de 300 eran exactamente eso — idénticas a las buenas
    # salvo la confianza (0,41 frente a 0,50), casi siempre la calle paralela («Carrera 85» por
    # «Carrera 85C», ~100 m). Para barrio y comuna sobra precisión. Sube de 63 % a ~82 %.
    if tipo == "street" and conf >= CONFIANZA_CALLE_CERCANA and inc is not None \
            and inc <= INCERTIDUMBRE_CALLE_CERCANA_M:
        return "ok"
    return "baja_confianza"


CONFIANZA_CALLE_CERCANA = 0.4
INCERTIDUMBRE_CALLE_CERCANA_M = 250

# La misma regla de `clasificar`, en SQL, para reclasificar lo ya guardado (sin la parte de
# municipio, que ya quedó decidida al guardar: no se toca lo que está en `fuera_de_municipio`).
RECLASIFICAR = f"""
    UPDATE marts.bi_geo_direccion SET estado = CASE
        WHEN tipo_resultado IS NULL OR tipo_resultado NOT IN {TIPOS_UBICABLES}
             OR COALESCE(incertidumbre_m, 0) > {INCERTIDUMBRE_MAX_M} THEN 'impreciso'
        WHEN COALESCE(confianza, 0) >= {CONFIANZA_MINIMA} THEN 'ok'
        WHEN tipo_resultado = 'street' AND confianza >= {CONFIANZA_CALLE_CERCANA}
             AND incertidumbre_m <= {INCERTIDUMBRE_CALLE_CERCANA_M} THEN 'ok'
        ELSE 'baja_confianza' END
    WHERE estado NOT IN ('fuera_de_municipio', 'sin_resultado')
"""


def guardar_direcciones(lo, filas):
    if not filas:
        return
    cols = ["llave", "consulta", "proveedor", "estado", "latitud", "longitud", "tipo_resultado",
            "confianza", "tipo_coincidencia", "barrio", "comuna_localidad", "ciudad",
            "departamento", "codigo_postal", "place_id", "incertidumbre_m"]
    with lo.get_connection() as c:
        cur = c.cursor()
        from psycopg2.extras import execute_values
        execute_values(cur, f"""
            INSERT INTO marts.bi_geo_direccion ({', '.join(cols)}) VALUES %s
            ON CONFLICT (llave) DO UPDATE SET
              {', '.join(f'{k} = EXCLUDED.{k}' for k in cols if k != 'llave')},
              consultado_en = now()""",
            [tuple(f.get(k) for k in cols) for f in filas])
        c.commit()


def guardar_terceros(lo, df):
    if df.empty:
        return
    with lo.get_connection() as c:
        cur = c.cursor()
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO marts.bi_geo_tercero (tercero_id, llave, hash_direccion, barrio_escrito)
            VALUES %s
            ON CONFLICT (tercero_id) DO UPDATE SET
              llave = EXCLUDED.llave, hash_direccion = EXCLUDED.hash_direccion,
              barrio_escrito = EXCLUDED.barrio_escrito, asignado_en = now()""",
            [(int(r.tercero_id), r.llave, r.hash_direccion, r.barrio_escrito)
             for r in df.itertuples()])
        c.commit()


# ── Informe ───────────────────────────────────────────────────────────────────

def informe(lo, llaves=None):
    filtro = "WHERE d.llave = ANY(%s)" if llaves else ""
    params = [list(llaves)] if llaves else None
    df = lo.consultar(f"""
        SELECT d.llave, d.estado, d.tipo_resultado, d.tipo_coincidencia, d.confianza,
               d.barrio IS NOT NULL AS con_barrio, d.comuna_localidad IS NOT NULL AS con_comuna,
               d.barrio, d.comuna_localidad, d.ciudad, d.incertidumbre_m, g.barrio_escrito
        FROM marts.bi_geo_direccion d
        LEFT JOIN marts.bi_geo_tercero g ON g.llave = d.llave
        {filtro}
    """, params)
    if df is None or df.empty:
        print("  (no hay nada geocodificado todavía)")
        return
    # Una dirección puede tener varios clientes: se cuenta una vez.
    df = df.drop_duplicates("llave")
    print(f"\n  Direcciones: {len(df)}")
    for col in ("estado", "tipo_resultado", "tipo_coincidencia"):
        print(f"\n  -- {col} --")
        for k, v in Counter(df[col].fillna("(vacío)")).most_common():
            print(f"     {v:6d}  {100*v/len(df):5.1f} %  {k}")
    print("\n  -- incertidumbre (mitad de la diagonal de lo que coincidió) --")
    tramos = [(0, 100, "<= 100 m"), (101, 250, "101-250 m"), (251, 500, "251-500 m"),
              (501, 1000, "501 m-1 km"), (1001, 10**9, "> 1 km")]
    inc = df.incertidumbre_m.dropna()
    for lo_m, hi_m, rotulo in tramos:
        n = int(((inc >= lo_m) & (inc <= hi_m)).sum())
        print(f"     {n:6d}  {100*n/len(df):5.1f} %  {rotulo}")
    print(f"     {int(df.incertidumbre_m.isna().sum()):6d}  sin bbox")
    ok = df[df.estado == "ok"]
    if len(ok):
        print(f"\n  De las {len(ok)} OK: con barrio OSM {ok.con_barrio.mean()*100:.1f} % · "
              f"con comuna/localidad {ok.con_comuna.mean()*100:.1f} %")
        print("\n  -- comuna/localidad de OSM más frecuentes (¿son comunas de verdad?) --")
        for (c, ciu), v in Counter(zip(ok.comuna_localidad.fillna("(vacío)"),
                                       ok.ciudad.fillna("?"))).most_common(15):
            print(f"     {v:5d}  {c}  [{ciu}]")
        print("\n  -- barrio de OSM más frecuentes --")
        for (b, ciu), v in Counter(zip(ok.barrio.fillna("(vacío)"),
                                       ok.ciudad.fillna("?"))).most_common(10):
            print(f"     {v:5d}  {b}  [{ciu}]")
        ambos = ok[ok.barrio.notna() & ok.barrio_escrito.notna()]
        if len(ambos):
            coinc = sum(misma_ciudad(a, b) for a, b in zip(ambos.barrio, ambos.barrio_escrito))
            print(f"  Barrio OSM vs barrio ESCRITO por el cliente: {coinc} de {len(ambos)} coinciden")


# ── Principal ─────────────────────────────────────────────────────────────────

def main(seco, segmento, limite, piloto, muestra, solo_informe, sin_llamadas, reclasificar=False):
    lo = DBLoader()
    if reclasificar:
        # Aplica la regla vigente de `clasificar` a lo YA guardado: cero llamadas.
        with lo.get_connection() as c:
            cur = c.cursor()
            cur.execute(RECLASIFICAR)
            print(f"Reclasificadas {cur.rowcount} direcciones con la regla vigente.")
            c.commit()
        informe(lo)
        return
    if solo_informe:
        informe(lo)
        return

    # `auto` (lo que corre el cron): asigna a TODOS los clientes —gratis— y llama por prioridad.
    df = preparar(candidatos(lo, "todos" if segmento == "auto" else segmento))
    geo = df[df.consulta.notna()]
    # ⚠ Dos cosas distintas, y mezclarlas fue el primer diseño: ASIGNAR (tercero → llave + barrio
    # escrito, gratis) y LLAMAR (llave que no está en la caché, se paga). Lo pendiente de llamar se
    # decide contra la caché, no contra la asignación: si no, asignar a todos sin llamar dejaría
    # sus direcciones fuera de la cola para siempre.
    por_asignar = df[df.cambio]
    llaves_seg = set(geo.llave)
    en_cache = llaves_en_cache(lo, llaves_seg)
    a_llamar = sorted(llaves_seg - en_cache)
    if segmento == "auto":
        a_llamar = por_prioridad(lo, geo, a_llamar)

    print(f"Segmento «{segmento}»: {len(df)} clientes · {len(geo)} con dirección geocodificable "
          f"({len(llaves_seg)} direcciones distintas) · {df.barrio_escrito.notna().sum()} con "
          f"barrio escrito")
    print(f"Por asignar (nuevos o con dirección cambiada): {len(por_asignar)} clientes · "
          f"por llamar: {len(a_llamar)} direcciones ({len(en_cache)} ya en caché)")
    usado = usado_hoy(lo)
    print(f"Cupo de hoy: {usado} usadas de {CUPO_DIARIO}; tope de esta corrida {limite}")
    if muestra:
        print("\n  -- consultas tal como saldrían --")
        for r in geo.head(muestra).itertuples():
            print(f"     {r.consulta}   [barrio escrito: {r.barrio_escrito}]")

    if piloto:
        orden = sorted(a_llamar, key=lambda k: _md5("piloto:" + k))   # reproducible
        a_llamar = orden[:piloto]
    a_llamar = a_llamar[:max(0, min(limite, CUPO_DIARIO - usado))]

    if seco:
        print(f"\n(--seco) Se asignarían {len(por_asignar)} clientes y se harían {len(a_llamar)} "
              f"llamadas. No se llamó a nada ni se escribió.")
        return

    # Asignar es gratis: tercero → llave, hash y barrio escrito. El barrio escrito sirve YA, sin
    # ninguna llamada (13,9 % de los clientes con venta lo traen).
    guardar_terceros(lo, por_asignar)
    print(f"Asignados {len(por_asignar)} clientes.")
    if sin_llamadas:
        print("(--sin-llamadas) No se llamó al geocodificador.")
        return

    clave = os.getenv("GEOAPIFY_API_KEY")
    if not clave:
        raise SystemExit("Falta GEOAPIFY_API_KEY (en el .env local y en el servicio analisis_datos "
                         "de Railway). Ver docs/georreferenciacion_clientes.md.")
    if not a_llamar:
        print("Nada que llamar.")
        return
    total = reservar(lo, len(a_llamar))
    print(f"Reservadas {len(a_llamar)} llamadas (hoy van {total}).")

    consulta_de = dict(zip(geo.llave, geo.consulta))
    ciudad_de = dict(zip(geo.llave, geo.ciudad))
    t_ini = time.perf_counter()
    hechas = geocodificar_en_paralelo(lo, a_llamar, consulta_de, ciudad_de, clave)
    minutos = (time.perf_counter() - t_ini) / 60
    print(f"Hechas {hechas} llamadas en {minutos:.1f} min.")
    informe(lo, a_llamar if piloto else None)


# ⚠ EN PARALELO (2026-10-13): el piloto en serie tardó ~4,5 s por consulta (latencia + cortes de red
# con reintento), o sea ~3,5 h para el cupo de un día. Con HILOS concurrentes el tiempo es el de la
# red dividido entre ellos; el LIMITADOR mantiene el ritmo de arranque por debajo de las 5/s del plan
# gratis aunque los hilos terminen a la vez. Cada hilo tiene su propia `Session` (no es thread-safe).
HILOS = 4


def geocodificar_en_paralelo(lo, llaves, consulta_de, ciudad_de, clave):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    candado, ultimo = threading.Lock(), [0.0]
    local = threading.local()

    def turno():
        """Espera su turno para no arrancar más de POR_SEGUNDO consultas por segundo."""
        with candado:
            espera = ultimo[0] + 1.0 / POR_SEGUNDO - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
            ultimo[0] = time.perf_counter()

    def una(llave):
        if not hasattr(local, "sesion"):
            local.sesion = requests.Session()
        turno()
        fila = interpretar(_llamar(local.sesion, consulta_de[llave], clave), ciudad_de.get(llave))
        fila.update({"llave": llave, "consulta": consulta_de[llave], "proveedor": PROVEEDOR})
        return fila

    lote, hechas = [], 0
    with ThreadPoolExecutor(max_workers=HILOS) as ex:
        for fut in as_completed([ex.submit(una, k) for k in llaves]):
            lote.append(fut.result())
            hechas += 1
            if len(lote) >= 200:
                guardar_direcciones(lo, lote)     # se guarda por tandas: un corte no lo pierde todo
                lote = []
                logging.info("geocodificadas %s de %s", hechas, len(llaves))
    guardar_direcciones(lo, lote)
    return hechas


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seco", action="store_true", help="cuenta y enseña, sin red ni escritura")
    ap.add_argument("--segmento", choices=list(SEGMENTOS) + ["auto"], default="recompradores",
                    help="auto = lo que corre el cron: asigna a todos y llama por PRIORIDAD")
    ap.add_argument("--limite", type=int, default=TOPE_POR_DEFECTO)
    ap.add_argument("--piloto", type=int, default=0, help="N direcciones al azar (reproducible)")
    ap.add_argument("--muestra", type=int, default=0, help="enseña N consultas normalizadas")
    ap.add_argument("--informe", action="store_true", help="calidad de lo ya geocodificado")
    ap.add_argument("--sin-llamadas", action="store_true",
                    help="solo asigna (barrio escrito incluido), sin llamar al geocodificador")
    ap.add_argument("--reclasificar", action="store_true",
                    help="aplica la regla vigente de estado a lo ya guardado (sin llamadas)")
    a = ap.parse_args()
    main(a.seco, a.segmento, a.limite, a.piloto, a.muestra, a.informe, a.sin_llamadas,
         a.reclasificar)
