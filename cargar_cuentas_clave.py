"""
cargar_cuentas_clave.py — Reproduce en `marts` las tablas de CUENTAS CLAVE que hoy el BI arma en
Power Query desde archivos LOCALES, leyendo ahora desde Google Drive (para poder correr en Railway y
que Power BI lea de Postgres). Ver el proceso completo en docs/cuentas_clave_migracion.md.

FASE 1 (este archivo, en construcción): VENTAS de PAÍSES (ecuador, peru, dominicana), portados
FIELMENTE del Power Query del PBIX (varios usan Python embebido). Esquema común de salida:
    CLIENTE, FECHA, PRODUCTO, NOMBRE_TIENDA, UNIDADES (+ CANAL VENTA, VENDEDOR, VALORES según país)

Uso (validación, sin cargar aún):  python cargar_cuentas_clave.py
"""
import io
import logging
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from classes.drive_loader import DriveLoader, DRIVE_IDS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# IDs de Drive descubiertos (subcarpetas de data/paises y archivos de segmentación)
FOLDER_ECUADOR = "1Gjklp9qgKk4v5d-eIp3tUR72Ksw0LFZ9"
FOLDER_PERU = "1uf_D-WoQ9NnHil-cvy4n-cBV4mP20ko0"
FOLDER_RD = "1T3e2JqxI4Lh9PEI3fInLRhA0MzH35JlW"
SEG_ECUADOR = "1bz0vRdiOUYrucSkZd4glUSY2JZXZ-lfC"        # SEGMENTO DE CLIENTES.xlsx (hoja 'Tipo de cliente')
SEG_DOMINICANA = "1wWT4WJZnocluVbBtXj5idRSLABFr_lkn"     # SEGMENTO DE CLIENTES- DOMINICANA.xlsx (Hoja1)
SEG_PERU = "1VTg8zSMrzGK4DMFM_qiLNLsYlo_pD9dG"          # SEGMENTO DE CLIENTES- PERÚ.xlsx (Hoja1)

# Esquema unificado = las columnas de la tabla CUENTAS_CLAVE ANEXO del PBIX (para poder repuntar sin
# romper medidas/visuales). Cada retailer aporta las que tiene; el resto quedan nulas.
COMUN = ["CLIENTE", "FECHA", "PRODUCTO", "NOMBRE_TIENDA", "UNIDADES", "CANAL VENTA", "VENDEDOR",
         "VALORES", "CIUDAD", "NOMBRE_PRODUCTO", "TIENDA", "Nombre", "Campaña", "Sucursal"]


# ── lecturas de apoyo desde Drive ────────────────────────────────────────────
def _bytes(dl, file_id):
    buf, _, _ = dl._descargar_bytes(file_id)
    return buf


def leer_base(dl):
    """BASE_CUENTAS_CLAVE (códigos por retailer → PRODUCTO). Desde Drive, columnas originales."""
    return pd.read_excel(_bytes(dl, DRIVE_IDS["base_cuentas_clave"]))


def leer_kits(dl):
    return pd.read_excel(_bytes(dl, DRIVE_IDS["kits"]))  # kits.xlsx → cols KIT, PRODUCTO


def _archivos_carpeta(dl, folder_id, exts=(".xls", ".xlsx")):
    items = dl.list_folder(folder_id)
    return [it for it in items if it["name"].lower().endswith(exts)]


