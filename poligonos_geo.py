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

# ── Las fuentes, verificadas el 2026-10-13 (que responden, conteo, campos, licencia) ──────────
# Cada una: ciudad (DANE), nivel (barrio|comuna), url (GeoJSON en 4326), campo del nombre, del
# código y, para un barrio, el de su comuna si lo trae (respaldo: la comuna sale primero del
# polígono de comunas). `paginar=False` donde el servidor rechaza `resultOffset` (AMB → 400).
# ⛔ NO entran, a propósito: Cartagena y Santa Marta (sus barrios publicados no son oficiales o no
#    tienen licencia), y Bogotá «barrio legalizado» (solo cubre asentamientos legalizados y su
#    servicio se editó por última vez en 2019): se usa el SECTOR CATASTRAL, que cubre la ciudad.
# ⚠ «Licencia no declarada» = dato público de la alcaldía o del catastro, sin licencia explícita
#    en el servicio. Se usa para análisis interno con su atribución; no se republica.
_ARC = "where=1%3D1&outFields={campos}&outSR=4326&f=geojson"
_DANE = ("https://geoportal.dane.gov.co/mparcgis/rest/services/Comuna_Localidad/"
         "Serv_Comuna_Localidad_2018/FeatureServer/0/query?where=COD_MPIO%3D%27{dane}%27"
         "&outFields=COD_MPIO,COD_LOC_COM,NOM_LOC_COM&outSR=4326&f=geojson")
_LIC_DANE = "Términos de uso del DANE — Fuente: Departamento Administrativo Nacional de Estadística"
_SIN_LIC = "no declarada (dato público oficial)"


def _dane(dane, ciudad):
    return {"ciudad": dane, "nivel": "comuna", "url": _DANE.format(dane=dane),
            "campo_nombre": "NOM_LOC_COM", "campo_codigo": "COD_LOC_COM",
            "fuente": f"DANE, comunas y localidades 2018 ({ciudad})", "licencia": _LIC_DANE}


