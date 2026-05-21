"""
Reset de tablas del DW — usar solo si se necesita recargar una tabla desde cero.
Borra la tabla indicada y el siguiente ciclo del cron reconstruye el histórico completo.
"""
import logging
from db_loader import DBLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TABLAS_A_RESETEAR = [
    "odoo_apuntes",
    # "odoo_purchase_orders",
    # "odoo_stock",
]

if __name__ == "__main__":
    loader = DBLoader()
    with loader.get_connection() as conn:
        cur = conn.cursor()
        for tabla in TABLAS_A_RESETEAR:
            cur.execute(f"DROP TABLE IF EXISTS raw.{tabla} CASCADE;")
            logging.info(f"Tabla raw.{tabla} eliminada — el cron recargará el histórico completo.")
        conn.commit()
