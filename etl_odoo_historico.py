# import os
# import xmlrpc.client
# import pandas as pd
# import logging
# from dotenv import load_dotenv
# from db_loader import DBLoader

# # Configuración de Logs para ver el progreso en tiempo real
# logging.basicConfig(
#     level=logging.INFO, 
#     format="%(asctime)s - %(levelname)s - %(message)s"
# )

# load_dotenv()

# def aplanar_datos_odoo(data_list):
#     """Limpia los campos Many2one [id, name] de Odoo."""
#     if not data_list:
#         return pd.DataFrame()
#     df = pd.DataFrame(data_list)
#     for col in df.columns:
#         if df[col].apply(lambda x: isinstance(x, (list, tuple))).any():
#             df[col] = df[col].apply(lambda x: x[1] if isinstance(x, (list, tuple)) else x)
#     return df

# def carga_historica_total():
#     # 1. Configuración de conexión
#     url = os.getenv("url")
#     db = os.getenv("db")
#     username = os.getenv("username")
#     password = os.getenv("password")

#     common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
#     uid = common.authenticate(db, username, password, {})
#     models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

#     loader = DBLoader()
    
#     # --- PARÁMETROS DE CARGA ---
#     modelo_odoo = 'account.move.line'
#     nombre_tabla = 'account_move_line'
#     batch_size = 2000  # Tamaño del lote por petición a la API
#     offset = 0
#     primera_vuelta = True

#     logging.info(f"🚀 Iniciando carga histórica de {modelo_odoo}")

#     # Borramos la tabla si existe para empezar de cero y evitar duplicados en el histórico
#     with loader.get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(f"DROP TABLE IF EXISTS raw.{nombre_tabla} CASCADE;")
#         conn.commit()
#         logging.info(f"🧹 Tabla raw.{nombre_tabla} reiniciada.")

#     while True:
#         logging.info(f"📦 Extrayendo registros desde offset: {offset}...")
        
#         # Filtros de negocio para La Poción
#         domain = [['move_id.move_type', 'in', ['out_invoice', 'out_refund']]]
        
#         fields = [
#             'id', 'company_id', 'invoice_date', 'move_id', 'product_id', 
#             'partner_id', 'account_id', 'quantity', 'price_unit', 
#             'currency_id', 'price_subtotal', 'balance', 'date', 
#             'name', 'analytic_distribution', 'matching_number', 'write_date'
#         ]

#         # Consulta a Odoo con paginación
#         raw_data = models.execute_kw(db, uid, password, modelo_odoo, 'search_read', [domain], {
#             'fields': fields,
#             'limit': batch_size,
#             'offset': offset,
#             'order': 'id asc'  # Ordenar por ID para asegurar consistencia en el paginado
#         })

#         if not raw_data:
#             logging.info("🏁 No hay más registros que cargar.")
#             break

#         df_batch = aplanar_datos_odoo(raw_data)

#         # La primera vez creamos la tabla, las siguientes solo insertamos
#         if primera_vuelta:
#             loader.preparar_y_cargar(df_batch, nombre_tabla)
#             primera_vuelta = False
#         else:
#             # Aquí podrías usar una función de inserción rápida si la tienes, 
#             # pero preparar_y_cargar funcionará bien gracias al UPSERT.
#             loader.preparar_y_cargar(df_batch, nombre_tabla)
        
#         offset += batch_size

#     logging.info(f"✅ Proceso terminado. Total registros procesados: {offset}")

# if __name__ == "__main__":
#     carga_historica_total()

import os
import xmlrpc.client
import pandas as pd
import logging
from dotenv import load_dotenv
from db_loader import DBLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

def aplanar_datos_odoo(data_list):
    if not data_list: return pd.DataFrame()
    df = pd.DataFrame(data_list)
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, tuple))).any():
            df[col] = df[col].apply(lambda x: x[1] if isinstance(x, (list, tuple)) else (None if x is False else x))
    return df

def carga_test_10k():
    # Conexión Odoo
    url, db, user, pw = os.getenv('url'), os.getenv('db'), os.getenv('username_odoo'), os.getenv('password')
    
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, pw, {})
    if not uid:
        logging.error("ERROR: La autenticación falló. Revisa URL, DB, Usuario o Password.")
        return
    else:
        logging.info(f"Autenticación exitosa. UID obtenido: {uid}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    loader = DBLoader()
    tabla_dest = 'account_move_line'
    
    # --- PARÁMETROS DE CONTROL ---
    LIMIT_TOTAL = 10000  # Tu límite de prueba
    batch_size = 2000    # Lotes de 2k para no estresar el buffer
    offset = 0

    logging.info(f"Iniciando prueba de carga controlada: {LIMIT_TOTAL} filas.")

    while offset < LIMIT_TOTAL:
        logging.info(f"Solicitando lote: {offset} a {offset + batch_size}...")
        
        raw_data = models.execute_kw(db, uid, pw, 'account.move.line', 'search_read', [[
            ['move_id.move_type', 'in', ['out_invoice', 'out_refund']]
        ]], {
            'fields': [
                'id', 'company_id', 'invoice_date', 'move_id', 'product_id', 
                'partner_id', 'account_id', 'quantity', 'price_unit', 
                'price_subtotal', 'balance', 'date', 'name', 'write_date'
            ],
            'limit': batch_size, 
            'offset': offset, 
            'order': 'id asc'
        })

        if not raw_data: 
            logging.info("No hay más datos disponibles.")
            break

        df = aplanar_datos_odoo(raw_data)
        loader.preparar_y_cargar(df, tabla_dest)
        
        offset += len(raw_data)
        
        # Seguridad adicional para no pasarnos del límite exacto
        if offset >= LIMIT_TOTAL:
            logging.info(f"✅ Límite de prueba de {LIMIT_TOTAL} alcanzado.")
            break

    logging.info("Smoke Test finalizado con éxito.")

if __name__ == "__main__":
    carga_test_10k()