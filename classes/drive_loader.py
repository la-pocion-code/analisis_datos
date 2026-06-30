"""
drive_loader.py
===============
Módulo para leer archivos desde Google Drive y cargarlos al DW en Railway.
Complementa db_loader.py — úsalos juntos.

Uso básico:
    from drive_loader import DriveLoader
    from db_loader import DBLoader

    dl = DriveLoader()
    loader = DBLoader()

    # Leer un Excel de Drive como DataFrame
    df = dl.leer_excel(FILE_ID)

    # Leer una carpeta completa de CSVs y consolidarlos
    df = dl.consolidar_carpeta(FOLDER_ID, extension='csv')

    # Cargar al DW
    loader.cargar(df, 'dim_kits', schema='raw', if_exists='replace')
"""

import io
import os
import logging
import pandas as pd
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("drive_loader.log"),
        logging.StreamHandler()
    ]
)

# ========================
# IDs DE ARCHIVOS Y CARPETAS EN DRIVE
# Actualizar aquí si cambian — nunca hardcodear en el notebook
# ========================
DRIVE_IDS = {

    # ---- DIMENSIONES (data/) ----
    'kits':                  '1FecszstBm_TWIkZn_Y7IizhgHAGM7NBC',
    'clientes_padres':       '1bQWAZRPDj7pNX_5YSe-x9NLjdLFKWjgf',
    'ciudad':                '1JKZXhXltR6zAqvHQadyIjhyUrBfyr0US',
    'zonas':                 '1o7Es37xK0YEnJQMaHkkLyQ5s4w6uhUVK',
    'zonas_cundinamarca':    '1dLSDaPU_niujTAqVuIpO4sZlXviKeZ_m',
    'base_bogota':           '1YygVyyFIg2oEaGlRaLLOGAqGk6vE0u-F',
    'clientes_impulso':      '1ezwZO9NV_B2gayxZOCCwO1wGKekVZFG5',
    'base_cartera':          '12jFtyCQ4fCGMco3L1RD4JQ8CfG4eGd8A',
    'presupuesto_general':   '1YgZe508exLdxZ29krUCGSaUFIc3fLO4Z',
    'categorias':            '1Fldo8b-MfoxHXc9UKyJbjJm7Ho2gHHlc',
    'lineas_categorias':     '1XA0NKGTWxqYdUiaaMdnflTbk610kVQ-s',
    'ofertas':               '1L74N-F3sKEEJgEk2HnOsoJKX-51KF_Jy',
    'costos_productos':      '15D5Vz46XqLAghJ1hVQPncP4N3uoabZj0',
    'bd_clientes':           '13yLRqlQ79sZMrUxl_Lzm0QheFuJ_9QpR',
    'deptos':                '1zqchcPXNKIwP2Vk5CnCTfpLhDmUtHHK1',

    # ---- CARPETAS ----
    'carpeta_data':          '1NkkdC7p_ird1KaQbA7_70O7dvFA66GBA',
    'carpeta_clean_data':    '107OKNu-sdCaYN0QjynslKX8CKCe-yUJr',
    'carpeta_exploded':      '1G-PnUUvHENNKeTXlHelQBGWb_sna5oDn',
    'carpeta_cartera':       '1CFTK3YYWKny8wujXlhqTxcDI2KxYXrm4',
    'carpeta_contabilidad':  '134jlktluBLbxgYqcm3ZCgdzGOpEc9yXj',
    'carpeta_cuentas_clave': '1wC79opFds_JN6p0Q-WtwE-czYxgjTsiC',
}


