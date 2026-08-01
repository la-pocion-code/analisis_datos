"""
cargar_bi_datasets.py — Sube a `marts.bi_*` los datasets que el BI (Power BI) todavía consume desde
archivos locales, para DESCONECTAR el BI de archivos locales. Fuente: Google Drive (DriveLoader),
igual patrón que cargar_mapeos.py. Recarga completa (if_exists='replace') como los map_*.

Se corre A DEMANDA (cuando cambie un archivo en Drive):

    python cargar_bi_datasets.py

Cada dataset va en su try/except: un fallo NO aborta el resto. Al final imprime un resumen.

CARGAS DIRECTAS (1 archivo Drive -> 1 tabla):
    bi_lineas            data/LINEAS Y CATEGORIAS.xlsx        (DRIVE_IDS['lineas_categorias'])
    bi_ofertas           data/OFERTAS.xlsx                    (DRIVE_IDS['ofertas'])
    bi_presupuesto       data/PRESUPUESTO GENERAL.xlsx        (DRIVE_IDS['presupuesto_general'])
    bi_clientes_impulso  data/Clientes Impulso.xlsx           (DRIVE_IDS['clientes_impulso'])
    bi_base_pyg          data/contabilidad/base_consolidada.csv (DRIVE_IDS['base_consolidada'])
    bi_cuentas_clave     data/cuentas_clave/base_cuentas_clave.xlsx (DRIVE_IDS['base_cuentas_clave'])
    bi_cartera           data/../cartera_procesada.csv        (DRIVE_IDS['cartera_procesada'])
    bi_cliente_credito   data/cliente_cartera.xlsx            (DRIVE_IDS['cliente_cartera'])

COMBINADOS por Python:
    bi_nielsen           consolida los Excel de la carpeta data/nielseiq (DRIVE_IDS['carpeta_nielseiq'])

Los combinados de CUENTAS CLAVE (ventas por retailer + inventarios + tiendas) NO están aquí todavía:
su lógica vive en un notebook exploratorio incompleto (archivado/cuentas_clave.ipynb, solo 4 de ~9
retailers, mezclada con un modelo de reposición). Se portarán cuando se defina la fuente limpia.
"""
import logging
import sys

import pandas as pd

sys.path.insert(0, ".")
from classes.db_loader import DBLoader
from classes.drive_loader import DriveLoader, DRIVE_IDS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SCHEMA = "marts"

# (drive_key, tabla, tipo)  — tipo: 'excel' | 'csv'
DIRECTAS = [
    ("lineas_categorias",  "bi_lineas",           "excel"),
    ("ofertas",            "bi_ofertas",          "excel"),
    ("presupuesto_general", "bi_presupuesto",     "excel"),
    ("clientes_impulso",   "bi_clientes_impulso", "excel"),   # shortcut de Drive (se resuelve)
    ("base_cuentas_clave", "bi_cuentas_clave",    "excel"),
    ("cartera_procesada",  "bi_cartera",          "csv"),
    ("cliente_cartera",    "bi_cliente_credito",  "excel"),
]

# base pyg (base_consolidada.csv) = ~1.09M filas y es REDUNDANTE con el modelo contable del DW
# (marts.fact_movimiento_contable / v_balance_comprobacion). No se migra por defecto: el BI debería
# leer el PyG del DW, no un CSV duplicado. Si de verdad se necesita como tabla, correr aparte.
BASE_PYG = ("base_consolidada", "bi_base_pyg", "csv")


def _cargar_directas(dl, lo, resumen):
    for key, tabla, tipo in DIRECTAS:
        try:
            fid = DRIVE_IDS[key]
            df = dl.read_csv(fid) if tipo == "csv" else dl.read_excel(fid)
            ok = lo.cargar(df, tabla, schema=SCHEMA, if_exists="replace", source_file=key)
            resumen.append((tabla, df.shape[0], df.shape[1], "OK" if ok else "ERROR carga"))
        except Exception as e:
            logging.error(f"{tabla}: {e}")
            resumen.append((tabla, 0, 0, f"ERROR {e}"))