# ── ECUADOR (Python embebido del PBIX: descarta filas con fuente ROJA) ────────
def ventas_ecuador(dl, base):
    import openpyxl
    ROJO = "FFFF0000"
    dfs = []
    for f in _archivos_carpeta(dl, FOLDER_ECUADOR, (".xlsx",)):
        wb = openpyxl.load_workbook(_bytes(dl, f["id"]), data_only=True)
        sheet = wb.active
        filas = []
        for row in sheet.iter_rows():
            color = row[0].font.color
            if color and color.rgb == ROJO:
                continue
            filas.append([c.value for c in row])
        if filas:
            dfs.append(pd.DataFrame(filas[1:], columns=filas[0]))
    df = pd.concat(dfs, ignore_index=True)
    df = df[~df["Fecha"].isna()]
    df.loc[df["Tipo"] == "DEV", "Cant."] *= -1
    df.loc[df["Tipo"] == "DEV", "Sub. - Dscto."] *= -1
    df = df[~df["Vendedor"].str.contains("muestras|publicidad", case=False, na=False)]
    df = df[(df["Vendedor"] != "ZARATE CARDENAS JORGE ENRIQUE") & (df["Cliente"] != "ZARATE CARDENAS JORGE")]
    # pasos M
    excluir = ["CAJAS PLEGADIZAS POCION", "COSMETIQUERA FUCCIA POCION",
               "PRACTICE HAND 12PCS  / BULTO 80PCS COD ZAR-ML80", "SACHET EMUGEL RIZOS TONGOLE 15ML",
               "SACHET LEAVE ON TONGOLE 15ML DEFINE Y VITALIZA", "SACHET MSC ANCESTRAL 15ML COD 555057",
               "SACHET SHAMPOO TONGOLE 15ML HIDRATACION Y LIMPIEZA 15ML", "THERMO LA POCION FUCSIA"]
    df = df[~df["Producto"].isin(excluir)].copy()
    df["Código"] = pd.to_numeric(df["Código"], errors="coerce").astype("Int64")
    df = df.merge(base[["cod_ecuador", "PRODUCTO"]], left_on="Código", right_on="cod_ecuador", how="left")
    try:
        seg = pd.read_excel(_bytes(dl, SEG_ECUADOR), sheet_name="Tipo de cliente")
        df = df.merge(seg[["Cliente", "Segmento"]], on="Cliente", how="left")
    except Exception as e:
        logging.warning(f"ecuador: segmentación no disponible en Drive ({e}); CANAL VENTA quedará nulo")
        df["Segmento"] = None
    df["CLIENTE"] = "ZAR IMPORT ZARIMPORT S.A."
    df = df.rename(columns={"Cant.": "UNIDADES", "Fecha": "FECHA", "Cliente": "NOMBRE_TIENDA",
                            "Vendedor": "VENDEDOR", "Segmento": "CANAL VENTA", "Sub. - Dscto.": "VALORES"})
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    return df.reindex(columns=COMUN)


# ── PERÚ (Python embebido: read_excel + concat; luego joins BASE/kits/segmento) ──
def ventas_peru(dl, base, kits):
    dfs = [pd.read_excel(_bytes(dl, f["id"])) for f in _archivos_carpeta(dl, FOLDER_PERU)]
    df = pd.concat(dfs, ignore_index=False, axis=0)
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.merge(base[["peru", "PRODUCTO"]], left_on="Material", right_on="peru", how="left")
    df = df.merge(kits.rename(columns={"PRODUCTO": "PRODUCTO_KIT"}), left_on="Descr.Material",
                  right_on="KIT", how="left")
    df["PRODUCTO"] = df["PRODUCTO_KIT"].where(df["PRODUCTO_KIT"].notna(), df["PRODUCTO"])
    # Anulado vacío: en el Excel la celda vacía llega como NaN (no ""), por eso se normaliza.
    anulado_vacio = df["Anulado"].fillna("").astype(str).str.strip() == ""
    df = df[df["Descr.Clase Fact"].isin(["Boleta Kamill", "Fact. Nacional", "Factura Kamill"]) & anulado_vacio]
    seg = pd.read_excel(_bytes(dl, SEG_PERU))
    df = df.merge(seg[["CLIENTE", "SEGMENTO"]], left_on="Nombre Cliente", right_on="CLIENTE", how="left",
                  suffixes=("", "_seg"))
    df = df.rename(columns={"Fec.Emis.Fact": "FECHA", "Cant.Fact.": "UNIDADES",
                            "Descr.Of.Ventas": "NOMBRE_TIENDA", "SEGMENTO": "CANAL VENTA"})
    df = df[df["Descr.CanalDist."] != "Interno"]
    df["CLIENTE"] = "DROGUERIA CORPORACION LIFE S.A.C."
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    df["VENDEDOR"] = None
    df["VALORES"] = None
    return df.reindex(columns=COMUN)


# ── Colombia: carpetas de retailers en carpeta_cuentas_clave ─────────────────
FOLDER_CC = DRIVE_IDS["carpeta_cuentas_clave"]
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
         "noviembre": 11, "diciembre": 12}


def _subcarpeta(dl, nombre):
    for it in dl.list_folder(FOLDER_CC):
        if it["name"].strip().lower() == nombre.lower():
            return it["id"]
    raise FileNotFoundError(f"subcarpeta {nombre} no está en carpeta_cuentas_clave")


def _concat_carpeta(dl, folder_id, ext, lector):
    dfs = []
    for f in _archivos_carpeta(dl, folder_id, ext):
        try:
            dfs.append(lector(_bytes(dl, f["id"])))
        except Exception as e:
            logging.error(f"  {f['name']}: {e}")
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _fecha_mes_anio(mes, anio):
    m = MESES.get(str(mes).strip().lower())
    return pd.Timestamp(int(anio), m, 1) if m and pd.notna(anio) else pd.NaT