class DriveLoader:
    """
    Lee archivos desde Google Drive usando una Service Account.
    Compatible con Excel (.xlsx) y CSV.
    """

    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

    def __init__(self):
        creds_path = os.getenv('GOOGLE_CREDENTIALS_PATH')
        if not creds_path or not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"No se encontró el archivo de credenciales: {creds_path}\n"
                "Verifica que GOOGLE_CREDENTIALS_PATH esté definido en el .env"
            )
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=self.SCOPES
        )
        self.service = build('drive', 'v3', credentials=creds)
        logging.info("DriveLoader inicializado correctamente")

    # ========================
    # LEER ARCHIVO
    # ========================
    def _descargar_bytes(self, file_id: str) -> tuple:
        """Descarga un archivo de Drive y retorna (bytes, nombre, mimeType)."""
        meta = self.service.files().get(
            fileId=file_id,
            fields='name, mimeType'
        ).execute()

        nombre   = meta['name']
        mimetype = meta['mimeType']

        # Google Sheets → exportar como xlsx
        if mimetype == 'application/vnd.google-apps.spreadsheet':
            export_mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            request = self.service.files().export_media(
                fileId=file_id, mimeType=export_mime
            )
        else:
            request = self.service.files().get_media(fileId=file_id)

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        buffer.seek(0)
        logging.info(f"Archivo descargado: {nombre}")
        return buffer, nombre, mimetype

    def read_excel(
        self,
        file_id: str,
        sheet_name=0,
        **kwargs
    ) -> pd.DataFrame:
        """
        Lee un archivo Excel (.xlsx) o Google Sheets desde Drive.

        Args:
            file_id:    ID del archivo en Drive.
            sheet_name: Hoja a leer (nombre o índice). Default: primera hoja.
            **kwargs:   Argumentos adicionales para pd.read_excel.

        Returns:
            DataFrame con el contenido del archivo.
        """
        buffer, nombre, _ = self._descargar_bytes(file_id)
        df = pd.read_excel(buffer, sheet_name=sheet_name, **kwargs)
        logging.info(f"Excel leído: {nombre} — shape: {df.shape}")
        return df

    def read_csv(
        self,
        file_id: str,
        sep=';',
        decimal=',',
        encoding='utf-8',
        **kwargs
    ) -> pd.DataFrame:
        """
        Lee un archivo CSV desde Drive.

        Args:
            file_id:  ID del archivo en Drive.
            sep:      Separador. Default: ';'
            decimal:  Decimal. Default: ','
            encoding: Encoding. Default: 'utf-8'

        Returns:
            DataFrame con el contenido del archivo.
        """
        buffer, nombre, _ = self._descargar_bytes(file_id)
        try:
            df = pd.read_csv(buffer, sep=sep, decimal=decimal,
                             encoding=encoding, **kwargs)
        except UnicodeDecodeError:
            buffer.seek(0)
            df = pd.read_csv(buffer, sep=sep, decimal=decimal,
                             encoding='latin1', **kwargs)
        logging.info(f"CSV leído: {nombre} — shape: {df.shape}")
        return df

    # ========================
    # LISTAR Y CONSOLIDAR CARPETA
    # ========================
    def list_folder(self, folder_id: str, extension: str = None) -> list:
        """
        Lista archivos en una carpeta de Drive.

        Args:
            folder_id: ID de la carpeta.
            extension: Filtrar por extensión ('xlsx', 'csv'). None = todos.

        Returns:
            Lista de dicts con {id, name, mimeType}.
        """
        query = f"'{folder_id}' in parents and trashed=false"
        results = self.service.files().list(
            q=query,
            fields="files(id, name, mimeType)",
            orderBy="name"
        ).execute()

        archivos = results.get('files', [])

        if extension:
            archivos = [
                f for f in archivos
                if f['name'].lower().endswith(f'.{extension}')
                or f['mimeType'] == 'application/vnd.google-apps.spreadsheet'
            ]

        logging.info(f"Carpeta {folder_id}: {len(archivos)} archivos encontrados")
        return archivos

    def consolidar_carpeta(
        self,
        folder_id: str,
        extension: str = 'csv',
        sheet_name=0,
        sep=';',
        decimal=',',
        **kwargs
    ) -> pd.DataFrame:
        """
        Consolida todos los archivos de una carpeta en un solo DataFrame.
        Equivalente al método consolidar_carpeta() de ReportClassNew pero desde Drive.

        Args:
            folder_id:  ID de la carpeta en Drive.
            extension:  'csv' o 'xlsx'.
            sheet_name: Hoja a leer (solo para xlsx).
            sep:        Separador CSV.
            decimal:    Decimal CSV.

        Returns:
            DataFrame consolidado.
        """
        archivos = self.list_folder(folder_id, extension)

        if not archivos:
            logging.warning(f"No se encontraron archivos .{extension} en carpeta {folder_id}")
            return pd.DataFrame()

        dfs = []
        for f in archivos:
            try:
                if extension == 'csv':
                    df = self.read_csv(f['id'], sep=sep, decimal=decimal, **kwargs)
                else:
                    df = self.read_excel(f['id'], sheet_name=sheet_name, **kwargs)
                dfs.append(df)
                logging.info(f"  ✓ {f['name']} — {len(df):,} filas")
            except Exception as e:
                logging.error(f"  ✗ {f['name']}: {e}")

        if not dfs:
            return pd.DataFrame()

        df_final = pd.concat(dfs, ignore_index=True)
        logging.info(f"Consolidación completada — shape total: {df_final.shape}")
        return df_final
