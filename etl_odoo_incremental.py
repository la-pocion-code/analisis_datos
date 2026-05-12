import os
import xmlrpc.client
import pandas as pd
import numpy as np
import logging
from dotenv import load_dotenv
from db_loader import DBLoader  # Importamos tu clase

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

load_dotenv()

def aplanar_datos_odoo(data_list):
    """Convierte los campos [id, name] de Odoo en valores planos para la DB."""
    if not data_list:
        return pd.DataFrame()
    df = pd.DataFrame(data_list)
    for col in df.columns:
        # Si la columna es una lista (Many2one), extraemos el ID o el nombre
        # En este caso, extraemos el nombre (posición 1) si existe
        if df[col].apply(lambda x: isinstance(x, (list, tuple))).any():
            df[col] = df[col].apply(lambda x: x[1] if isinstance(x, (list, tuple)) else x)
    return df

def ejecutar_sincronizacion():
    # 1. Credenciales
    url = os.getenv("url")
    db = os.getenv("db")
    username = os.getenv("username")
    password = os.getenv("password")

    # 2. Conexión a Odoo
    common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
    uid = common.authenticate(db, username, password, {})
    models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

    # 3. Inicializar nuestro Cargador de DB
    loader = DBLoader()

    # --- PROCESO PARA ACCOUNT.MOVE.LINE ---
    nombre_tabla = "account_move_line"
    
    # A. Buscar última fecha de modificación en nuestra DB
    query_fecha = f"SELECT MAX(write_date) FROM raw.{nombre_tabla}"
    res_fecha = loader.consultar(query_fecha)
    
    # Si la tabla no existe o está vacía, iniciamos desde una fecha antigua
    last_sync = res_fecha.iloc[0,0] if res_fecha is not None and not res_fecha.empty and res_fecha.iloc[0,0] else "2000-01-01 00:00:00"
    
    logging.info(f"Sincronizando {nombre_tabla} desde: {last_sync}")

    # B. Consultar cambios en Odoo
    # Traemos lo que se modificó después de la última sincronización
    domain = [
        ('write_date', '>', str(last_sync)),
        ('move_id.move_type', 'in', ['out_invoice', 'out_refund'])
    ]
    
    fields = [
        'id', 'company_id', 'invoice_date', 'move_id', 'product_id', 
        'partner_id', 'account_id', 'quantity', 'price_unit', 
        'currency_id', 'price_subtotal', 'balance', 'date', 
        'name', 'analytic_distribution', 'matching_number', 'write_date'
    ]

    lineas_raw = models.execute_kw(db, uid, password, 'account.move.line', 'search_read', [domain], {
        'fields': fields,
        'limit': 5000 # Un límite razonable para 15 minutos
    })

    # C. Cargar a Postgres
    if lineas_raw:
        df = aplanar_datos_odoo(lineas_raw)
        # Usamos la función de preparación que crea tabla y hace UPSERT
        loader.preparar_y_cargar(df, nombre_tabla)
        logging.info(f"Se actualizaron {len(df)} registros.")
    else:
        logging.info("No se encontraron cambios en Odoo.")

if __name__ == "__main__":
    ejecutar_sincronizacion()