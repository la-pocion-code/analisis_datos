"""
poligonos_geo.py — Barrios y comunas OFICIALES por punto-en-polígono, para los mapas de clientes.

    python poligonos_geo.py --cargar            # descarga y guarda los polígonos de FUENTES
    python poligonos_geo.py --cargar --solo 11001-barrio
    python poligonos_geo.py --asignar           # barrio y comuna de cada coordenada geocodificada
    python poligonos_geo.py --informe           # cobertura por ciudad

POR QUÉ (piloto del 2026-10-13): el barrio que devuelve OpenStreetMap llega en el 13,8 % de las
direcciones ubicadas y vacío en Bogotá, Medellín y Cali, y su «comuna» son nombres sueltos («El
Hormiguero», «Zipaquirá ciudad»). La COORDENADA sí es buena (82 % ubicadas, la mayoría a menos de
250 m). Así que el barrio y la comuna salen de cruzar esa coordenada con los polígonos OFICIALES de
cada ciudad (datos abiertos de su alcaldía o catastro), y OSM queda como respaldo.

⚠ EL PUNTO DE UNA CALLE CAE ENTRE BARRIOS. Lo normal es que coincida la calle y no la casa, y su
coordenada está sobre el eje de la vía; en un polígono catastral la vía suele quedar FUERA de todos
los barrios. Por eso: primero «dentro», y si no, el polígono MÁS CERCANO a menos de
`TOLERANCIA_M`. El método queda guardado (`dentro` / `cercano`) para poder medirlo.

⚠ LAS GEOMETRÍAS SE PIDEN EN WGS84 (EPSG:4326), que es lo que exige GeoJSON (RFC 7946): las fuentes
colombianas publican en MAGNA-SIRGAS (EPSG:3116, 9377…) y cruzar sin reproyectar daría cero
coincidencias sin ningún error. Cada fuente declara cómo pedir 4326 (p. ej. `outSR=4326`), y el
cargador RECHAZA una geometría cuyas coordenadas no caben en Colombia.

⚠ LICENCIAS: cada fuente lleva la suya y su texto de atribución, que viajan con el polígono.
"""
import os
import sys
import json
import logging
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from dotenv import load_dotenv
from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.validation import make_valid

from classes.db_loader import DBLoader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TIMEOUT_S = 120
# ~60 m: en Colombia (latitud 0-12°) un grado son ~111 km, así que basta un factor fijo para
# una tolerancia de este tamaño.
TOLERANCIA_M = 60
M_POR_GRADO = 111_000
# Colombia con margen (San Andrés incluido): una coordenada fuera de aquí es una proyección mal
# pedida, no un barrio.
CAJA_COLOMBIA = (-82.5, -4.5, -66.5, 13.6)   # lon_min, lat_min, lon_max, lat_max

# Las fuentes. Se rellenan con la investigación verificada (URL que responde, licencia, campos).
# Cada una: ciudad (DANE), nivel (barrio|comuna), url (que devuelva GeoJSON en 4326), campo del
# nombre, campo del código, y para un barrio el campo de su comuna si lo trae.
FUENTES = []


def _paginas(url):
    """Descarga un GeoJSON. Si es un FeatureServer de ArcGIS, pagina con `resultOffset` (los
    servidores cortan en 1.000-2.000 entidades por respuesta y un barrio de más se perdería en
    silencio)."""
    if "FeatureServer" not in url and "MapServer" not in url:
        r = requests.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json().get("features", [])
    feats, offset = [], 0
    while True:
        sep = "&" if "?" in url else "?"
        r = requests.get(f"{url}{sep}resultOffset={offset}&resultRecordCount=1000",
                         timeout=TIMEOUT_S)
        r.raise_for_status()
        lote = r.json().get("features", [])
        feats.extend(lote)
        if len(lote) < 1000:
            return feats
        offset += len(lote)


def _en_colombia(geom):
    x0, y0, x1, y1 = geom.bounds
    a, b, c, d = CAJA_COLOMBIA
    return a <= x0 and x1 <= c and b <= y0 and y1 <= d


