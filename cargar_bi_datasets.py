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
    python cargar_bi_datasets.py --dataset nielsen --seco         # validar SIN escribir

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

⚠ NIELSEN — tres cosas que hace el loader y que no son evidentes:
  1. La MARCA se deduce del prefijo del ITEM (no se usa la columna MARCAS tal cual), y las
     variantes de la casa se unifican con `ALIAS_MARCA` en MARCAS **y** FABRICANTES.
     `MARCA_ORIGEN` guarda la etiqueta original.
  2. Se ABORTA si hay filas duplicadas sobre la clave natural: casi siempre significa que en
     la carpeta de Drive quedaron los archivos del export anterior, y cargarlos doblaria en
     silencio los valores de `mv_nielsen_semana`.
  3. Tarda ~8,5 min (573k filas) y deja la tabla con lock exclusivo mientras inserta: no
     lanzarla en el tick `:00` del cron, y usar `--seco` antes si el export cambio.

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

# ── NIELSEN: marcas que son LA MISMA CASA con otro nombre ──────────────────────
# Nielsen no usa un nombre estable para nuestra marca, y cada variante que no se unifique
# sale del share de marca propia (`marts.bi_nielsen_marca_propia` solo lista 'POCION') y
# aparece en el ranking como si fuera competencia.
#
#   TONGOLE     — la sub-marca; ya se unificaba antes con un `df.loc[...]` suelto.
#   PCN POCION  — medido 2026-08-04: es UN producto, `PCN POCION DEFENSA TOTAL ANTICASPA
#                 BOTELLA 450ML`, desde la semana que cierra el 08/03/26, en FARMACIAS y
#                 ECOMMERCE. "PCN" es el codigo de la empresa (PCN Pocion, empresa 8 de
#                 Odoo) y coincide con el lanzamiento de la linea Control Caspa.
#
# ⚠ Se aplica a MARCAS **y a FABRICANTES**: Nielsen puso 'PCN POCION' en las dos columnas
# (los 16 items de POCION traen fabricante 'POCION'), y el grano de las dos MV incluye
# `fabricante` — unificar solo la marca dejaria la misma casa partida en dos en cualquier
# vista por fabricante.
#
# ⚠ NO se hace con una regla generica de prefijo ("todo lo que empiece por PCN"): si
# Nielsen usara 'PCN' para algo ajeno se fusionaria en silencio. Cada equivalencia va
# escrita aqui. `MARCA_ORIGEN` conserva la etiqueta original y `mv_nielsen_item_semana`
# ya la expone, asi que el rastro no se pierde.
ALIAS_MARCA = {
    "TONGOLE":    "POCION",
    "PCN POCION": "POCION",
}

# Clave natural de una fila de Nielsen = la MISMA del indice unico
# `ux_mv_nielsen_item_semana`. Se usa para detectar duplicados antes de cargar (ver
# _duplicados_nielsen).
CLAVE_NIELSEN = (
    "markets", "periods", "categoria", "fabricantes", "marcas", "item", "upc",
    "presentacion_unif", "tipo_unif", "promocionno_promocion_unif",
)

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


def _validar_seco(lo, df, tabla):
    """
    Valida (SIN escribir) que el archivo se pueda cargar sobre la tabla que ya existe.

    Existe porque desde el arreglo de `DBLoader.cargar` una tabla con MV encima se recarga con
    TRUNCATE, y si el archivo origen PERDIO o RENOMBRO una columna la recarga se aborta a
    proposito. En Nielsen eso se descubriria despues de 8,5 minutos de carga: mejor saberlo
    antes. Devuelve el estado para el resumen.
    """
    canon = lo._limpiar_columnas(df)
    cols_df = set(canon.columns) | {"_loaded_at", "_source_file"}
    fila = lo.consultar(
        """SELECT string_agg(column_name, ',' ORDER BY ordinal_position) c
             FROM information_schema.columns
            WHERE table_schema = %(s)s AND table_name = %(t)s""",
        {"s": SCHEMA, "t": tabla},
    )
    if fila is None or fila.empty:
        return "SECO: no se pudo leer las columnas del destino"
    destino = set((fila.iloc[0]["c"] or "").split(",")) - {""}
    if not destino:
        return "SECO: la tabla no existe todavia; la cargaria creandola"

    faltan = sorted(c for c in destino - cols_df if c != "id")
    nuevas = sorted(cols_df - destino)
    partes = []
    if faltan:
        partes.append(f"FALTAN en el archivo: {', '.join(faltan)} -> la carga se ABORTARIA")
    if nuevas:
        partes.append(f"columnas nuevas (se agregarian): {', '.join(nuevas)}")
    return "SECO: " + ("; ".join(partes) if partes else "cargaria sin problemas (columnas iguales)")


