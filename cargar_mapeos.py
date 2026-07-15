"""
cargar_mapeos.py — Carga los mapeos de negocio de VENTAS que NO viven en Odoo (Fase 4 del DW).

Lee los Excel de Drive (vía DriveLoader, service account) y los deja en el esquema `marts`:
    marts.map_zona               (departamento, categoria) -> zona
    marts.map_zona_cundinamarca  (departamento, ciudad, categoria) -> zona
    marts.map_cliente_padre      (cliente) -> cliente_padre
    marts.map_categoria          (categoria_origen) -> categoria_bi   [dict de renombrado, en código]

    marts.map_zona_bogota — DEPRECADO: `Base_bogota.xlsx` ya no se usa (vacío en Drive). La tabla
    queda creada pero vacía; este script ya no la carga.

Replica el enriquecimiento local de ReportClassNew.transformar_base() pero como tablas del DW.
Requiere el DDL sql/marts/16_mapeos_ventas.sql aplicado. Se corre A DEMANDA (cuando cambie un Excel):

    python cargar_mapeos.py

Regla del proyecto: es el ÚNICO insumo NO-Odoo del DW (mapeos comerciales). Cada tabla se recrea
completa (TRUNCATE + insert) para reflejar filas eliminadas en el Excel.
"""
import sys
import logging

import pandas as pd

sys.path.insert(0, ".")
from classes.db_loader import DBLoader
from classes.drive_loader import DriveLoader, DRIVE_IDS
from etl_dw_marts import upsert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Normalización del vocabulario de CATEGORÍA (tipo de cliente) → marts.map_categoria.
# Se aplica al final de consolidar_categoria() (etl_dw_marts.py). Editable en la tabla sin tocar código.
CATEGORIAS_RENOMBRAR = {
    # 1) Renombrado del Excel (ReportClassNew.transformar_base, categorias_renombrar)
    "Catalogo": "CATÁLOGO",
    "Distribuidor": "DISTRIBUIDOR",
    "Empleado": "EMPLEADO",
    "FARMACIAS": "FARMACIA",
    "HOLE COSMETICS": "HOLE COSMETICS SAS",
    "Surticosmeticos": "SURTICOSMETICOS",
    # 2) Vocabulario del analítico (plan 21 "Canal" = x_plan21_id) → mismo vocabulario que arriba.
    #    Solo aplica cuando el tercero no trae tipo_cliente (el analítico rellena).
    "Cliente Final": "CALL CENTER",
    "Mayoristas": "MAYORISTA NV",
    "Distribuidores": "DISTRIBUIDOR",
    "Farmacia": "FARMACIA",
    "Coopidrogas": "COOPIDROGAS",
    "Catálogo": "CATÁLOGO",
}


def _norm(df, columnas):
    """Selecciona/renombra columnas Excel→destino, castea a texto y descarta filas con clave nula."""
    faltan = [c for c in columnas if c not in df.columns]
    if faltan:
        raise KeyError(f"Faltan columnas {faltan}. Columnas disponibles: {list(df.columns)}")
    out = df[list(columnas)].rename(columns=columnas)
    for c in out.columns:
        out[c] = out[c].astype("string").str.strip()
    return out


def _recargar(loader, df, tabla, pk):
    """TRUNCATE + insert (recarga completa). Descarta filas con PK nula y duplicados de PK."""
    pks = [pk] if isinstance(pk, str) else list(pk)
    df = df.dropna(subset=pks).drop_duplicates(subset=pks)
    df = df.where(pd.notnull(df), None)
    with loader.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"TRUNCATE marts.{tabla};")
        conn.commit()
    n = upsert(loader, df, tabla, pk)
    logging.info(f"  {tabla}: {n} filas")


def cargar_mapeos():
    dl = DriveLoader()
    loader = DBLoader()

    # 1) ZONA general: DEPARTAMENTO + CATEGORÍA -> zona
    zonas = dl.read_excel(DRIVE_IDS["zonas"])
    _recargar(loader, _norm(zonas, {"DEPARTAMENTO": "departamento", "CATEGORÍA": "categoria",
                                    "zona": "zona"}),
              "map_zona", ["departamento", "categoria"])

    # 2) ZONA Cundinamarca: DEPARTAMENTO + CIUDAD + CATEGORÍA -> zona
    cundi = dl.read_excel(DRIVE_IDS["zonas_cundinamarca"])
    _recargar(loader, _norm(cundi, {"DEPARTAMENTO": "departamento", "CIUDAD": "ciudad",
                                    "CATEGORÍA": "categoria", "ZONA_CUNDINAMARCA": "zona"}),
              "map_zona_cundinamarca", ["departamento", "ciudad", "categoria"])

    # 3) ZONA Bogotá: DEPRECADO — `Base_bogota.xlsx` ya no se usa (está vacío en Drive).
    #    marts.map_zona_bogota se deja creada pero vacía. Si vuelve a usarse, restaurar aquí.

    # 4) CLIENTE PADRE: CLIENTE -> CLIENTE PADRE
    padres = dl.read_excel(DRIVE_IDS["clientes_padres"])
    _recargar(loader, _norm(padres, {"CLIENTE": "cliente", "CLIENTE PADRE": "cliente_padre"}),
              "map_cliente_padre", "cliente")

    # 5) CATEGORÍA normalizada: dict de renombrado (en código, no Excel)
    cat = pd.DataFrame({"categoria_origen": list(CATEGORIAS_RENOMBRAR),
                        "categoria_bi": list(CATEGORIAS_RENOMBRAR.values())})
    _recargar(loader, cat, "map_categoria", "categoria_origen")

    logging.info("OK: mapeos de ventas cargados en marts.")


if __name__ == "__main__":
    cargar_mapeos()
