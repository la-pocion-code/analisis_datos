import os
import re
import time
import logging
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from typing import Optional
from contextlib import contextmanager
from typing import Optional

# ========================
# LOGGING
# ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("db_loader.log"),
        logging.StreamHandler()
    ]
)

load_dotenv()


class DBLoader:
    """
    Clase para cargar DataFrames a PostgreSQL (Railway).
    Detecta tipos automaticamente desde el DataFrame,
    crea la tabla si no existe y soporta carga incremental.

    Uso basico:
        loader = DBLoader()

        # Carga incremental (solo filas nuevas por fecha)
        loader.cargar_incremental(df, 'ventas', fecha_col='FECHA_FACTURA')

        # Carga completa (reemplaza la tabla)
        loader.cargar(df, 'presupuesto', if_exists='replace')

        # Consulta
        df = loader.consultar("SELECT * FROM raw.ventas WHERE fecha_factura > '2025-01-01'")
    """

    # ========================
    # RENOMBRES EXPLICITOS
    # Columnas con caracteres especiales que deben conservar
    # un nombre semantico claro en PostgreSQL.
    # Agregar aqui cualquier columna que necesite nombre fijo.
    # ========================
    COLUMN_MAP = {
        'TOTAL($)':     'total_cop',
        'TOTAL($)_ORI': 'total_cop_ori',
        'TOTAL($)_x':   'total_cop_x',
        'TOTAL($)_y':   'total_cop_y',
    }

    def __init__(self):
        self.host     = os.getenv('DB_HOST')
        self.port     = os.getenv('DB_PORT')
        self.dbname   = os.getenv('DB_NAME')
        self.user     = os.getenv('DB_USER')
        self.password = os.getenv('DB_PASSWORD')

    # ========================
    # CONEXION
    # ========================
    @contextmanager
    def get_connection(self):
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                dbname=self.dbname,
                user=self.user,
                password=self.password
            )
            logging.info("Conexion establecida con Railway PostgreSQL")
            yield conn
        except psycopg2.Error as e:
            logging.error(f"Error de conexion: {e}")
            raise
        finally:
            if conn:
                conn.close()
                logging.info("Conexion cerrada")

    # ========================
    # MAPEO DE TIPOS PANDAS -> POSTGRESQL
    # ========================
    def _pg_type(self, dtype, col_name: str) -> str:
        """Mapea dtype de pandas a tipo PostgreSQL."""
        col = col_name.upper()
        if np.issubdtype(dtype, np.integer):
            return "BIGINT"
        elif np.issubdtype(dtype, np.floating):
            return "NUMERIC"
        elif np.issubdtype(dtype, np.datetime64):
            return "TIMESTAMP"
        elif col in ('OBSERVACIONES', 'DESCRIPCION', 'NOTAS', 'DETALLE', 'CUERPO_HTML'):
            return "TEXT"
        else:
            return "VARCHAR(512)"

    # ========================
    # LIMPIEZA DE COLUMNAS
    # ========================
    def _limpiar_columnas(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Estandariza nombres de columnas para PostgreSQL:
        1. Aplica renombres explicitos del COLUMN_MAP (nombres semanticos fijos).
        2. Limpia el resto de caracteres especiales.
        3. Resuelve duplicados restantes agregando sufijo _2, _3, etc.
        """
        df = df.copy()

        # Paso 1: renombres explicitos
        df = df.rename(columns=self.COLUMN_MAP)

        # Paso 2: limpiar y resolver duplicados
        nuevas = []
        conteo = {}

        for col in df.columns:
            limpia = re.sub(
                r'[^a-z0-9_]', '',
                col.strip().lower().replace(' ', '_')
            )
            if not limpia:
                limpia = 'col'

            if limpia in conteo:
                conteo[limpia] += 1
                limpia = f"{limpia}_{conteo[limpia]}"
            else:
                conteo[limpia] = 1

            nuevas.append(limpia)

        # Avisar si quedaron duplicados residuales
        duplicados = [n for n in nuevas if re.search(r'_\d+$', n)]
        if duplicados:
            logging.warning(f"Columnas renombradas por duplicado residual: {duplicados}")

        df.columns = nuevas
        return df

    # ========================
    # CARGA PRINCIPAL
    # ========================
    def cargar(
        self,
        df: pd.DataFrame,
        table_name: str,
        schema: str = "raw",
        if_exists: str = "append",
        batch_size: int = 5000,
        source_file: str = None,
        fecha_col: Optional[str] = None
    ) -> bool:
        """
        Carga un DataFrame a PostgreSQL.

        Args:
            df:           DataFrame a cargar.
            table_name:   Nombre de la tabla destino (sin esquema).
            schema:       Esquema destino. Default: 'raw'.
            if_exists:    'append'  -> solo inserta filas (incremental).
                          'replace' -> borra y recrea la tabla completa.
            batch_size:   Filas por lote.
            source_file:  Nombre del archivo origen para auditoria.
            fecha_col:    Nombre de la columna que contiene las fechas.
        Returns:
            True si fue exitoso, False si hubo error.
        """
        start = time.time()
        full_table = f"{schema}.{table_name}"

        # Limpiar columnas
        df = self._limpiar_columnas(df)

        # Columnas de auditoria
        df['_loaded_at']   = pd.Timestamp.now()
        df['_source_file'] = source_file or ''

        # Convertir NaN -> None para PostgreSQL
        df = df.replace({pd.NA: None, np.nan: None})
        df = df.astype(object).where(pd.notnull(df), None)

        try:
            with self.get_connection() as conn:
                cur = conn.cursor()

                # Crear esquema si no existe
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")

                # Si replace: borrar tabla existente
                if if_exists == 'replace':
                    cur.execute(f"DROP TABLE IF EXISTS {full_table};")
                    logging.info(f"Tabla {full_table} eliminada para recreacion")

                # Crear tabla si no existe (auto-detecta tipos desde el DataFrame)
                cols_def = ", ".join([
                    f"{col} {self._pg_type(dtype, col)}"
                    for col, dtype in zip(df.columns, df.dtypes)
                ])
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {full_table} (
                        id SERIAL PRIMARY KEY,
                        {cols_def}
                    );
                """)
                conn.commit()
                logging.info(f"Tabla {full_table} lista para recibir datos")

                # Insercion por lotes
                cols         = list(df.columns)
                cols_str     = ", ".join(cols)
                placeholders = ", ".join(["%s"] * len(cols))
                insert_sql   = f"INSERT INTO {full_table} ({cols_str}) VALUES ({placeholders})"

                values    = df.values.tolist()
                filas_ok  = 0
                filas_err = 0

                for i in range(0, len(values), batch_size):
                    batch = values[i:i + batch_size]
                    try:
                        psycopg2.extras.execute_batch(cur, insert_sql, batch)
                        conn.commit()
                        filas_ok += len(batch)
                        logging.info(f"  Lote {i // batch_size + 1}: {len(batch)} filas insertadas")
                    except psycopg2.Error as e:
                        conn.rollback()
                        filas_err += len(batch)
                        logging.error(f"  Error en lote {i // batch_size + 1}: {e}")

                elapsed = time.time() - start
                logging.info(f"OK {filas_ok:,} filas cargadas en {full_table} ({elapsed:.1f}s)")
                if filas_err:
                    logging.warning(f"AVISO {filas_err:,} filas con error")

                cur.close()
                return True

        except Exception as e:
            logging.error(f"Error cargando {full_table}: {e}")
            return False

    # ========================
    # CARGA INCREMENTAL
    # ========================
    def cargar_incremental(
        self,
        df: pd.DataFrame,
        table_name: str,
        fecha_col: Optional[str]  ,
        schema: str = "raw",
        batch_size: int = 5000,
        source_file: str = None
    ) -> bool:
        """
        Carga solo las filas nuevas comparando con la ultima fecha en la tabla.

        Args:
            df:         DataFrame completo del periodo.
            table_name: Nombre de la tabla destino.
            fecha_col:  Nombre de la columna de fecha en el DataFrame original.
            schema:     Esquema destino.
        """
        full_table = f"{schema}.{table_name}"

        # Nombre limpio de la columna de fecha (como quedara en PostgreSQL)
        # Aplica primero el COLUMN_MAP por si la columna de fecha esta en el mapa
        fecha_col_clean = self.COLUMN_MAP.get(fecha_col, fecha_col)
        fecha_col_clean = re.sub(
            r'[^a-z0-9_]', '',
            fecha_col_clean.strip().lower().replace(' ', '_')
        )

        try:
            with self.get_connection() as conn:
                cur = conn.cursor()

                # Verificar si la tabla ya existe
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = %s
                        AND   table_name   = %s
                    );
                """, (schema, table_name))
                tabla_existe = cur.fetchone()[0]

                if tabla_existe:
                    cur.execute(f"SELECT MAX({fecha_col_clean}) FROM {full_table};")
                    ultima_fecha = cur.fetchone()[0]
                    logging.info(f"Ultima fecha en {full_table}: {ultima_fecha}")
                else:
                    ultima_fecha = None
                    logging.info(f"Tabla {full_table} no existe — carga inicial completa")

                cur.close()
            if not fecha_col:
                logging.warning("No se especifico fecha_col para carga incremental — se cargara todo el DataFrame")
                return self.cargar(
                    df=df,
                    table_name=table_name,
                    schema=schema,
                    if_exists="append",
                    batch_size=batch_size,
                    source_file=source_file
                )
            
            else:
                # Filtrar solo filas mas nuevas que la ultima fecha cargada
                df[fecha_col] = pd.to_datetime(df[fecha_col])

                if ultima_fecha:
                    ultima_fecha = pd.to_datetime(ultima_fecha)
                    df_nuevo = df[df[fecha_col] > ultima_fecha].copy()
                else:
                    df_nuevo = df.copy()

                logging.info(f"Filas nuevas a cargar: {len(df_nuevo):,}")

                if df_nuevo.empty:
                    logging.info("No hay filas nuevas — nada que cargar")
                    return True

                return self.cargar(
                    df=df_nuevo,
                    table_name=table_name,
                    schema=schema,
                    if_exists="append",
                    batch_size=batch_size,
                    source_file=source_file
                )

        except Exception as e:
            logging.error(f"Error en carga incremental de {full_table}: {e}")
            return False

    # ========================
    # CONSULTA
    # ========================
    def consultar(self, sql: str, params: list = None) -> Optional[pd.DataFrame]:
        """Ejecuta un SELECT y retorna un DataFrame."""
        try:
            with self.get_connection() as conn:
                df = pd.read_sql(sql, conn, params=params)
                logging.info(f"Consulta ejecutada — shape: {df.shape}")
                return df
        except Exception as e:
            logging.error(f"Error ejecutando consulta: {e}")
            return None


    def preparar_y_cargar(self, df: pd.DataFrame, table_name: str, schema: str = "raw"):
        """
        Crea la tabla automáticamente si no existe y realiza UPSERT basado en 'id'.
        """
        df = self._limpiar_columnas(df)
        full_table = f"{schema}.{table_name}"
        
        # 1. Mapeo de columnas para SQL
        cols_def = []
        for col, dtype in zip(df.columns, df.dtypes):
            tipo_pg = self._pg_type(dtype, col)
            # Forzamos que la columna 'id' sea la PRIMARY KEY de la tabla
            if col.lower() == 'id':
                cols_def.append(f"{col} BIGINT PRIMARY KEY")
            else:
                cols_def.append(f"{col} {tipo_pg}")

        create_sql = f"CREATE TABLE IF NOT EXISTS {full_table} ({', '.join(cols_def)});"

        # 2. Lógica de Upsert (Sincronización total)
        cols = list(df.columns)
        placeholders = ", ".join(["%s"] * len(cols))
        # Excluimos 'id' del SET para no intentar actualizar la PK
        update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in cols if c.lower() != 'id'])
        
        upsert_sql = f"""
            INSERT INTO {full_table} ({', '.join(cols)}) 
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {update_set};
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")
            cur.execute(create_sql) # Crea la tabla con la estructura del DF
            
            # Convertir NaNs a None para evitar errores en Postgres
            values = df.replace({np.nan: None}).values.tolist()
            psycopg2.extras.execute_batch(cur, upsert_sql, values)
            conn.commit()
            logging.info(f"Sincronización exitosa en {full_table} ({len(df)} filas)")
    
    def aplanar_datos_odoo(data_list):
        """Convierte listas [id, name] de Odoo en solo el nombre o valor limpio."""
        if not data_list:
            return pd.DataFrame()
            
        df = pd.DataFrame(data_list)
        
        for col in df.columns:
            # Si la columna tiene listas (Many2one), extraemos el nombre (posición 1)
            # Usamos apply para manejar casos donde el valor sea False/None
            if df[col].apply(lambda x: isinstance(x, (list, tuple))).any():
                df[col] = df[col].apply(lambda x: x[1] if isinstance(x, (list, tuple)) else x)
                
        return df