def ventas_farmatodo(dl, base):
    df = _concat_carpeta(dl, _subcarpeta(dl, "Farmatodo"), (".xlsx",), lambda b: pd.read_excel(b))
    df = df.merge(base[["vpn_farmatodo", "PRODUCTO"]], left_on="VPN", right_on="vpn_farmatodo", how="left")
    df["FECHA"] = df.apply(lambda r: _fecha_mes_anio(r["MES"], r["AÑO"]), axis=1)
    df = df[df["VPN"].notna()].copy()
    df["CIUDAD"] = df["CIUDAD"].astype(str).str.replace("Bogota", "Bogotá", regex=False)
    df["CLIENTE"] = "FARMATODO COLOMBIA SA"
    df = df.rename(columns={"UNIDADES_VENDIDAS": "UNIDADES", "DESCRIPCION_ITEM": "NOMBRE_PRODUCTO"})
    return df.reindex(columns=COMUN)


def ventas_prosalon(dl, base):
    def lee(b):
        return pd.read_csv(b, sep="|", engine="python", encoding="latin-1")
    df = _concat_carpeta(dl, _subcarpeta(dl, "Prosalon"), (".txt",), lee)
    df = df.rename(columns={"SUBTOTAL": "VENTA", "Desc. C.O.": "NOMBRE_TIENDA"})
    df = df.merge(base[["item_prosalon", "PRODUCTO"]], left_on="ITEM", right_on="item_prosalon", how="left")
    df = df[df["UNIDADES"].notna()].copy()
    df["NOMBRE_TIENDA"] = df["NOMBRE_TIENDA"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    df["CLIENTE"] = "PROSALON DISTRIBUCIONES SAS"
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce", dayfirst=True)
    return df.reindex(columns=COMUN)


def ventas_pasteur(dl, base):
    df = _concat_carpeta(dl, _subcarpeta(dl, "Pasteur"), (".xlsx",), lambda b: pd.read_excel(b))
    df = df.rename(columns={"fechaVenta": "FECHA", "ValorVenta": "VENTA", "CantidadVenta": "UNIDADES"})
    df = df.merge(base[["plu_pasteur", "PRODUCTO"]], left_on="PLU", right_on="plu_pasteur", how="left")
    df = df.rename(columns={"PuntoVentaNombre": "NOMBRE_TIENDA", "Ciudad": "CIUDAD",
                            "TipoVenta": "CANAL VENTA", "ProductoNombre": "NOMBRE_PRODUCTO"})
    df["CLIENTE"] = "DISTRIBUIDORA PASTEUR S.A"
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    return df.reindex(columns=COMUN)


def ventas_locatel(dl, base):
    def lee(b):
        try:
            return pd.read_csv(b, sep=";", encoding="utf-8")
        except UnicodeDecodeError:
            b.seek(0)
            return pd.read_csv(b, sep=";", encoding="latin-1")
    df = _concat_carpeta(dl, _subcarpeta(dl, "Locatel"), (".csv",), lee)
    df["CLIENTE"] = "BRECCIA SALUD S.AS."
    df["FECHA"] = df.apply(lambda r: _fecha_mes_anio(r["MES"], r["AÑO"]), axis=1)
    df = df.merge(base[["locatel_cod_sap", "PRODUCTO"]], left_on="CODIGO SAP",
                  right_on="locatel_cod_sap", how="left")
    df = df.rename(columns={"VENTA NETA": "VENTA", "UNIDADES VENDIDAS": "UNIDADES",
                            "NOMBRE CENTRO": "NOMBRE_TIENDA", "NOMBRE PRODUCTO": "NOMBRE_PRODUCTO"})
    return df.reindex(columns=COMUN)


def ventas_novaventa(dl, base):
    df = _concat_carpeta(dl, _subcarpeta(dl, "Novaventa"), (".xlsx",), lambda b: pd.read_excel(b))
    df = df.rename(columns={"Ventas": "VENTA", "Unds Brutas": "UNIDADES"})
    df = df.merge(base[["cod_novaventa", "PRODUCTO"]], left_on="Código", right_on="cod_novaventa", how="left")

    def fnova(r):
        camp = r["Campaña"]
        dias = (20 * int(camp) - 1) if camp <= 18 else (18 * 20 - 1)
        return pd.Timestamp(int(r["Año"]), 1, 1) + pd.Timedelta(days=dias)
    df["FECHA"] = df.apply(fnova, axis=1)
    df = df[df["Descripción producto"].notna()].copy()
    df = df.rename(columns={"Código": "COD_CLIENTE", "Descripción producto": "NOMBRE_PRODUCTO",
                            "Campaña": "Campaña"})
    df = df[df["NOMBRE_PRODUCTO"] != "Termo La Poción"].copy()
    df["NOMBRE_TIENDA"] = "Novaventa"
    df["CLIENTE"] = "NOVAVENTA S.A.S"
    return df.reindex(columns=COMUN)


# ── DOMINICANA (Python embebido: fila 'Total', melt de fechas, ffill clientes) ──
def ventas_dominicana(dl, base):
    dfs = []
    for f in _archivos_carpeta(dl, FOLDER_RD):
        try:
            a = pd.read_excel(_bytes(dl, f["id"]), header=None)
            idx_total = int(a[a.iloc[:, 0] == "Total"].index[0])
            a.columns = a.iloc[idx_total - 2].values
            idx_fechas = [0] + [i for i in range(len(a.columns)) if pd.notna(a.columns[i]) and i != 0]
            a = a[idx_total + 1:].iloc[:, idx_fechas]
            a = a.rename(columns={a.columns[0]: "Detalles"})
            a["Clientes"] = np.where(a["Detalles"].astype(str).str.contains("[", regex=False, na=False),
                                     np.nan, a["Detalles"])
            a["Clientes"] = a["Clientes"].ffill()
            a = a[a["Detalles"].astype(str).str.contains("[", regex=False, na=False)]
            a = a.melt(id_vars=["Clientes", "Detalles"], var_name="Fecha", value_name="Valor")
            a["Fecha"] = [pd.to_datetime(f"{y}-{MESES[mes.lower()]}-01")
                          for mes, y in (str(i).split(" ") for i in a["Fecha"])]
            a["Valor"] = pd.to_numeric(a["Valor"], errors="coerce")
            a = a[a["Valor"] > 0]
            a["Sucursal"] = a["Clientes"].astype(str).str.split(",").str[1]
            a["Clientes"] = a["Clientes"].astype(str).str.split(",").str[0].str.strip().str.replace(
                r"\s+", " ", regex=True)
            a["Detalles"] = a["Detalles"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
            a["Valor"] = a["Valor"].astype(int)
            dfs.append(a)
        except Exception as e:
            logging.error(f"  RD {f['name']}: {e}")
    df = pd.concat(dfs, ignore_index=True)
    df = df.loc[:, ~df.columns.duplicated()]
    df = df[df["Clientes"].notna()]
    # M: split Detalles, join BASE por primer token de PRODUCTO
    det = df["Detalles"].str.split(" ", expand=True)
    df["Detalles.1"] = det[0].astype(str).str.replace("[7708162555347]", "[PCN31]", regex=False).str.strip()
    df["Detalles.3"] = det[2] if det.shape[1] > 2 else None
    base = base.copy()
    base["PC1"] = base["PRODUCTO"].astype(str).str.split().str[0].str.strip()
    df = df.merge(base[["PC1", "PRODUCTO"]].drop_duplicates("PC1"), left_on="Detalles.1",
                  right_on="PC1", how="left")
    df = df[df["Detalles.3"] == "POCION"].copy()
    df["CLIENTE"] = "DISTRIBUIDORA LEOPHARMA S.R.L."
    df = df.rename(columns={"Valor": "UNIDADES", "Fecha": "FECHA", "Clientes": "NOMBRE_TIENDA"})
    seg = pd.read_excel(_bytes(dl, SEG_DOMINICANA))
    df = df.merge(seg[["Clientes", "Segmento"]].rename(columns={"Clientes": "NOMBRE_TIENDA",
                  "Segmento": "SEG_D"}), on="NOMBRE_TIENDA", how="left")
    df["CANAL VENTA"] = df["SEG_D"].fillna("Ecommerce").replace("BYG", "Beauty and Go")
    df["VENDEDOR"] = None
    df["VALORES"] = None
    return df.reindex(columns=COMUN)


def _leer_hoja_detectando(b, sheet, marca):
    """Lee una hoja y detecta la fila de encabezado (la que contiene `marca` en alguna celda)."""
    raw = pd.read_excel(b, sheet_name=sheet, header=None)
    hrow = 0
    for i in range(min(15, len(raw))):
        if raw.iloc[i].astype(str).str.strip().isin([marca]).any():
            hrow = i
            break
    cols = [str(x).strip() for x in raw.iloc[hrow].tolist()]
    body = raw.iloc[hrow + 1:].copy()
    body.columns = cols
    return body


def ventas_surti(dl, base):
    # 1 archivo específico (hoja 'BD'); hist_surti se descarta (sus filas no traen EAN 13).
    fid = None
    for f in _archivos_carpeta(dl, _subcarpeta(dl, "Surticosmeticos"), (".xlsx",)):
        if f["name"].strip() == "Sell out - POCIÓN - ene 2026.xlsx":
            fid = f["id"]
    if fid is None:  # tolerante: primer .xlsx
        fid = _archivos_carpeta(dl, _subcarpeta(dl, "Surticosmeticos"), (".xlsx",))[0]["id"]
    df = pd.read_excel(_bytes(dl, fid), sheet_name="BD")
    df = df[df["Etiquetas de fila"] != "Total general"].copy()
    df = df.drop(columns=[c for c in ("Cantidad Total",) if c in df.columns])
    idv = [c for c in ("Etiquetas de fila", "EAN 13", "Nombre", "FECHA") if c in df.columns]
    tiendas = [c for c in df.columns if c not in idv]
    df = df.melt(id_vars=idv, value_vars=tiendas, var_name="NOMBRE_TIENDA", value_name="UNIDADES")
    df["CLIENTE"] = "SURTICOSMETICOS HF EU"
    df = df.merge(base[["SURTI_AEN", "PRODUCTO"]], left_on="EAN 13", right_on="SURTI_AEN", how="left")
    df = df[df["EAN 13"].notna()].copy()
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    return df.reindex(columns=COMUN)


def ventas_laskin(dl, base):
    def lee(b):
        d = _leer_hoja_detectando(b, "Ventas", "FECHA")
        return d
    df = _concat_carpeta(dl, _subcarpeta(dl, "LASKIN"), (".xlsx",), lee)
    df = df[df["FECHA"].notna() & (df["FECHA"].astype(str) != "FECHA")].copy()
    df = df.rename(columns={"Desc. C.O.": "NOMBRE_TIENDA", "Item": "COD_CLIENTE",
                            "Suma de Cantidad inv.": "UNIDADES", "Nombre vendedor": "CANAL VENTA"})
    df["CLIENTE"] = "LASKIN S.A"
    df = df.merge(base[["Laskin_item", "PRODUCTO"]], left_on="COD_CLIENTE", right_on="Laskin_item", how="left")
    df = df[df["COD_CLIENTE"].notna()].copy()
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    return df.reindex(columns=COMUN)


def ventas_krika(dl, base):
    def lee(b):
        return _leer_hoja_detectando(b, "Hoja1", "Desc. bodega")
    df = _concat_carpeta(dl, _subcarpeta(dl, "Krika"), (".xlsx",), lee)
    df = df[(df["Desc. bodega"] != "Desc. bodega") & (df["Desc. bodega"] != "Grand Total")].copy()
    df = df.merge(base[["Krika", "PRODUCTO"]], left_on="Desc. item", right_on="Krika", how="left")
    df = df.rename(columns={"Cantidad inv.": "UNIDADES", "Desc. bodega": "NOMBRE_TIENDA"})
    df["CLIENTE"] = "LUCEGO SAS"
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    return df.reindex(columns=COMUN)


# ══════════════════════ INVENTARIOS → bi_inventario_cclave ══════════════════════
FOLDER_INV_CO = "193Zx7VWc18UxkquJ8ZvB8KY2lk99NhJ8"        # carpeta_cuentas_clave/inventarios
FOLDER_INV_PAISES = "1oRzgPyqYN63caTn-YuLSLkFhDtQhK9Q-"     # data/paises/INVENTARIOS
COMUN_INV = ["CLIENTE", "PRODUCTO", "NOMBRE_TIENDA", "COD_CLIENTE", "INVENTARIO", "MAXIMO"]
ARUMA = ["ARUMA ARKADIA", "ARUMA BUENAVISTA BARRANQUILLA", "ARUMA CALLE 147", "ARUMA CARIBE PLAZA",
         "ARUMA CENTRO CHIA", "ARUMA CENTRO MAYOR", "ARUMA DIVERPLAZA", "ARUMA ECOPLAZA MOSQUERA",
         "ARUMA EL TESORO", "ARUMA FUNDADORES MANIZALES", "ARUMA JARDIN PLAZA", "ARUMA LA QUINTA",
         "ARUMA LOS MOLINOS", "ARUMA MALLPLAZA NQS", "ARUMA MAYORCA", "ARUMA NUESTRO BOGOTA",
         "ARUMA PARQUE COLINA", "ARUMA PLAZA BOCAGRANDE", "ARUMA PLAZA CENTRAL", "ARUMA PLAZA IMPERIAL",
         "ARUMA TITAN PLAZA", "ARUMA UNICENTRO BOGOTA", "ARUMA GRAN ESTACION", "BODEGA PRINCIPAL SUPPLA"]


def _archivo_por_nombre(dl, folder_id, nombre):
    for f in dl.list_folder(folder_id):
        if f["name"].strip() == nombre:
            return f["id"]
    raise FileNotFoundError(f"{nombre} no está en la carpeta {folder_id}")


def inv_farmatodo(dl, base):
    df = pd.read_excel(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_CO, "DataStoreDetallado FARMATODO.xlsx")),
                       sheet_name="Sheet1")
    df = df.rename(columns={"VPN": "COD_CLIENTE", "Nombre de la tienda": "NOMBRE_TIENDA",
                            "Unidades disponibles en inventario": "INVENTARIO", "Máximo": "MAXIMO"})
    df["CLIENTE"] = "FARMATODO COLOMBIA SA"
    df = df.merge(base[["vpn_farmatodo", "PRODUCTO"]], left_on="COD_CLIENTE", right_on="vpn_farmatodo", how="left")
    return df.reindex(columns=COMUN_INV)