def _cargar_directas(dl, lo, resumen, seleccion=None, seco=False):
    for key, tabla, tipo in DIRECTAS:
        if seleccion is not None and key not in seleccion:
            continue
        try:
            fid = DRIVE_IDS[key]
            df = dl.read_csv(fid) if tipo == "csv" else dl.read_excel(fid)
            if seco:
                resumen.append((tabla, df.shape[0], df.shape[1], _validar_seco(lo, df, tabla)))
                continue
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


def _reclasificar_marcas(df):
    """
    Reasigna MARCAS por el prefijo del ITEM (igual que el Python.Execute del PBIX) y unifica
    las variantes de la casa con ALIAS_MARCA. Conserva el original en MARCA_ORIGEN.

    ⚠ Las marcas se recorren de la MAS LARGA a la mas corta. Antes se recorrian en orden de
    aparicion en los datos y se devolvia el primer prefijo que casaba: con dos marcas donde
    una es prefijo de la otra ('POCION' y un futuro 'POCION PRO'), el resultado dependia de
    que fila viniera primero en el concat de los 9 Excel, o sea que la MISMA carga podia
    clasificar distinto entre corridas. Por longitud gana siempre la mas especifica.
    """
    if "MARCAS" not in df.columns or "ITEM" not in df.columns:
        return df

    marcas = sorted((str(m) for m in pd.Series(df["MARCAS"]).dropna().unique()),
                    key=len, reverse=True)
    df["MARCA_ORIGEN"] = df["MARCAS"]

    def _obtener_marca(nombre):
        texto = str(nombre).upper()
        for m in marcas:
            if texto.startswith(m.upper()):
                return m
        return "OTRAS MARCAS"

    df["MARCAS"] = df["ITEM"].apply(_obtener_marca)

    # Unificar la casa. Se aplica tambien a FABRICANTES porque Nielsen puso 'PCN POCION' en
    # las dos columnas y el grano de las dos MV incluye fabricante.
    #
    # ⚠ Solo se reescriben las filas QUE TIENEN alias: el resto queda byte a byte como vino.
    # Normalizar la columna entera (un .str.upper() de paso, por ejemplo) cambiaria la
    # etiqueta de marcas ajenas y podria colapsar dos que solo difieran en mayusculas,
    # moviendo el conteo de marcas del cuadre sin que nadie lo pidiera.
    for col in ("MARCAS", "FABRICANTES"):
        if col not in df.columns:
            continue
        nuevo = df[col].astype(str).str.strip().str.upper().map(ALIAS_MARCA)
        cambia = nuevo.notna() & (nuevo != df[col])
        if cambia.any():
            df.loc[cambia, col] = nuevo[cambia]
            logging.info(f"  alias de marca aplicado en {col}: {int(cambia.sum())} filas")
    return df


def _duplicados_nielsen(lo, df):
    """
    Filas duplicadas sobre la clave natural (la misma de `ux_mv_nielsen_item_semana`).

    ⚠ POR QUE SE MIRA: la carpeta de Drive se mantiene a mano (3 archivos por categoria). Si
    alguna vez se suben los nuevos SIN borrar los viejos, el dano es asimetrico y medio
    invisible: `mv_nielsen_item_semana` fallaria al refrescar (viola su indice unico), pero
    `mv_nielsen_semana` **doblaria los valores en silencio**, porque agrega con GROUP BY y
    sumaria cada fila dos veces.

    Se comparan los valores en crudo, asi que detecta copias exactas (el caso del archivo
    repetido); dos filas que solo difieran en espacios finales no las ve, igual que tampoco
    las distinguiria el indice de la MV.
    """
    canon = lo._limpiar_columnas(df)
    cols = [c for c in CLAVE_NIELSEN if c in canon.columns]
    if not cols:
        return 0, []
    return int(canon.duplicated(subset=cols, keep=False).sum()), cols