FUENTES = [
    # ── Bogotá: sector catastral (UAECD) + localidad (SDP). CC BY 4.0 ──
    {"ciudad": "11001", "nivel": "barrio",
     "url": "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/catastro/"
            "sectorcatastral/MapServer/0/query?" + _ARC.format(campos="SCACODIGO,SCANOMBRE"),
     "campo_nombre": "SCANOMBRE", "campo_codigo": "SCACODIGO",
     "fuente": "UAECD Bogotá, sector catastral", "licencia": "CC BY 4.0"},
    {"ciudad": "11001", "nivel": "comuna",
     "url": "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/"
            "ordenamientoterritorial/localidad/MapServer/0/query?"
            + _ARC.format(campos="LOCCODIGO,LOCNOMBRE"),
     "campo_nombre": "LOCNOMBRE", "campo_codigo": "LOCCODIGO",
     "fuente": "SDP Bogotá, localidades", "licencia": "CC BY 4.0"},
    # ── Medellín: catastro (barrio/vereda con su comuna; comuna/corregimiento) ──
    {"ciudad": "05001", "nivel": "barrio",
     "url": "https://www.medellin.gov.co/servidormapas/rest/services/ServiciosCatastro/"
            "Base_Catastral/MapServer/1/query?"
            + _ARC.format(campos="codigo,nombre_barrio,nombre_comuna"),
     "campo_nombre": "nombre_barrio", "campo_codigo": "codigo", "campo_comuna": "nombre_comuna",
     "fuente": "Subsecretaría de Catastro de Medellín", "licencia": _SIN_LIC},
    {"ciudad": "05001", "nivel": "comuna",
     "url": "https://www.medellin.gov.co/servidormapas/rest/services/ServiciosCatastro/"
            "Base_Catastral/MapServer/0/query?" + _ARC.format(campos="comuna,nombre"),
     "campo_nombre": "nombre", "campo_codigo": "comuna",
     "fuente": "Subsecretaría de Catastro de Medellín", "licencia": _SIN_LIC},
    # ── Cali: IDESC / DAPM, WFS ──
    {"ciudad": "76001", "nivel": "barrio",
     "url": "https://ws-idesc.cali.gov.co/geoserver/dapm/ows?service=WFS&version=1.0.0"
            "&request=GetFeature&typeName=dapm:pdt_dpa_barrios_sectores"
            "&outputFormat=application/json&srsName=EPSG:4326",
     "campo_nombre": "barnombre", "campo_codigo": "barcodigo", "campo_comuna": "barcomuna",
     "fuente": "IDESC Cali (DAPM), barrios", "licencia": "CC BY"},
    {"ciudad": "76001", "nivel": "comuna",
     "url": "https://ws-idesc.cali.gov.co/geoserver/dapm/ows?service=WFS&version=1.0.0"
            "&request=GetFeature&typeName=dapm:pdt_dpa_comunas"
            "&outputFormat=application/json&srsName=EPSG:4326",
     "campo_nombre": "comnombre", "campo_codigo": "comcodigo",
     "fuente": "IDESC Cali (DAPM), comunas", "licencia": "CC BY"},
    # ── Barranquilla y Bello: datos.gov.co (SODA) ──
    {"ciudad": "08001", "nivel": "barrio",
     "url": "https://www.datos.gov.co/resource/qui6-qeux.geojson?$limit=5000",
     "campo_nombre": "nombre", "campo_comuna": "localidad",
     "fuente": "Alcaldía de Barranquilla (datos.gov.co qui6-qeux)", "licencia": "CC BY-SA 4.0"},
    {"ciudad": "05088", "nivel": "barrio",
     "url": "https://www.datos.gov.co/resource/pnhh-ccwd.geojson?$limit=5000",
     "campo_nombre": "nombre",
     "fuente": "Alcaldía de Bello (datos.gov.co pnhh-ccwd)", "licencia": "CC BY-SA 4.0"},
    # ── Área Metropolitana de Bucaramanga (AMB): una capa, cuatro municipios ──
    *[{"ciudad": dane, "nivel": "barrio", "paginar": False,
       "url": "https://mapa.amb.gov.co/server/rest/services/VISOR/"
              "MAPA_BASE_VECTORIAL_105_Definitivo/MapServer/2/query?where=MUNICIPIO%3D%27"
              f"{mpio}%27&outFields=NOMBRE,MUNICIPIO&f=geojson",
       "campo_nombre": "NOMBRE",
       "fuente": f"Área Metropolitana de Bucaramanga, barrios ({mpio.title()})",
       "licencia": _SIN_LIC}
      for dane, mpio in (("68001", "BUCARAMANGA"), ("68276", "FLORIDABLANCA"),
                         ("68307", "GIRON"), ("68547", "PIEDECUESTA"))],
    # ── Pereira, Manizales, Itagüí y Soacha: barrios y comunas propios ──
    {"ciudad": "66001", "nivel": "barrio",
     "url": "https://services3.arcgis.com/Zdpg0E6lri7EggIc/arcgis/rest/services/"
            "Barrios_del_municipio_de_Pereira/FeatureServer/0/query?"
            + _ARC.format(campos="NOMBRE,CODBAR,COMUNA"),
     "campo_nombre": "NOMBRE", "campo_codigo": "CODBAR", "campo_comuna": "COMUNA",
     "fuente": "Portal Geográfico de Pereira, barrios", "licencia": _SIN_LIC},
    {"ciudad": "66001", "nivel": "comuna",
     "url": "https://services3.arcgis.com/Zdpg0E6lri7EggIc/arcgis/rest/services/"
            "Comunas_Actual/FeatureServer/0/query?" + _ARC.format(campos="*"),
     "campo_nombre": "NOMBRE",
     "fuente": "Portal Geográfico de Pereira, comunas", "licencia": _SIN_LIC},
    {"ciudad": "17001", "nivel": "barrio",
     "url": "https://sig.manizales.gov.co/wadmzl/rest/services/20_WEB/2021_consulta_catas_urbano_web/"
            "FeatureServer/1/query?" + _ARC.format(campos="*"),
     "campo_nombre": "BARRIOS",
     "fuente": "Alcaldía de Manizales, barrios", "licencia": _SIN_LIC},
    {"ciudad": "17001", "nivel": "comuna",
     "url": "https://sig.manizales.gov.co/wadmzl/rest/services/20_WEB/2021_consulta_catas_urbano_web/"
            "FeatureServer/0/query?" + _ARC.format(campos="*"),
     "campo_nombre": "NOMBRES_CO", "campo_codigo": "nro",
     "fuente": "Alcaldía de Manizales, comunas", "licencia": _SIN_LIC},
    {"ciudad": "05360", "nivel": "barrio",
     "url": "https://arcgis.itagui.gov.co/waserver/rest/services/Catastro/VC_Catastro_2024/"
            "MapServer/7/query?" + _ARC.format(campos="nom_barrio,barrio,comuna"),
     "campo_nombre": "nom_barrio", "campo_codigo": "barrio", "campo_comuna": "comuna",
     "fuente": "Alcaldía de Itagüí, catastro 2024", "licencia": _SIN_LIC},
    {"ciudad": "05360", "nivel": "comuna",
     "url": "https://arcgis.itagui.gov.co/waserver/rest/services/Cartografia_Base/"
            "01_Division_Politico_Administrativa/MapServer/3/query?" + _ARC.format(campos="*"),
     "campo_nombre": "comuna",
     "fuente": "Alcaldía de Itagüí, comunas", "licencia": _SIN_LIC},
    {"ciudad": "25754", "nivel": "barrio",
     "url": "https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services/"
            "Barrios_Soacha_Magna/FeatureServer/0/query?"
            + _ARC.format(campos="NOM_BARRIO,COM_MAPA"),
     "campo_nombre": "NOM_BARRIO", "campo_comuna": "COM_MAPA",
     "fuente": "IDE Cundinamarca / Alcaldía de Soacha, barrios", "licencia": "CC BY 4.0"},
    {"ciudad": "25754", "nivel": "comuna",
     "url": "https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services/"
            "Comunas_Soacha_Magna/FeatureServer/0/query?" + _ARC.format(campos="*"),
     "campo_nombre": "COMUNA",
     "fuente": "IDE Cundinamarca / Alcaldía de Soacha, comunas", "licencia": "CC BY 4.0"},
    # ── Comunas del DANE donde la ciudad no publica las suyas ──
    *[_dane(d, c) for d, c in (
        ("08001", "Barranquilla"), ("13001", "Cartagena"), ("05088", "Bello"),
        ("73001", "Ibagué"), ("68001", "Bucaramanga"), ("54001", "Cúcuta"), ("41001", "Neiva"),
        ("76520", "Palmira"), ("50001", "Villavicencio"), ("47001", "Santa Marta"),
        ("05266", "Envigado"))],
]


