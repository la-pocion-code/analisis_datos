"""
cargar_bi_datasets.py — Sube a `marts.bi_*` los datasets que el BI (Power BI) todavía consume desde
archivos locales, para DESCONECTAR el BI de archivos locales. Fuente: Google Drive (DriveLoader),
igual patrón que cargar_mapeos.py. Recarga completa (if_exists='replace') como los map_*.

Se corre A DEMANDA (cuando cambie un archivo en Drive):

    python cargar_bi_datasets.py                                  # todo (~4 min, casi todo Nielsen)
    python cargar_bi_datasets.py --dataset presupuesto_general    # SOLO ese archivo (segundos)
    python cargar_bi_datasets.py --dataset bi_presupuesto         # igual: acepta el nombre de tabla
    python cargar_bi_datasets.py --listar                         # qué claves existen
    python cargar_bi_datasets.py --sin-refresco                   # cargar sin refrescar las MV

Al terminar refresca SOLO las vistas materializadas que dependen de lo que se cargó
(`refrescar_mv_dashboards.MVS_POR_TABLA`). Sin eso, recargar la tabla no cambia nada en el
tablero hasta el siguiente tick del cron — y en Nielsen y cuentas clave el cron solo las
refresca en el tick :00, o sea hasta una hora después.

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

⚠ RECARGAR UNA TABLA QUE TIENE UNA MV ENCIMA: `bi_presupuesto` y `bi_nielsen` alimentan MV de
los dashboards, así que `DROP TABLE` es IMPOSIBLE (Postgres lo rechaza sin CASCADE, y CASCADE se
llevaría las MV y sus GRANT a intranet_ro). `DBLoader.cargar` lo detecta solo y recarga con
TRUNCATE; aquí no hay nada que hacer. Es lo que rompió la carga del presupuesto entre el 28-jul y
el 3-ago-2026: el DROP fallaba, no se insertaba nada y la tabla se quedaba con el dato viejo.

Los combinados de CUENTAS CLAVE (ventas por retailer + inventarios + tiendas) NO están aquí todavía:
su lógica vive en un notebook exploratorio incompleto (archivado/cuentas_clave.ipynb, solo 4 de ~9
retailers, mezclada con un modelo de reposición). Se portarán cuando se defina la fuente limpia.
"""
import argparse
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

NIELSEN_KEY   = "carpeta_nielseiq"
NIELSEN_TABLA = "bi_nielsen"

# base pyg (base_consolidada.csv) = ~1.09M filas y es REDUNDANTE con el modelo contable del DW
# (marts.fact_movimiento_contable / v_balance_comprobacion). No se migra por defecto: el BI debería
# leer el PyG del DW, no un CSV duplicado. Si de verdad se necesita como tabla, correr aparte.
BASE_PYG = ("base_consolidada", "bi_base_pyg", "csv")


def _alias() -> dict:
    """
    Alias aceptados por --dataset -> clave de Drive.

    Se acepta también el NOMBRE DE TABLA porque es lo que el usuario ve en el resumen y
    en el error: quien lee `bi_presupuesto  348  10  ERROR` va a escribir `bi_presupuesto`,
    no `presupuesto_general`.
    """
    m = {}
    for key, tabla, _ in DIRECTAS:
        m[key] = key
        m[tabla] = key
    m[NIELSEN_KEY] = NIELSEN_KEY
    m[NIELSEN_TABLA] = NIELSEN_KEY
    m["nielsen"] = NIELSEN_KEY
    return m


def _resolver(valores) -> set:
    """Traduce lo que pasó el usuario a claves de Drive. Sale con error si algo no existe."""
    alias, seleccion, malos = _alias(), set(), []
    for v in valores:
        clave = alias.get(v.strip())
        if clave:
            seleccion.add(clave)
        else:
            malos.append(v)
    if malos:
        print(f"ERROR: dataset desconocido: {', '.join(malos)}")
        print("Válidos (clave de Drive | tabla):")
        _listar()
        raise SystemExit(2)
    return seleccion


def _listar() -> None:
    for key, tabla, tipo in DIRECTAS:
        print(f"  {key:<20} | {tabla:<22} ({tipo})")
    print(f"  {NIELSEN_KEY:<20} | {NIELSEN_TABLA:<22} (carpeta de Excel)")