def inv_pasteur(dl, base):
    df = pd.read_excel(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_CO, "Inventario mes corriente_PASTEUR.xlsx")),
                       sheet_name="data")
    df = df.rename(columns={"PuntoVentaNombre": "NOMBRE_TIENDA", "PLU": "COD_CLIENTE",
                            "CantidadInventario": "INVENTARIO"})
    df["CLIENTE"] = "DISTRIBUIDORA PASTEUR S.A"
    df = df.merge(base[["plu_pasteur", "PRODUCTO"]], left_on="COD_CLIENTE", right_on="plu_pasteur", how="left")
    return df.reindex(columns=COMUN_INV)


def inv_locatel(dl, base):
    df = pd.read_excel(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_CO, "inventarioproveedor LOCATEL.xlsx")),
                       sheet_name="Sheet1")
    df = df.drop(columns=[c for c in ("TOTAL", "ESTADO", "EAN", "NOMBRE PRODUCTO") if c in df.columns])
    df = df.melt(id_vars=["CODIGO SAP"], var_name="NOMBRE_TIENDA", value_name="INVENTARIO")
    df["CLIENTE"] = "BRECCIA SALUD S.AS."
    df = df.rename(columns={"CODIGO SAP": "COD_CLIENTE"})
    df = df.merge(base[["locatel_cod_sap", "PRODUCTO"]], left_on="COD_CLIENTE", right_on="locatel_cod_sap", how="left")
    return df.reindex(columns=COMUN_INV)