def cargar(lo, fuente):
    feats = _paginas(fuente["url"])
    filas, malas = [], 0
    for f in feats:
        props = f.get("properties") or {}
        try:
            geom = make_valid(shape(f["geometry"]))
        except Exception:
            malas += 1
            continue
        if geom.is_empty or not _en_colombia(geom):
            malas += 1
            continue
        nombre = str(props.get(fuente["campo_nombre"]) or "").strip()
        codigo = str(props.get(fuente.get("campo_codigo") or fuente["campo_nombre"]) or nombre).strip()
        padre = props.get(fuente["campo_comuna"]) if fuente.get("campo_comuna") else None
        filas.append((fuente["ciudad"], fuente["nivel"], codigo, nombre.title() if nombre.isupper() else nombre,
                      str(padre).strip() if padre not in (None, "") else None,
                      json.dumps(geom.__geo_interface__), fuente["fuente"], fuente["licencia"]))
    if not filas:
        raise RuntimeError(f"{fuente['ciudad']}-{fuente['nivel']}: 0 polígonos válidos de "
                           f"{len(feats)} ({malas} descartados). ¿Se pidió en 4326?")
    with lo.get_connection() as c:
        cur = c.cursor()
        # Reemplazo atómico de esa ciudad y nivel: nadie ve la tabla a medias.
        cur.execute("DELETE FROM marts.bi_geo_poligono WHERE ciudad_dane = %s AND nivel = %s",
                    (fuente["ciudad"], fuente["nivel"]))
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO marts.bi_geo_poligono
                (ciudad_dane, nivel, codigo, nombre, comuna, geojson, fuente, licencia)
            VALUES %s ON CONFLICT (ciudad_dane, nivel, codigo) DO NOTHING""", filas)
        c.commit()
    logging.info("%s-%s: %s polígonos (%s descartados de %s)", fuente["ciudad"], fuente["nivel"],
                 len(filas), malas, len(feats))
    return len(filas)


class Indice:
    """Los polígonos de una ciudad y un nivel, con su árbol espacial."""

    def __init__(self, filas):
        self.filas = filas
        self.geoms = [shape(json.loads(f["geojson"])) for f in filas]
        self.arbol = STRtree(self.geoms)

    def buscar(self, lon, lat):
        """(fila, método) del polígono que contiene el punto, o el más cercano a < TOLERANCIA_M."""
        p = Point(lon, lat)
        for i in self.arbol.query(p, predicate="covered_by"):
            return self.filas[int(i)], "dentro"
        i = self.arbol.nearest(p)
        if i is not None and self.geoms[int(i)].distance(p) * M_POR_GRADO <= TOLERANCIA_M:
            return self.filas[int(i)], "cercano"
        return None, None


def asignar(lo, todo=False):
    """Barrio y comuna oficiales de cada dirección ubicada (`estado='ok'`) en una ciudad con
    polígonos. `todo=True` recalcula también las ya asignadas (tras cargar polígonos nuevos)."""
    pol = lo.consultar("SELECT ciudad_dane, nivel, codigo, nombre, comuna, geojson "
                       "FROM marts.bi_geo_poligono")
    if pol is None or pol.empty:
        print("No hay polígonos cargados.")
        return 0
    indices = {(c, n): Indice(g.to_dict("records")) for (c, n), g in pol.groupby(["ciudad_dane", "nivel"])}
    ciudades = sorted({c for c, _ in indices})
    filtro = "" if todo else "AND d.fuente_poligono IS NULL"
    pts = lo.consultar(f"""
        SELECT d.llave, d.latitud, d.longitud, min(t.codigo_dane_municipio) AS dane
        FROM marts.bi_geo_direccion d
        JOIN marts.bi_geo_tercero g ON g.llave = d.llave
        JOIN marts.dim_tercero t ON t.tercero_id = g.tercero_id
        WHERE d.estado = 'ok' AND t.codigo_dane_municipio = ANY(%s) {filtro}
        GROUP BY d.llave, d.latitud, d.longitud
    """, [ciudades])
    if pts is None or pts.empty:
        print("Nada que asignar.")
        return 0
    cambios = []
    for r in pts.itertuples():
        lon, lat = float(r.longitud), float(r.latitud)
        barrio, mb = (indices[(r.dane, "barrio")].buscar(lon, lat)
                      if (r.dane, "barrio") in indices else (None, None))
        comuna, mc = (indices[(r.dane, "comuna")].buscar(lon, lat)
                      if (r.dane, "comuna") in indices else (None, None))
        nombre_comuna = comuna["nombre"] if comuna else (barrio["comuna"] if barrio else None)
        metodo = mb or mc or "sin_poligono"
        cambios.append((barrio["nombre"] if barrio else None, nombre_comuna, metodo, r.llave))
    with lo.get_connection() as c:
        cur = c.cursor()
        from psycopg2.extras import execute_batch
        execute_batch(cur, """
            UPDATE marts.bi_geo_direccion
               SET barrio_oficial = %s, comuna_oficial = %s, fuente_poligono = %s
             WHERE llave = %s""", cambios, page_size=500)
        c.commit()
    print(f"Asignadas {len(cambios)} direcciones en {len(ciudades)} ciudades con polígonos.")
    return len(cambios)


def informe(lo):
    df = lo.consultar("""
        SELECT t.codigo_dane_municipio AS dane, max(t.ciudad) AS ciudad,
               COUNT(DISTINCT d.llave) AS ubicadas,
               COUNT(DISTINCT d.llave) FILTER (WHERE d.barrio_oficial IS NOT NULL) AS con_barrio,
               COUNT(DISTINCT d.llave) FILTER (WHERE d.comuna_oficial IS NOT NULL) AS con_comuna,
               COUNT(DISTINCT d.llave) FILTER (WHERE d.fuente_poligono = 'cercano') AS por_cercania
        FROM marts.bi_geo_direccion d
        JOIN marts.bi_geo_tercero g ON g.llave = d.llave
        JOIN marts.dim_tercero t ON t.tercero_id = g.tercero_id
        WHERE d.estado = 'ok' AND d.fuente_poligono IS NOT NULL
        GROUP BY 1 ORDER BY ubicadas DESC
    """)
    print(df.to_string(index=False) if df is not None and not df.empty else "(nada asignado)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cargar", action="store_true")
    ap.add_argument("--solo", help="ciudad-nivel, p. ej. 11001-barrio")
    ap.add_argument("--asignar", action="store_true")
    ap.add_argument("--todo", action="store_true", help="con --asignar: recalcula también lo asignado")
    ap.add_argument("--informe", action="store_true")
    a = ap.parse_args()
    lo = DBLoader()
    if a.cargar:
        for f in FUENTES:
            if a.solo and a.solo != f"{f['ciudad']}-{f['nivel']}":
                continue
            cargar(lo, f)
    if a.asignar:
        asignar(lo, todo=a.todo)
    if a.informe:
        informe(lo)