def _cargar_nielsen(dl, lo, resumen, seco=False):
    tabla = NIELSEN_TABLA
    try:
        items = dl.list_folder(DRIVE_IDS[NIELSEN_KEY], "xlsx")
        files = [f for f in items if f["name"].lower().endswith(".xlsx")]
        logging.info(f"nielsen: {len(files)} archivos en la carpeta")
        dfs = []
        for f in files:
            try:
                parte = _leer_nielsen(dl, f["id"])
                dfs.append(parte)
                logging.info(f"  nielsen ok: {f['name']} ({len(parte):,} filas)")
            except Exception as e:
                logging.error(f"  nielsen {f['name']}: {e}")
        if not dfs:
            resumen.append((tabla, 0, 0, "VACIO (sin Excel en la carpeta)"))
            return
        df = pd.concat(dfs, ignore_index=True)
        df = _reclasificar_marcas(df)

        ndup, cols = _duplicados_nielsen(lo, df)
        if ndup:
            msg = (f"{ndup} filas duplicadas sobre la clave natural ({', '.join(cols)}). "
                   f"Casi siempre son archivos repetidos en la carpeta de Drive: revisa que "
                   f"no queden los del export anterior. No se carga, para no doblar los "
                   f"valores de mv_nielsen_semana en silencio")
            logging.error(f"{tabla}: {msg}")
            resumen.append((tabla, df.shape[0], df.shape[1], f"ERROR {msg}"))
            return

        if seco:
            resumen.append((tabla, df.shape[0], df.shape[1], _validar_seco(lo, df, tabla)))
            return

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


def cargar_bi_datasets(datasets=None, refrescar_mv=True, seco=False):
    """
    datasets:     None = todos. Lista de claves de Drive o nombres de tabla = solo esos.
    refrescar_mv: refrescar al final las MV que dependen de lo cargado.
    seco:         validar sin escribir nada (columnas contra el destino + duplicados).
    """
    seleccion = _resolver(datasets) if datasets else None

    dl = DriveLoader()
    lo = DBLoader()
    resumen = []

    _cargar_directas(dl, lo, resumen, seleccion, seco)
    if seleccion is None or NIELSEN_KEY in seleccion:
        _cargar_nielsen(dl, lo, resumen, seco)

    print("\n" + "=" * 70)
    print(f"RESUMEN — {'VALIDACION EN SECO (no se escribio nada)' if seco else f'datasets BI cargados en {SCHEMA}.bi_*'}")
    print("=" * 70)
    print(f"{'tabla':<22}{'filas':>10}{'cols':>7}   estado")
    for tabla, filas, cols, estado in resumen:
        # El motivo va en su propia línea: antes el resumen decía solo "ERROR carga" con
        # las filas al lado, que se lee como éxito, y el detalle quedaba en db_loader.log.
        if estado == "OK":
            corto = "OK"
        elif estado.startswith("SECO"):
            corto = "SECO"
        else:
            corto = "ERROR"
        print(f"{tabla:<22}{filas:>10}{cols:>7}   {corto}")
        if estado != "OK":
            detalle = estado.split(": ", 1)[-1] if estado.startswith("SECO") else \
                      (estado[6:] if estado.startswith("ERROR ") else estado)
            print(f"{'':<39}   └─ {detalle}")

    if refrescar_mv and not seco:      # en seco no se escribio nada: no hay que refrescar
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
    ap.add_argument("--seco", action="store_true",
                    help="Validar SIN escribir: compara las columnas del archivo contra la "
                         "tabla destino y busca duplicados. Util antes de una carga larga "
                         "(Nielsen tarda ~8,5 min y bloquea la tabla).")
    args = ap.parse_args()

    if args.listar:
        print("Datasets disponibles (clave de Drive | tabla):")
        _listar()
        return

    cargar_bi_datasets(datasets=args.dataset, refrescar_mv=not args.sin_refresco, seco=args.seco)


if __name__ == "__main__":
    main()