def inv_prosalon(dl, base):
    b = _bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_CO, "PROSALON.txt"))
    df = pd.read_csv(b, sep="|", engine="python", encoding="utf-8")
    df = df[df["CO_BODEGA"].astype(str) != ""].copy()
    df = df.rename(columns={"ITEM": "COD_CLIENTE", "Desc. C.O.": "TIENDA", "DISPONIBLE": "INVENTARIO"})
    df["CLIENTE"] = "PROSALON DISTRIBUCIONES SAS"
    df = df.merge(base[["item_prosalon", "PRODUCTO"]], left_on="COD_CLIENTE", right_on="item_prosalon", how="left")
    df["NOMBRE_TIENDA"] = (df["TIENDA"].astype(str).str.replace("TIENDA ", "", regex=False)
                           .str.strip().str.replace(r"\s+", " ", regex=True))
    df = df[df["NOMBRE_TIENDA"].isin([a.replace(" ", " ") for a in ARUMA] + ARUMA)].copy()
    return df.reindex(columns=COMUN_INV)


def inv_laskin(dl, base):
    df = _leer_hoja_detectando(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_CO, "LASKIN.xlsx")),
                               "Existencias", "Desc. bodega")
    df = df.rename(columns={"Desc. bodega": "NOMBRE_TIENDA", "Item": "COD_CLIENTE",
                            "Suma de Existencia": "INVENTARIO"})
    df["CLIENTE"] = "LASKIN S.A"
    df = df.merge(base[["Laskin_item", "PRODUCTO"]], left_on="COD_CLIENTE", right_on="Laskin_item", how="left")
    return df.reindex(columns=COMUN_INV)


