"""
Re-siembra `marts.bi_cartera_responsable` desde la hoja `Responsables` de
`base_cartera.xlsx` (Drive). A demanda.

    python cargar_cartera_responsables.py            # carga
    python cargar_cartera_responsables.py --seco     # valida e imprime, no escribe

⚠⚠ POR QUE EXISTE ESTO
La tabla se sembro UNA vez, el 2026-08-01, desde el volcado `bi_cartera` del
pipeline viejo (del 2026-07-23) y no tenia forma de actualizarse. Medido contra
la base el 2026-08-03: el DW tenia 4 responsables y la hoja tiene 6. Faltaban
ANDRES VASQUEZ, MIRIAM BURGOS y MARIA PAULA; sobraba DANIELA DURAN con 2.313 MM.
**El 56 % de la cartera de credito (5.374 MM de 9.621) estaba atribuida al
responsable equivocado o a nadie**, y por tanto fuera del informe de todo el
mundo. Este script cierra ese circuito: la hoja pasa a ser la fuente de verdad y
cambiar un responsable es editarla y volver a correr esto.

⚠⚠ LA COLUMNA `TERCERO_ID` ES LA QUE IMPORTA
El cruce por razon social se cae en silencio, y se le vio caerse dos veces:

  · la hoja dice `FARMATODO COLOMBIA SA` y quien factura en Odoo es `FARMATODO
    COLOMBIA S.A`, con puntos. Un punto dejaba 853.168.462 pesos sin responsable.
    Encima existe un duplicado del mismo cliente SIN ventas, que es justo el que
    casa con el nombre de la hoja.
  · el notebook cablea `C&L SOLUTIONS LLC.` y Odoo dice `C&L SOLUTIONS LLC`, asi
    que ese cliente no recibia sus 120 dias de credito pactados.

Por eso la hoja lleva ahora una columna `TERCERO_ID` y es la llave preferente.
Se rellena SOLO en las filas de nivel cliente; las de nivel tipo cruzan por
`TIPO CLIENTE` y ponerles un id las romperia (un tipo agrupa muchos terceros).

⚠ Las filas que no se puedan resolver se REPORTAN, no se saltan calladas: es
justo lo que hizo que nadie se enterara durante dias.
"""
import argparse
import logging
import sys

import pandas as pd

sys.path.insert(0, ".")
from classes.db_loader import DBLoader
from classes.drive_loader import DriveLoader, DRIVE_IDS
from etl_dw_marts import upsert

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

SCHEMA = "marts"
TABLA = "bi_cartera_responsable"
HOJA = "Responsables"
DRIVE_KEY = "base_cartera"

# Lo que tiene que traer la hoja. `TERCERO_ID` y `UBICACION` son opcionales: la
# hoja funciono anos sin la primera y la segunda no se usa en la MV.
COLS_OBLIGATORIAS = ["TIPO CLIENTE", "CLIENTE", "RESPONSABLE"]

