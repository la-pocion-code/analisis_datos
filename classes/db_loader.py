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
        # Motivo del ultimo fallo de cargar(). Existe porque cargar() devuelve un bool
        # y el llamador no tenia forma de imprimir POR QUE fallo: el resumen de
        # cargar_bi_datasets.py decia "ERROR carga" con el numero de filas al lado, que
        # se lee como exito. El detalle quedaba solo en db_loader.log.
        self.ultimo_error = None

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
        try:
            if np.issubdtype(dtype, np.integer):
                return "BIGINT"
            elif np.issubdtype(dtype, np.floating):
                return "NUMERIC"
            elif np.issubdtype(dtype, np.datetime64):
                return "TIMESTAMP"
        except TypeError:
            pass  # pandas 2.x StringDtype / extension types caen a VARCHAR
        if col in ('OBSERVACIONES', 'DESCRIPCION', 'NOTAS', 'DETALLE', 'CUERPO_HTML'):
            return "TEXT"
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
    # DEPENDENCIAS DE UNA TABLA (vistas y vistas materializadas que la LEEN)
    # ========================
    def _dependientes(self, cur, schema: str, table_name: str) -> list:
        """
        Devuelve [(objeto, tipo)] de las vistas/MV que leen esta tabla.

        ⚠ POR QUE EXISTE: `if_exists='replace'` se implementaba con DROP TABLE (sin
        CASCADE), y PostgreSQL lo RECHAZA en cuanto una vista o MV cuelga de la tabla:

            cannot drop table marts.bi_presupuesto because other objects depend on it
            DETAIL: materialized view marts.mv_presupuesto_mes depends on ...

        Eso dejo `marts.bi_presupuesto` y `marts.bi_nielsen` imposibles de recargar
        desde que se crearon las MV de los dashboards (28-jul-2026): el DROP fallaba,
        NO se insertaba ni una fila, y la tabla se quedaba con el dato viejo. Las mismas
        cargas funcionaban el 22 y 23-jul, cuando las MV aun no existian.

        ⚠ La salida NO se usa para hacer DROP ... CASCADE (que es lo que sugiere el HINT
        de Postgres): eso se llevaria por delante las MV y sus GRANT a intranet_ro, y la
        intranet responderia "relation does not exist" hasta re-ejecutar su DDL y el
        24_rol_intranet.sql. Se usa para elegir la ruta TRUNCATE, que preserva la tabla,
        sus tipos, sus permisos y las MV.
        """
        cur.execute(
            """
            SELECT DISTINCT
                   dep_ns.nspname || '.' || dep.relname                    AS objeto,
                   CASE dep.relkind WHEN 'm' THEN 'vista materializada'
                                    WHEN 'v' THEN 'vista'
                                    ELSE dep.relkind::TEXT END             AS tipo
            FROM pg_depend d
            JOIN pg_rewrite     r      ON r.oid       = d.objid
            JOIN pg_class       dep    ON dep.oid     = r.ev_class
            JOIN pg_namespace   dep_ns ON dep_ns.oid  = dep.relnamespace
            JOIN pg_class       src    ON src.oid     = d.refobjid
            JOIN pg_namespace   src_ns ON src_ns.oid  = src.relnamespace
            WHERE src_ns.nspname = %s
              AND src.relname    = %s
              AND d.classid      = 'pg_rewrite'::regclass
              AND d.refclassid   = 'pg_class'::regclass
              AND dep.oid       <> src.oid
            ORDER BY 1
            """,
            (schema, table_name),
        )
        return [(objeto, tipo) for objeto, tipo in cur.fetchall()]

    def _columnas_actuales(self, cur, schema: str, table_name: str) -> list:
        """Columnas que ya tiene la tabla en la base, en orden."""
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table_name),
        )
        return [col for (col,) in cur.fetchall()]

    def _preparar_truncate(self, cur, conn, schema, table_name, full_table, df, deps) -> None:
        """
        Reconcilia las columnas de la tabla con las del DataFrame ANTES de un
        TRUNCATE + INSERT. En la ruta DROP + CREATE la estructura se rehacia sola; aqui
        la tabla sobrevive, asi que hay que mirar si el origen cambio de forma.

        - Columna NUEVA en el archivo -> ALTER TABLE ADD COLUMN. Es aditivo: no puede
          romper una vista/MV, que solo nombran las columnas que ya usaban.
        - Columna que la tabla TIENE y el archivo YA NO trae -> se ABORTA. Truncar e
          insertar sin ella dejaria a la MV leyendo una columna que nadie alimenta: se
          quedaria toda en NULL sin un solo error, y el tablero mostraria huecos o
          buckets '(sin ...)' como si fuera un problema de datos del negocio.
          Renombrar una columna en el Excel se ve exactamente asi (una que falta + una
          nueva), y es el caso mas probable.

        Levanta RuntimeError con instrucciones; lo captura el except de cargar().
        """
        actuales = self._columnas_actuales(cur, schema, table_name)
        if not actuales:      # la tabla no existe: no hay nada que reconciliar
            return

        # `id` es el SERIAL que pone esta clase, nunca viene en el archivo.
        faltantes = [c for c in actuales if c != 'id' and c not in df.columns]
        nuevas    = [c for c in df.columns if c not in actuales]

        if faltantes:
            objetos = ", ".join(o for o, _ in deps)
            raise RuntimeError(
                f"el origen ya no trae {len(faltantes)} columna(s) que {full_table} si "
                f"tiene: {', '.join(faltantes)}. No se recarga para no dejar en NULL algo "
                f"que leen {objetos}. Revisa el encabezado del archivo (¿renombrada?); si "
                f"el cambio es intencional: DROP de esas vistas/MV, recargar, re-ejecutar "
                f"su DDL y despues sql/marts/24_rol_intranet.sql para devolver los GRANT"
            )

        for col in nuevas:
            # Mismo tipo que habria puesto el CREATE TABLE de la ruta DROP+CREATE: en este
            # punto cargar() ya hizo astype(object), asi que _pg_type da VARCHAR(512) —
            # que es justo como estan hoy todas las columnas de las bi_*.
            tipo = self._pg_type(df[col].dtype, col)
            cur.execute(f"ALTER TABLE {full_table} ADD COLUMN {col} {tipo};")
            logging.info(f"  {full_table}: columna nueva {col} {tipo} agregada")
        if nuevas:
            conn.commit()

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
                          'replace' -> recarga completa. Si la tabla NO tiene vistas/MV
                                       encima: DROP + CREATE (como siempre). Si las
                                       tiene: TRUNCATE + INSERT, porque el DROP es
                                       imposible sin CASCADE (ver _dependientes).
            batch_size:   Filas por lote.
            source_file:  Nombre del archivo origen para auditoria.
            fecha_col:    Nombre de la columna que contiene las fechas.
        Returns:
            True si fue exitoso, False si hubo error (motivo en self.ultimo_error).
        """
        start = time.time()
        full_table = f"{schema}.{table_name}"
        self.ultimo_error = None

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

                # ── Si replace: elegir COMO se hace la recarga ──────────────────
                # Con vistas/MV encima el DROP es imposible (sin CASCADE, que no es
                # opcion) => se recarga con TRUNCATE, que las preserva.
                modo_truncate = False
                if if_exists == 'replace':
                    deps = self._dependientes(cur, schema, table_name)
                    if deps:
                        modo_truncate = True
                        detalle = ", ".join(f"{o} ({t})" for o, t in deps)
                        logging.info(
                            f"{full_table}: recarga por TRUNCATE (no se puede DROP, "
                            f"dependen {len(deps)}: {detalle})"
                        )
                        self._preparar_truncate(cur, conn, schema, table_name, full_table, df, deps)
                    else:
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

                # ⚠ En modo TRUNCATE el vaciado y los INSERT van en UNA SOLA transaccion
                # (un unico commit al final). Con un commit por lote habria una ventana en
                # la que la tabla se ve VACIA: Power BI leyendo, o un REFRESH de la MV que
                # cayera ahi, publicarian cero. Y si un lote falla, el rollback deshace
                # tambien el TRUNCATE => la tabla conserva el dato anterior COMPLETO, que
                # es mejor que media tabla.
                if modo_truncate:
                    cur.execute(f"TRUNCATE TABLE {full_table} RESTART IDENTITY;")
                    logging.info(f"  {full_table} vaciada (misma transaccion que los INSERT)")

                for i in range(0, len(values), batch_size):
                    batch = values[i:i + batch_size]
                    try:
                        psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=batch_size)
                        if not modo_truncate:
                            conn.commit()
                        filas_ok += len(batch)
                        logging.info(f"  Lote {i // batch_size + 1}: {len(batch)} filas insertadas")
                    except psycopg2.Error as e:
                        conn.rollback()
                        filas_err += len(batch)
                        logging.error(f"  Error en lote {i // batch_size + 1}: {e}")
                        if modo_truncate:
                            self.ultimo_error = f"lote {i // batch_size + 1}: {e}"
                            logging.error(
                                f"{full_table}: recarga ABORTADA y revertida; la tabla "
                                f"conserva el dato anterior completo"
                            )
                            cur.close()
                            return False

                if modo_truncate:
                    conn.commit()

                elapsed = time.time() - start
                logging.info(f"OK {filas_ok:,} filas cargadas en {full_table} ({elapsed:.1f}s)")
                if filas_err:
                    # ⚠ Antes esto devolvia True igual: una carga a medias se reportaba
                    # como OK y el llamador no tenia forma de enterarse.
                    logging.warning(f"AVISO {filas_err:,} filas con error")
                    self.ultimo_error = f"{filas_err} filas no se insertaron (ver db_loader.log)"

                cur.close()
                return filas_err == 0

        except Exception as e:
            logging.error(f"Error cargando {full_table}: {e}")
            self.ultimo_error = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
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