def inv_ecuador(dl, base):
    df = pd.read_excel(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_PAISES, "INVENTARIO ECUADOR.xlsx")),
                       sheet_name="Hoja1")
    df = df.merge(base[["PRODUCTO"]], left_on="SKU", right_on="PRODUCTO", how="left")
    df = df.drop(columns=[c for c in ("TOTAL",) if c in df.columns])
    df = df.melt(id_vars=["SKU", "PRODUCTO"], var_name="NOMBRE_TIENDA", value_name="INVENTARIO")
    df["CLIENTE"] = "ZAR IMPORT ZARIMPORT S.A."
    return df.reindex(columns=COMUN_INV)


def inv_peru(dl, base):
    df = pd.read_excel(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_PAISES, "INVENTARIO PERÚ.xlsx")),
                       sheet_name="Hoja1")
    df = df.merge(base[["PRODUCTO"]], left_on="SKU", right_on="PRODUCTO", how="left")
    df["CLIENTE"] = "DROGUERIA CORPORACION LIFE S.A.C."
    return df.reindex(columns=COMUN_INV)


def inv_rd(dl, base):
    df = pd.read_excel(_bytes(dl, _archivo_por_nombre(dl, FOLDER_INV_PAISES, "INVENTARIO RD.xlsx")),
                       sheet_name="Hoja1")
    df["SKU"] = df["SKU"].astype(str).str.replace(r"[\x00-\x1f]", "", regex=True).str.strip()
    df["SKU_mod"] = df["SKU"].str.split("]").str[0] + "]"
    df["SKU_mod"] = df["SKU_mod"].str.replace("[7708162555347]", "[PCN31]", regex=False)
    base = base.copy()
    base["PC1"] = base["PRODUCTO"].astype(str).str.split().str[0].str.strip()
    df = df.merge(base[["PC1", "PRODUCTO"]].drop_duplicates("PC1"), left_on="SKU_mod", right_on="PC1", how="left")
    df["CLIENTE"] = "DISTRIBUIDORA LEOPHARMA S.R.L."
    return df.reindex(columns=COMUN_INV)