# El valor con el que la hoja marca la fila por defecto.
MARCA_DEFAULT = "Default"


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza cabeceras y valida que estan las columnas obligatorias."""
    df = df.rename(columns={c: str(c).strip().upper() for c in df.columns})
    faltan = [c for c in COLS_OBLIGATORIAS if c not in df.columns]
    if faltan:
        raise KeyError(
            f"Faltan columnas {faltan} en la hoja '{HOJA}'. "
            f"Columnas disponibles: {list(df.columns)}")
    for c in ("TERCERO_ID", "UBICACION", "UBICACIÓN"):
        if c not in df.columns:
            df[c] = None
    # La hoja usa «UBICACIÓN» con tilde; la tabla, sin ella.
    df["UBICACION"] = df["UBICACION"].fillna(df["UBICACIÓN"])
    return df


def _fila(row) -> dict | None:
    """
    Traduce una fila de la hoja a una fila de la tabla, o None si no sirve.

    Las tres formas validas, de mas especifica a menos:
      · con TERCERO_ID          -> cruza por id (la buena)
      · con CLIENTE distinto del tipo -> cruza por razon social (respaldo)
      · solo TIPO CLIENTE       -> cruza por tipo
    """
    tipo = (str(row["TIPO CLIENTE"]).strip()
            if pd.notna(row["TIPO CLIENTE"]) else "")
    cliente = str(row["CLIENTE"]).strip() if pd.notna(row["CLIENTE"]) else ""
    responsable = (str(row["RESPONSABLE"]).strip()
                   if pd.notna(row["RESPONSABLE"]) else "")
    ubicacion = (str(row["UBICACION"]).strip()
                 if pd.notna(row["UBICACION"]) else None)

    if not responsable:
        return None

    tid = row.get("TERCERO_ID")
    try:
        tercero_id = int(tid) if pd.notna(tid) and str(tid).strip() != "" else None
    except (TypeError, ValueError):
        logging.warning("  TERCERO_ID no numerico en '%s': %r. Se ignora el id.",
                        cliente or tipo, tid)
        tercero_id = None

    # La fila por defecto.
    if tipo == MARCA_DEFAULT:
        return {"tercero_id": None, "tipo_cliente": None, "cliente": None,
                "responsable": responsable, "ubicacion": ubicacion,
                "es_default": True,
                "nota": "Fila Default de la hoja Responsables."}

    # ⚠ En la hoja, cuando la fila es de nivel TIPO, la columna CLIENTE repite el
    # nombre del tipo (`MAYORISTA NV` / `MAYORISTA NV`). Si se cargara como
    # `cliente`, no casaria con ningun tercero y esa cartera quedaria sin dueño.
    es_de_tipo = (not cliente) or (cliente.upper() == tipo.upper())

    if es_de_tipo:
        if not tipo:
            return None
        return {"tercero_id": None, "tipo_cliente": tipo, "cliente": None,
                "responsable": responsable, "ubicacion": ubicacion,
                "es_default": False,
                "nota": "Por tipo de cliente (hoja Responsables)."}

    if tercero_id is None:
        logging.warning(
            "  '%s' es una fila de CLIENTE y no trae TERCERO_ID: se cruzara por "
            "razon social, que ya se cayo con FARMATODO y con C&L SOLUTIONS.",
            cliente)

    return {"tercero_id": tercero_id, "tipo_cliente": None, "cliente": cliente,
            "responsable": responsable, "ubicacion": ubicacion,
            "es_default": False,
            "nota": ("Por tercero_id (hoja Responsables)." if tercero_id
                     else "Por razon social (hoja Responsables) - SIN tercero_id.")}


def _validar(filas: list) -> list:
    """
    Avisa de lo que dejaria la carga en un estado incoherente.

    No aborta: la tabla tiene indices unicos parciales que rechazarian los
    duplicados de verdad. Esto es para que se vea ANTES, no despues.
    """
    problemas = []
    for llave, etiqueta in (("tercero_id", "tercero_id"),
                            ("cliente", "cliente"),
                            ("tipo_cliente", "tipo de cliente")):
        vistos = {}
        for f in filas:
            v = f.get(llave)
            if v is None:
                continue
            if v in vistos and vistos[v] != f["responsable"]:
                problemas.append(
                    f"{etiqueta} '{v}' aparece con dos responsables distintos: "
                    f"{vistos[v]} y {f['responsable']}")
            vistos[v] = f["responsable"]

    defaults = [f for f in filas if f["es_default"]]
    if len(defaults) > 1:
        problemas.append(f"hay {len(defaults)} filas Default y solo puede haber una")
    if not defaults:
        problemas.append(
            "no hay fila Default: los clientes que no casen quedaran en "
            "'(sin responsable)'")
    return problemas


def cargar(seco: bool = False) -> dict:
    """Lee la hoja, la traduce y re-siembra la tabla. Devuelve un resumen."""
    resumen = {"leidas": 0, "cargadas": 0, "por_id": 0, "por_cliente": 0,
               "por_tipo": 0, "default": 0, "descartadas": 0, "problemas": []}

    try:
        dl = DriveLoader()
        df = dl.read_excel(DRIVE_IDS[DRIVE_KEY], sheet_name=HOJA)
    except Exception as exc:                                # noqa: BLE001
        logging.error("No se pudo leer la hoja '%s' de Drive: %s", HOJA, exc)
        resumen["problemas"].append(f"lectura de Drive: {exc}")
        return resumen

    try:
        df = _norm(df)
    except KeyError as exc:
        logging.error("%s", exc)
        resumen["problemas"].append(str(exc))
        return resumen

    resumen["leidas"] = len(df)
    filas = []
    for _, row in df.iterrows():
        f = _fila(row)
        if f is None:
            resumen["descartadas"] += 1
            continue
        filas.append(f)
        if f["es_default"]:
            resumen["default"] += 1
        elif f["tercero_id"] is not None:
            resumen["por_id"] += 1
        elif f["cliente"]:
            resumen["por_cliente"] += 1
        else:
            resumen["por_tipo"] += 1

    resumen["problemas"] = _validar(filas)
    for p in resumen["problemas"]:
        logging.warning("  [aviso] %s", p)

    if not filas:
        logging.error("La hoja no produjo ninguna fila util; no se toca la tabla.")
        return resumen

    if seco:
        print("\n(seco) filas que se cargarian:")
        for f in filas:
            llave = (f"id={f['tercero_id']}" if f["tercero_id"]
                     else f"cliente={f['cliente']}" if f["cliente"]
                     else f"tipo={f['tipo_cliente']}" if f["tipo_cliente"]
                     else "DEFAULT")
            print(f"  {llave:<45} -> {f['responsable']}")
        return resumen

    # ⚠ `reemplazar=True`: TRUNCATE + INSERT en la MISMA transaccion. Es lo que
    # hace que quitar a alguien de la hoja lo quite de verdad de la tabla — con
    # un upsert normal, DANIELA DURAN seguiria ahi para siempre. Y al ir en una
    # sola transaccion, nadie ve la tabla vacia por el camino.
    try:
        loader = DBLoader()
        n = upsert(loader, pd.DataFrame(filas), TABLA, pk="id",
                   schema=SCHEMA, reemplazar=True)
        resumen["cargadas"] = n
    except Exception as exc:                                # noqa: BLE001
        logging.error("Fallo al escribir %s.%s: %s", SCHEMA, TABLA, exc)
        resumen["problemas"].append(f"escritura: {exc}")

    return resumen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seco", action="store_true",
                    help="Valida e imprime lo que haria, sin escribir.")
    args = ap.parse_args()

    r = cargar(seco=args.seco)

    print("\n" + "=" * 70)
    print(f"RESUMEN - responsables de cartera{'  (SECO)' if args.seco else ''}")
    print("=" * 70)
    print(f"  filas en la hoja ....... {r['leidas']}")
    print(f"  por tercero_id ......... {r['por_id']}")
    print(f"  por razon social ....... {r['por_cliente']}")
    print(f"  por tipo de cliente .... {r['por_tipo']}")
    print(f"  fila Default ........... {r['default']}")
    print(f"  descartadas ............ {r['descartadas']}")
    print(f"  cargadas ............... {r['cargadas']}")
    if r["problemas"]:
        print(f"\n  {len(r['problemas'])} aviso(s):")
        for p in r["problemas"]:
            print(f"    - {p}")

    if not args.seco and r["cargadas"]:
        print("\nAhora hay que refrescar la MV para que la intranet lo vea:")
        print("  python refrescar_mv_dashboards.py --mv mv_cartera_saldo")
        print("Y comprobarlo desde la intranet:")
        print("  python manage.py check_marts   (la seccion 7r debe pasar a verde)")


if __name__ == "__main__":
    main()