def _paginas(url, paginar=True):
    """Descarga un GeoJSON. Si es un FeatureServer de ArcGIS, pagina con `resultOffset` (los
    servidores cortan en 1.000-2.000 entidades por respuesta y un barrio de más se perdería en
    silencio)."""
    if not paginar or ("FeatureServer" not in url and "MapServer" not in url):
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
    feats = _paginas(fuente["url"], fuente.get("paginar", True))
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
    # ⚠ UNA ciudad por dirección (la misma regla que `asignar`, `min()` de las que no son nulas): una
    # dirección compartida por dos clientes, uno sin código DANE, salía contada dos veces.
    df = lo.consultar("""
        WITH d AS (
            SELECT d.llave, d.barrio_oficial, d.comuna_oficial, d.fuente_poligono,
                   min(t.codigo_dane_municipio) AS dane, max(t.ciudad) AS ciudad
            FROM marts.bi_geo_direccion d
            JOIN marts.bi_geo_tercero g ON g.llave = d.llave
            JOIN marts.dim_tercero t ON t.tercero_id = g.tercero_id
            WHERE d.estado = 'ok' AND d.fuente_poligono IS NOT NULL
            GROUP BY 1, 2, 3, 4
        )
        SELECT dane, max(ciudad) AS ciudad, COUNT(*) AS ubicadas,
               COUNT(*) FILTER (WHERE barrio_oficial IS NOT NULL) AS con_barrio,
               COUNT(*) FILTER (WHERE comuna_oficial IS NOT NULL) AS con_comuna,
               COUNT(*) FILTER (WHERE fuente_poligono = 'cercano') AS por_cercania
        FROM d GROUP BY 1 ORDER BY ubicadas DESC
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
        # Cada fuente por su lado: un servidor municipal caído no debe dejar sin polígonos a las
        # demás ciudades. Se informa todo al final.
        resumen = []
        for f in FUENTES:
            if a.solo and a.solo != f"{f['ciudad']}-{f['nivel']}":
                continue
            try:
                resumen.append((f["ciudad"], f["nivel"], cargar(lo, f), f["fuente"]))
            except Exception as e:
                resumen.append((f["ciudad"], f["nivel"], f"ERROR {type(e).__name__}: {str(e)[:90]}",
                                f["fuente"]))
        print()
        print("  -- carga de polígonos --")
        for ciudad, nivel, n, fuente in resumen:
            print(f"     {ciudad} {nivel:7s} {str(n):>6}  {fuente}")
    if a.asignar:
        asignar(lo, todo=a.todo)
    if a.informe:
        informe(lo)