def combinar_inventarios(dl, base):
    partes = [inv_pasteur(dl, base), inv_locatel(dl, base), inv_farmatodo(dl, base),
              inv_prosalon(dl, base), inv_laskin(dl, base), inv_peru(dl, base),
              inv_ecuador(dl, base), inv_rd(dl, base)]
    df = pd.concat(partes, ignore_index=True)
    df["MAXIMO"] = pd.to_numeric(df["MAXIMO"], errors="coerce").fillna(0)
    df["INVENTARIO"] = pd.to_numeric(df["INVENTARIO"], errors="coerce")
    df["NOMBRE_TIENDA"] = df["NOMBRE_TIENDA"].astype("string").str.strip()
    df["ID_TIENDA"] = df["CLIENTE"].astype("string") + df["NOMBRE_TIENDA"].astype("string")
    return df


CONTROL_INV = {"FARMATODO COLOMBIA SA": (2224, 13648), "BRECCIA SALUD S.AS.": (400, 6449),
               "DISTRIBUIDORA PASTEUR S.A": (1002, 2841), "PROSALON DISTRIBUCIONES SAS": (335, 4948),
               "LASKIN S.A": (260, 1343), "ZAR IMPORT ZARIMPORT S.A.": (50, 51210),
               "DROGUERIA CORPORACION LIFE S.A.C.": (999, 10430), "DISTRIBUIDORA LEOPHARMA S.R.L.": (22, 3008)}


def combinar_tiendas(dl, base, kits):
    """TIENDAS_CCLAVE = distinct de NOMBRE_TIENDA (upper/trim/clean) de ventas + inventarios."""
    v = combinar_ventas(dl, base, kits)[["CLIENTE", "NOMBRE_TIENDA"]]
    i = combinar_inventarios(dl, base)[["CLIENTE", "NOMBRE_TIENDA"]]
    t = pd.concat([v, i], ignore_index=True)
    t["NOMBRE_TIENDA"] = (t["NOMBRE_TIENDA"].astype("string").str.strip().str.upper()
                          .str.replace(r"[\x00-\x1f]", "", regex=True))
    t = t.dropna(subset=["NOMBRE_TIENDA"])
    t = t[t["NOMBRE_TIENDA"].str.strip() != ""]
    t = t.drop_duplicates(subset=["NOMBRE_TIENDA"])
    t["ID_TIENDA"] = t["CLIENTE"].astype("string") + t["NOMBRE_TIENDA"]
    return t


def cargar_inventarios():
    from classes.db_loader import DBLoader
    dl, lo = DriveLoader(), DBLoader()
    df = combinar_inventarios(dl, leer_base(dl))
    lo.cargar(df, "bi_inventario_cclave", schema="marts", if_exists="replace", source_file="inventarios (Drive)")
    logging.info(f"bi_inventario_cclave: {len(df):,} filas, {df['CLIENTE'].nunique()} clientes")


def cargar_tiendas():
    from classes.db_loader import DBLoader
    dl, lo = DriveLoader(), DBLoader()
    df = combinar_tiendas(dl, leer_base(dl), leer_kits(dl))
    lo.cargar(df, "bi_tiendas_cclave", schema="marts", if_exists="replace", source_file="cuentas_clave (Drive)")
    logging.info(f"bi_tiendas_cclave: {len(df):,} filas")


def validar_inventarios():
    dl = DriveLoader()
    df = combinar_inventarios(dl, leer_base(dl))
    print(f"{'CLIENTE':<34}{'filas':>8}{'inv':>12}{'ctrl_f':>9}{'ctrl_inv':>12}  ok")
    for cli, g in df.groupby("CLIENTE"):
        cf, cu = CONTROL_INV.get(cli, (None, None))
        fi, iv = len(g), g["INVENTARIO"].sum()
        ok = "✓" if cf and abs(fi - cf) <= max(2, cf * 0.02) and abs(iv - cu) <= max(2, abs(cu) * 0.02) else "?"
        print(f"{str(cli)[:33]:<34}{fi:>8,}{iv:>12,.0f}{(cf or 0):>9,}{(cu or 0):>12,}  {ok}")