def _cargar_directas(dl, lo, resumen, seleccion=None):
    for key, tabla, tipo in DIRECTAS:
        if seleccion is not None and key not in seleccion:
            continue
        try:
            fid = DRIVE_IDS[key]
            df = dl.read_csv(fid) if tipo == "csv" else dl.read_excel(fid)
            ok = lo.cargar(df, tabla, schema=SCHEMA, if_exists="replace", source_file=key)
            estado = "OK" if ok else f"ERROR {lo.ultimo_error or 'carga'}"
            resumen.append((tabla, df.shape[0], df.shape[1], estado))
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
    tabla = NIELSEN_TABLA
    try:
        items = dl.list_folder(DRIVE_IDS[NIELSEN_KEY], "xlsx")
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
        ok = lo.cargar(df, tabla, schema=SCHEMA, if_exists="replace", source_file=NIELSEN_KEY)
        estado = "OK" if ok else f"ERROR {lo.ultimo_error or 'carga'}"
        resumen.append((tabla, df.shape[0], df.shape[1], estado))
    except Exception as e:
        logging.error(f"{tabla}: {e}")
        resumen.append((tabla, 0, 0, f"ERROR {e}"))


def _refrescar_mv(resumen):
    """
    Refresca las MV que dependen de las tablas que cargaron BIEN.

    En try/except a propósito: la carga ya está commiteada y es lo importante. Si el
    refresco falla, el cron lo reintenta en el siguiente tick; hacer fallar el script
    aquí solo haría creer que no se cargó nada.
    """
    cargadas = [tabla for tabla, _, _, estado in resumen if estado == "OK"]
    if not cargadas:
        return
    try:
        from refrescar_mv_dashboards import refrescar_dependientes
        res = refrescar_dependientes(cargadas)
        if res["fallidas"]:
            print("\nAVISO: MV que no se refrescaron: "
                  + ", ".join(mv for mv, _ in res["fallidas"]))
    except Exception as e:                                    # noqa: BLE001
        logging.error(f"refresco de MV: {e}")
        print(f"\nAVISO: la carga terminó bien pero el refresco de MV falló: {e}")
        print("       Los tableros se actualizan igual en el siguiente tick del cron.")


def cargar_bi_datasets(datasets=None, refrescar_mv=True):
    """
    datasets:     None = todos. Lista de claves de Drive o nombres de tabla = solo esos.
    refrescar_mv: refrescar al final las MV que dependen de lo cargado.
    """
    seleccion = _resolver(datasets) if datasets else None

    dl = DriveLoader()
    lo = DBLoader()
    resumen = []

    _cargar_directas(dl, lo, resumen, seleccion)
    if seleccion is None or NIELSEN_KEY in seleccion:
        _cargar_nielsen(dl, lo, resumen)

    print("\n" + "=" * 70)
    print(f"RESUMEN — datasets BI cargados en {SCHEMA}.bi_*")
    print("=" * 70)
    print(f"{'tabla':<22}{'filas':>10}{'cols':>7}   estado")
    for tabla, filas, cols, estado in resumen:
        # El motivo va en su propia línea: antes el resumen decía solo "ERROR carga" con
        # las filas al lado, que se lee como éxito, y el detalle quedaba en db_loader.log.
        corto = "OK" if estado == "OK" else "ERROR"
        print(f"{tabla:<22}{filas:>10}{cols:>7}   {corto}")
        if estado != "OK":
            print(f"{'':<39}   └─ {estado[6:] if estado.startswith('ERROR ') else estado}")

    if refrescar_mv:
        _refrescar_mv(resumen)

    return resumen


def main():
    ap = argparse.ArgumentParser(
        description="Sube a marts.bi_* los datasets del BI que viven en Google Drive.")
    ap.add_argument("--dataset", action="append", metavar="CLAVE",
                    help="Cargar solo este dataset (repetible). Acepta la clave de Drive "
                         "(presupuesto_general) o el nombre de tabla (bi_presupuesto). "
                         "Por defecto: todos.")
    ap.add_argument("--listar", action="store_true", help="Listar los datasets disponibles y salir.")
    ap.add_argument("--sin-refresco", action="store_true",
                    help="No refrescar las MV dependientes al terminar.")
    args = ap.parse_args()

    if args.listar:
        print("Datasets disponibles (clave de Drive | tabla):")
        _listar()
        return

    cargar_bi_datasets(datasets=args.dataset, refrescar_mv=not args.sin_refresco)


if __name__ == "__main__":
    main()
