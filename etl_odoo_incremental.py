import os
import sys
import xmlrpc.client
import pandas as pd
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()


# ── Validación temprana: DB disponible antes de importar psycopg2 ──────────────
def verificar_db() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=10,
        )
        conn.close()
        logging.info("DB Railway: conexión OK")
        return True
    except Exception as e:
        logging.error(f"DB Railway no disponible: {e}")
        return False


if not verificar_db():
    logging.error("Abortando ETL — base de datos no accesible.")
    sys.exit(1)

from classes.db_loader import DBLoader


# ── Conexión Odoo ──────────────────────────────────────────────────────────────
def conectar_odoo():
    url  = os.getenv("url")
    db   = os.getenv("db")
    user = os.getenv("username_odoo")
    pw   = os.getenv("password")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, pw, {})
    if not uid:
        raise RuntimeError("Autenticación Odoo fallida. Verifica variables de entorno.")
    logging.info(f"Odoo conectado (uid={uid})")
    return db, uid, pw, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


# ── Descarga paginada genérica ─────────────────────────────────────────────────
def descargar_modelo_paginado(models, db, uid, pw, modelo, domain, fields, batch_size=2000):
    registros, offset = [], 0
    while True:
        lote = models.execute_kw(db, uid, pw, modelo, 'search_read', [domain], {
            'fields': fields, 'limit': batch_size, 'offset': offset, 'order': 'id asc'
        })
        if not lote:
            break
        registros.extend(lote)
        offset += len(lote)
        logging.info(f"  [{modelo}] {len(registros)} registros acumulados")
        if len(lote) < batch_size:
            break
    return registros


# ── Expansión de campos Many2one [id, nombre] ──────────────────────────────────
def expandir(df, col):
    df[f"{col}_id"]     = df[col].apply(lambda x: x[0] if isinstance(x, (list, tuple)) and x else None)
    df[f"{col}_nombre"] = df[col].apply(lambda x: x[1] if isinstance(x, (list, tuple)) and x else None)
    return df.drop(columns=[col])


# ── Obtener última fecha de sincronización ────────────────────────────────────
def ultima_fecha(loader, tabla, col="write_date", default="2024-01-01 00:00:00"):
    res = loader.consultar(f"SELECT MAX({col}) AS ult FROM raw.{tabla}")
    val = res["ult"][0] if res is not None and not res.empty else None
    return str(val) if val else default


# ══════════════════════════════════════════════════════════════════════════════
# JOBS DE SINCRONIZACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def sync_apuntes_contables(loader, models, db, uid, pw):
    """account.move.line → raw.odoo_apuntes (facturas y notas crédito de venta)"""
    tabla = "odoo_apuntes"
    desde = ultima_fecha(loader, tabla)
    logging.info(f"[{tabla}] Desde: {desde}")

    domain = [
        ["write_date", ">", desde],
        ["move_id.move_type", "in", ["out_invoice", "out_refund"]],
    ]
    fields = [
        "id", "date", "invoice_date", "move_id", "account_id",
        "partner_id", "quantity", "price_unit", "price_subtotal",
        "debit", "credit", "balance", "name", "write_date",
    ]

    datos = descargar_modelo_paginado(models, db, uid, pw, "account.move.line", domain, fields)
    if not datos:
        logging.info(f"[{tabla}] Sin cambios.")
        return

    df = pd.DataFrame(datos)
    for col in ["account_id", "partner_id", "move_id"]:
        df = expandir(df, col)

    loader.preparar_y_cargar(df, tabla)
    logging.info(f"[{tabla}] ✓ {len(df)} registros sincronizados.")


# ── Stub: Órdenes de compra ────────────────────────────────────────────────────
# def sync_ordenes_compra(loader, models, db, uid, pw):
#     """purchase.order → raw.odoo_purchase_orders"""
#     tabla  = "odoo_purchase_orders"
#     desde  = ultima_fecha(loader, tabla)
#     domain = [["write_date", ">", desde]]
#     fields = ["id", "name", "partner_id", "date_order", "amount_total", "state", "write_date"]
#     datos  = descargar_modelo_paginado(models, db, uid, pw, "purchase.order", domain, fields)
#     if not datos: return
#     df = pd.DataFrame(datos)
#     df = expandir(df, "partner_id")
#     loader.preparar_y_cargar(df, tabla)
#     logging.info(f"[{tabla}] ✓ {len(df)} registros sincronizados.")


# ── Stub: Inventario (stock.quant) ────────────────────────────────────────────
# def sync_inventario(loader, models, db, uid, pw):
#     """stock.quant → raw.odoo_stock  +  snapshot semanal en raw.odoo_stock_snapshot"""
#     tabla  = "odoo_stock"
#     domain = []
#     fields = ["id", "product_id", "location_id", "quantity", "reserved_quantity", "write_date"]
#     datos  = descargar_modelo_paginado(models, db, uid, pw, "stock.quant", domain, fields)
#     if not datos: return
#     df = pd.DataFrame(datos)
#     for col in ["product_id", "location_id"]:
#         df = expandir(df, col)
#     loader.preparar_y_cargar(df, tabla)            # estado actual (UPSERT)
#     # snapshot semanal: agregar semana y appended para histórico de inventario
#     # df["semana"] = pd.Timestamp.now().strftime("%Y-W%U")
#     # loader.cargar(df, "odoo_stock_snapshot", if_exists="append")
#     logging.info(f"[{tabla}] ✓ {len(df)} registros sincronizados.")


# ── Jobs activos ──────────────────────────────────────────────────────────────
JOBS = [
    sync_apuntes_contables,
    # sync_ordenes_compra,
    # sync_inventario,
]


def main():
    db, uid, pw, models = conectar_odoo()
    loader = DBLoader()
    for job in JOBS:
        try:
            job(loader, models, db, uid, pw)
        except Exception as e:
            logging.error(f"Error en {job.__name__}: {e}", exc_info=True)


if __name__ == "__main__":
    main()