CONTROL = {  # PBIX CUENTAS_CLAVE ANEXO (snapshot) → (filas, unidades)
    "SURTICOSMETICOS HF EU": (231, 3514), "LASKIN S.A": (1292, 2354), "LUCEGO SAS": (637, 11788),
    "FARMATODO COLOMBIA SA": (28227, 212665), "BRECCIA SALUD S.AS.": (4291, 18016),
    "DISTRIBUIDORA PASTEUR S.A": (7442, 7463), "PROSALON DISTRIBUCIONES SAS": (7659, 7782),
    "NOVAVENTA S.A.S": (99, 534468), "DISTRIBUIDORA LEOPHARMA S.R.L.": (3714, 18851),
    "DROGUERIA CORPORACION LIFE S.A.C.": (6923, 8375),
}


def combinar_ventas(dl, base, kits, incluir_ecuador=False):
    """CUENTAS_CLAVE ANEXO: Table.Combine de todos los retailers + ID_TIENDA + limpieza de NOMBRE_TIENDA
    (Trim/Clean/Upper), tal como el Power Query. ecuador se excluye por defecto (no está en el ANEXO
    actual del PBIX y su segmentación está rota en Drive)."""
    partes = [ventas_farmatodo(dl, base), ventas_prosalon(dl, base), ventas_pasteur(dl, base),
              ventas_locatel(dl, base), ventas_novaventa(dl, base), ventas_surti(dl, base),
              ventas_laskin(dl, base), ventas_krika(dl, base), ventas_dominicana(dl, base),
              ventas_peru(dl, base, kits)]
    if incluir_ecuador:
        partes.append(ventas_ecuador(dl, base))
    df = pd.concat(partes, ignore_index=True)
    df["NOMBRE_TIENDA"] = (df["NOMBRE_TIENDA"].astype("string").str.strip()
                           .str.replace(r"[\x00-\x1f]", "", regex=True).str.upper())
    df["ID_TIENDA"] = df["CLIENTE"].astype("string") + df["NOMBRE_TIENDA"].astype("string")
    # tipos numéricos/fecha (el concat con NaN los deja como object → DBLoader los cargaría VARCHAR)
    for c in ("UNIDADES", "VALORES"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
    return df


def cargar_ventas(incluir_ecuador=False):
    from classes.db_loader import DBLoader
    dl, lo = DriveLoader(), DBLoader()
    df = combinar_ventas(dl, leer_base(dl), leer_kits(dl), incluir_ecuador)
    ok = lo.cargar(df, "bi_cuentas_clave_ventas", schema="marts", if_exists="replace",
                   source_file="cuentas_clave (Drive)")
    logging.info(f"bi_cuentas_clave_ventas: {len(df):,} filas, {df['CLIENTE'].nunique()} clientes, ok={ok}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "cargar":
        cargar_ventas(incluir_ecuador="ecuador" in sys.argv)
        return
    if cmd == "inventarios":
        cargar_inventarios()
        return
    if cmd == "tiendas":
        cargar_tiendas()
        return
    if cmd == "val_inv":
        validar_inventarios()
        return
    dl = DriveLoader()
    base = leer_base(dl)
    kits = leer_kits(dl)
    funcs = [
        ("farmatodo", lambda: ventas_farmatodo(dl, base)),
        ("prosalon", lambda: ventas_prosalon(dl, base)),
        ("pasteur", lambda: ventas_pasteur(dl, base)),
        ("locatel", lambda: ventas_locatel(dl, base)),
        ("novaventa", lambda: ventas_novaventa(dl, base)),
        ("surti", lambda: ventas_surti(dl, base)),
        ("laskin", lambda: ventas_laskin(dl, base)),
        ("krika", lambda: ventas_krika(dl, base)),
        ("dominicana", lambda: ventas_dominicana(dl, base)),
        ("peru", lambda: ventas_peru(dl, base, kits)),
        ("ecuador", lambda: ventas_ecuador(dl, base)),
    ]
    print(f"{'retailer':<12}{'CLIENTE':<34}{'filas':>8}{'unids':>12}{'ctrl_filas':>11}{'ctrl_unids':>12}  ok")
    for nombre, fn in funcs:
        try:
            df = fn()
            df["UNIDADES"] = pd.to_numeric(df["UNIDADES"], errors="coerce")
            for cli, g in df.groupby("CLIENTE"):
                cf, cu = CONTROL.get(cli, (None, None))
                filas, unids = len(g), g["UNIDADES"].sum()
                ok = "✓" if cf and abs(filas - cf) <= max(2, cf * 0.01) and \
                    abs(unids - cu) <= max(2, abs(cu) * 0.01) else ("?" if cf else "—")
                print(f"{nombre:<12}{str(cli)[:33]:<34}{filas:>8,}{unids:>12,.0f}"
                      f"{(cf or 0):>11,}{(cu or 0):>12,}  {ok}")
        except Exception as e:
            logging.exception(f"{nombre}: {e}")


if __name__ == "__main__":
    main()