def _leer_nielsen(dl, file_id):
    """Un Excel de Nielsen trae varias filas de TÍTULO antes del encabezado real (número variable
    por archivo). Se detecta la fila de encabezado buscando 'Markets' en la primera columna y se
    reconstruye el DataFrame desde ahí (así las columnas quedan con nombre: CATEGORIA, ITEM,
    Vtas Valor, Vtas Unds, ... en vez de unnamed_*)."""
    buf, _, _ = dl._descargar_bytes(file_id)
    raw = pd.read_excel(buf, header=None)
    col0 = raw[0].astype(str).str.strip().str.lower()
    hit = col0[col0 == "markets"].index
    hrow = int(hit[0]) if len(hit) else 7   # fallback: fila 7 (observado)
    header = [str(x).strip() for x in raw.iloc[hrow].tolist()]
    body = raw.iloc[hrow + 1:].copy()
    body.columns = header
    body = body.dropna(how="all")
    body = body.loc[:, [c for c in body.columns if c and c.lower() != "nan"]]  # sin columnas sin nombre
    if "Periods" in body.columns:  # como el PBIX: solo filas de dato real (descarta subtotales/títulos)
        body = body[body["Periods"].notna()]
    return body


def _cargar_nielsen(dl, lo, resumen):
    tabla = "bi_nielsen"
    try:
        items = dl.list_folder(DRIVE_IDS["carpeta_nielseiq"], "xlsx")
        files = [f for f in items if f["name"].lower().endswith(".xlsx")]
        dfs = []
        for f in files:
            try:
                dfs.append(_leer_nielsen(dl, f["id"]))
                logging.info(f"  nielsen ok: {f['name']}")
            except Exception as e:
                logging.error(f"  nielsen {f['name']}: {e}")
        if not dfs:
            resumen.append((tabla, 0, 0, "VACIO (sin Excel en la carpeta)"))
            return
        df = pd.concat(dfs, ignore_index=True)
        # Reclasificar MARCAS por prefijo del ITEM (igual que el Python.Execute del PBIX):
        # marcas = valores originales de MARCAS; cada ITEM se re-asigna a la marca cuyo nombre
        # es prefijo del ITEM; TONGOLE -> POCION. Se conserva el original en MARCA_ORIGEN.
        if "MARCAS" in df.columns and "ITEM" in df.columns:
            marcas = list(pd.Series(df["MARCAS"]).dropna().unique())
            df["MARCA_ORIGEN"] = df["MARCAS"]

            def _obtener_marca(nombre):
                for m in marcas:
                    if str(nombre).upper().startswith(str(m).upper()):
                        return m
                return "OTRAS MARCAS"

            df["MARCAS"] = df["ITEM"].apply(_obtener_marca)
            df.loc[df["MARCAS"] == "TONGOLE", "MARCAS"] = "POCION"
        ok = lo.cargar(df, tabla, schema=SCHEMA, if_exists="replace", source_file="carpeta_nielseiq")
        resumen.append((tabla, df.shape[0], df.shape[1], "OK" if ok else "ERROR carga"))
    except Exception as e:
        logging.error(f"{tabla}: {e}")
        resumen.append((tabla, 0, 0, f"ERROR {e}"))


def cargar_bi_datasets():
    dl = DriveLoader()
    lo = DBLoader()
    resumen = []

    _cargar_directas(dl, lo, resumen)
    _cargar_nielsen(dl, lo, resumen)

    print("\n" + "=" * 70)
    print(f"RESUMEN — datasets BI cargados en {SCHEMA}.bi_*")
    print("=" * 70)
    print(f"{'tabla':<22}{'filas':>10}{'cols':>7}   estado")
    for tabla, filas, cols, estado in resumen:
        print(f"{tabla:<22}{filas:>10}{cols:>7}   {estado}")


if __name__ == "__main__":
    cargar_bi_datasets()
