import pandas as pd
import holidays
from pathlib import Path
import os 
from thefuzz import process
from rapidfuzz import process
import os
import re
import tkinter as tk
from tkinter import filedialog
import numpy as np
import json
import unicodedata
from typing import Dict, Optional, List
from io import StringIO
import requests
from difflib import get_close_matches
import unicodedata
from collections import defaultdict
import fitz  # PyMuPDF



class ReportClassNew():
    """
    Esta clase contiene las funciones que su utilizan para actulizar el bi
    y las ventas procesadas.    
    """



    def consolidar_carpeta(self, extension='xlsx', sep=None, encoding=None, decimal=',',sheet_name=None, ruta_carpeta=None, axis=0, ignore_index=True):
        """
        Consolida todos los archivos de una carpeta en un único DataFrame de pandas.

        Lee archivos con una extensión específica (xlsx, xls, xlsb, csv) desde una carpeta
        seleccionada manualmente o por parámetro, y los concatena en un solo DataFrame.

        Args:
            extension (str): Extensión de los archivos a consolidar. Ejemplo: 'xlsx', 'csv'.
            sheet_name (str/int/list/None): Hoja(s) de Excel a leer. Por defecto lee la primera hoja.
            ruta_carpeta (str, optional): Ruta de la carpeta con los archivos. Si es None, se abre un diálogo.

        Returns:
            pd.DataFrame: DataFrame con los datos consolidados. Si no hay archivos válidos, retorna vacío.
        """
        carpeta_seleccionada = ''

        if ruta_carpeta:
            # Validamos que la ruta proporcionada (ya un string válido) sea un directorio real.
            if os.path.isdir(ruta_carpeta):
                carpeta_seleccionada = ruta_carpeta
            else:
                # Este es el manejo de error de ejecución que SÍ podemos programar.
                print(f"Error: La ruta '{ruta_carpeta}' no existe o no es un directorio.")
                print("Sugerencia: Verifique que la ruta esté bien escrita y, si usa Windows, "
                    "que comience con 'r' (ej: r'C:\\...').")
                return pd.DataFrame()     

        else:
            # Si no se proporciona una ruta, abrir el diálogo
            root = tk.Tk()
            root.withdraw()
            carpeta_seleccionada = filedialog.askdirectory(
                title="Selecciona la carpeta que contiene los archivos"
            )

        if not carpeta_seleccionada:
            print("Operación cancelada. No se seleccionó ninguna carpeta.")
            return pd.DataFrame()

        lista_dataframes = []
        print(f"Buscando archivos con extensión '.{extension}' en: {carpeta_seleccionada}")

        for archivo in os.listdir(carpeta_seleccionada):
            if archivo.endswith(f'.{extension}'):
                ruta_completa = os.path.join(carpeta_seleccionada, archivo)
                try:
                    if extension in ['xlsx', 'xls', 'xlsb']:
                        # pd.read_excel puede devolver un DataFrame o un dict de DataFrames
                        df_o_dict = pd.read_excel(ruta_completa, sheet_name=sheet_name)
                        
                        if isinstance(df_o_dict, dict):
                            # Si es un diccionario, concatenar todas las hojas de ese archivo
                            df_archivo = pd.concat(df_o_dict.values(), ignore_index=True)
                            lista_dataframes.append(df_archivo)
                        else:
                            # Si es un solo DataFrame
                            lista_dataframes.append(df_o_dict)
                            
                    elif extension == 'csv':
                        df = pd.read_csv(ruta_completa, sep=sep, encoding=encoding, decimal=decimal, engine='python')
                        lista_dataframes.append(df)

                    print(f"  - Archivo '{archivo}' leído correctamente.")

                except Exception as e:
                    print(f"  - No se pudo leer el archivo '{archivo}'. Error: {e}")

        if not lista_dataframes:
            print(f"No se encontraron archivos válidos con la extensión '.{extension}' en la carpeta.")
            return pd.DataFrame()

        print("Concatenando todos los archivos...")
        df_concatenado = pd.concat(lista_dataframes, ignore_index=ignore_index, axis=axis)
        print("¡Consolidación completada!")
        
        return df_concatenado




    def validar_ruta(self):
        """
        Valida y retorna la ruta local donde se guardará la información.

        Busca la carpeta 'VENTA MENSUAL' en diferentes ubicaciones predefinidas.
        Si no la encuentra, muestra un mensaje de error.

        Returns:
            pathlib.Path: Ruta válida encontrada.
        """
        if Path("E:/Otros ordenadores/Mi portátil/VENTA MENSUAL").exists():
            ruta = Path("E:/Otros ordenadores/Mi portátil/VENTA MENSUAL")
        elif Path("G:/Otros ordenadores/Mi portátil/VENTA MENSUAL").exists():
            ruta = Path("G:/Otros ordenadores/Mi portátil/VENTA MENSUAL")
        elif Path("C:/Users/Dataa/Desktop/VENTAS/VENTA MENSUAL").exists():
            ruta = Path("C:/Users/Dataa/Desktop/VENTAS/VENTA MENSUAL")
        else:
            print('No se encontro la ruta')

        return ruta

    
    @staticmethod
    def limpiar_documento(valor):
        """
        Esta función limpia y extrae el número de identificación de un valor dado.
        Args:
            valor (str/int/float): Valor que contiene el número de identificación.
        Returns:
            str/pd.NA: Número de identificación limpio o pd.NA si no es válido.
        """
        valor = str(valor).strip()
        valor = valor.replace('.', '').replace("'",'').replace("”", '').replace("’",'')  # Elimina espacios en blanco

        # Si el valor es NaN o vacío, retorna NaN
        if pd.isna(valor) or str(valor).strip() == pd.NaT:
            return pd.NA
        # Si el valor contiene solo letras, retorna NaN
        if re.fullmatch(r'[A-Za-z]+', str(valor).strip()):
            return pd.NA
        # Extrae los dígitos iniciales antes de cualquier carácter no numérico
        match = re.match(r'^(\d+)', str(valor))
        if match:
            return match.group(1)
        return pd.NA    

    # Función para cargar archivo Excel o CSV
    def cargar_archivo(self, titulo: str = "Selecciona un archivo (.xlsx o .csv)"):
        root = tk.Tk()

        root.withdraw() 
        root.attributes('-topmost', True)  
        nombre_archivo = filedialog.askopenfilename(
            title=titulo,
            filetypes=[
                ("Archivos Excel", "*.xlsx"),
                ("Archivos CSV", "*.csv"),
                ("Todos los archivos", "*.*"),
            ]
        )

        if not nombre_archivo:
            print("No se seleccionó archivo.")
            return None

        try:
            ext = os.path.splitext(nombre_archivo)[1].lower()

            if ext == ".xlsx":
                df = pd.read_excel(nombre_archivo)
            elif ext == ".csv":
                df = pd.read_csv(nombre_archivo)
            else:
                raise ValueError("Formato no soportado")

            print(f"Archivo '{os.path.basename(nombre_archivo)}' cargado correctamente.")
            return df

        except Exception as e:
            print(f"Error al cargar el archivo: {e}")
            return None

    def transformar_base(self): 
        """
        Transforma la base de ventas y genera un archivo limpio y enriquecido.

        Integra información de ventas, TRM, ciudades y clientes. Aplica limpieza,
        normalización y categorización de datos. Si 'origen' es True, descuenta notas crédito.

        Args:
            origen (bool): Si True, ejecuta el proceso de notas crédito antes de transformar.

        Returns:
            dict: Diccionario con la base transformada, nombre del archivo, facturas afectadas,
                    y registros con errores de categorización.
        """
    

        # ---- Cargar ventas ----
        df_ventas = self.cargar_archivo("Selecciona el archivo de ventas (.xlsx o .csv)")

        # ---- Cargar notas crédito ----
        df_notas_credito = self.cargar_archivo("Selecciona el archivo de notas crédito (.xlsx o .csv)")


        # Extraer el número de factura usando una expresión regular
        pattern = re.compile(r":\s*([^\s,]+)")

        df_notas_credito['NUMERO_FACTURA'] = df_notas_credito['Líneas de factura/Referencia'].apply(
            lambda x: pattern.search(x).group(1) if pd.notna(x) and pattern.search(x) else None
        )

        # Guarda en la variables las ventas sin tipo de cliente y con etiqueta mayorista
        # Esta variables se guarda en el archivo de errores

        etiqueta_mayorista = df_ventas[(df_ventas['Tipo de cliente'].isna())&
                    (df_ventas['Etiqueta contacto']=='MAYORISTA NV')
                    ] 
        # Copia de la etiqueta los clientes mayoristas que aparecen en blanco
        df_ventas.loc[(df_ventas['Tipo de cliente'].isna())&
                    (df_ventas['Etiqueta contacto']=='MAYORISTA NV'), 'Tipo de cliente'
                    ] = 'MAYORISTA NV'

        # Mapear los valores de 'Equipo de ventas', 'Asesor Comercial' y 'Tipo de cliente' desde las facturas para ponerlo en cada línea de la factura
        equipo_por_factura = (
            df_ventas
            .groupby('Líneas de factura/Número')['Equipo de ventas']
            .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
            .to_dict()
        )

        df_ventas['Equipo de ventas'] = df_ventas['Líneas de factura/Número'].map(equipo_por_factura)

        asesor_por_factura = (
            df_ventas
            .groupby('Líneas de factura/Número')['Asesor Comercial']
            .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
            .to_dict()
        )
        df_ventas['Asesor Comercial'] = df_ventas['Líneas de factura/Número'].map(asesor_por_factura)

        tipo_por_factura = (
            df_ventas
            .groupby('Líneas de factura/Número')['Tipo de cliente']
            .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
            .to_dict()
        )
        df_ventas['Tipo de cliente'] = df_ventas['Líneas de factura/Número'].map(tipo_por_factura)

        # Mapear el valor de 'Total firmado' desde las facturas para ponerlo en cada línea de la factura
        total_por_factura = (
            df_ventas
            .groupby('Líneas de factura/Número')['Total firmado']
            .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
            .to_dict()
        )
        df_ventas['Total firmado'] = df_ventas['Líneas de factura/Número'].map(total_por_factura)


        # Calcula la TRM para cada línea de factura
        df_ventas['TRM'] = df_ventas['Total firmado'] / df_ventas.groupby(
            'Líneas de factura/Número'
        )['Líneas de factura/Total'].transform('sum')

        # Eliminar la columna original y renombrar la nueva columna
        df_notas_credito = df_notas_credito.drop(columns=['Líneas de factura/Número'])
        df_notas_credito = df_notas_credito.rename(columns={'NUMERO_FACTURA': 'Líneas de factura/Número'})

        # Convertir las cantidades y totales de las notas crédito a valores negativos
        df_notas_credito['Líneas de factura/Cantidad'] = -df_notas_credito['Líneas de factura/Cantidad']
        df_notas_credito['Líneas de factura/Total'] = -df_notas_credito['Líneas de factura/Total']
        df_notas_credito['Líneas de factura/Subtotal'] = -df_notas_credito['Líneas de factura/Subtotal'] # Sin impuestos

        # Crear una columna temporal que combine NUMERO_FACTURA y PRODUCTO
        df_ventas['NUMERO_FACTURA-PRODUCTO'] = df_ventas['Líneas de factura/Número'] + '-' + df_ventas['Líneas de factura/Producto']
        df_notas_credito['NUMERO_FACTURA-PRODUCTO'] = df_notas_credito['Líneas de factura/Número'] + '-' + df_notas_credito['Líneas de factura/Producto']
        # Filtrar las notas crédito para incluir solo las que coinciden con ventas existentes
        notas_credito_validas = df_notas_credito['NUMERO_FACTURA-PRODUCTO'].isin(df_ventas['NUMERO_FACTURA-PRODUCTO'])
        df_notas_credito_filtrado = df_notas_credito[notas_credito_validas]
        # Combinar ambos datasets (ventas y notas crédito)
        df_consolidado = pd.concat([df_ventas, df_notas_credito_filtrado], ignore_index=True)


        # Renombrar columnas para estandarizar nombres
        df_consolidado= df_consolidado.rename(columns={
            'Equipo de ventas': 'Equipo de Ventas',
            'Líneas de factura/Contacto':'Líneas de factura/Asociado',
            'Líneas de factura/Contacto/Ciudad':'Líneas de factura/Asociado/Ciudad',
            'Líneas de factura/Contacto/Correo electrónico':'Líneas de factura/Asociado/Correo electrónico',
            'Líneas de factura/Contacto/Estado':'Líneas de factura/Asociado/Estado',
            'Líneas de factura/Contacto/NIF':'Líneas de factura/Asociado/Número de Identificación',
            'Líneas de factura/Contacto/Teléfono':'Líneas de factura/Asociado/Teléfono',
            'Origen/Nombre de la fuente':'Origen/Nombre de la Fuente'

        })



        #  Agrupar por la columna temporal NUMERO_FACTURA-PRODUCTO
        df_consolidado = df_consolidado.groupby(
            'NUMERO_FACTURA-PRODUCTO',  # Agrupar por la combinación de factura y producto
            as_index=False
        ).agg({
            'Líneas de factura/Fecha de factura': 'first',
            'Líneas de factura/Asociado': 'first',
            'Líneas de factura/Número': 'first',
            'Líneas de factura/Producto': 'first',
            'Líneas de factura/Cantidad': 'sum',  # Sumar las cantidades
            'Líneas de factura/Subtotal': 'sum',  # Sumar los subtotales
            'Líneas de factura/Total': 'sum',     # Sumar los totales
            'Líneas de factura/Asociado/Número de Identificación': 'first',
            'Líneas de factura/Asociado/Teléfono': 'first',
            'Líneas de factura/Asociado/Correo electrónico': 'first',
            'Líneas de factura/Asociado/Ciudad': 'first',
            'Líneas de factura/Asociado/Estado': 'first',
            'Equipo de Ventas': 'first',
            'Líneas de factura/Referencia': 'first',
            'Asesor Comercial': 'first',
            'Origen': 'first',
            'Origen/Nombre de la Fuente': 'first',
            'Tipo de cliente': 'first',
            'Etiqueta contacto': 'first',
            'TRM': 'first',
            'Total firmado': 'first'
        })


        #  Eliminar la columna temporal NUMERO_FACTURA-PRODUCTO
        df_consolidado.drop(columns=['NUMERO_FACTURA-PRODUCTO'], inplace=True)
        #  Filtrar solo las filas donde la cantidad sea mayor que 0 (eliminar ventas canceladas)
        df_consolidado['Líneas de factura/Cantidad'] = pd.to_numeric(df_consolidado['Líneas de factura/Cantidad'], errors='coerce')
        df_consolidado = df_consolidado[df_consolidado['Líneas de factura/Cantidad'] > 0]

        df_consolidado['Líneas de factura/Fecha de factura'] = pd.to_datetime(df_consolidado['Líneas de factura/Fecha de factura'])
        fecha_min = df_consolidado['Líneas de factura/Fecha de factura'].min()
        fecha_max = df_consolidado['Líneas de factura/Fecha de factura'].max()

        # Obtiene el nombre del archivo de ventas según el rango de fechas
        mes_min = fecha_min.strftime('%m')
        anio_min = fecha_min.strftime('%Y')
        mes_max = fecha_max.strftime('%m')
        anio_max = fecha_max.strftime('%Y') 
        mes_min = fecha_min.month_name(locale='es_ES')
        mes_max = fecha_max.month_name(locale='es_ES')

        mes = f"{mes_min}_{anio_min}" if mes_min == mes_max else f"{mes_min}_{anio_min}_{mes_max}_{anio_max}"
        nombre_archivo = f'Ventas_{mes}.csv'

        # Obtener el listado de facturas afectadas por notas crédito
        print("Facturas afectadas por notas crédito:")
        facturas_afectadas = df_notas_credito_filtrado[['Líneas de factura/Número', 'Líneas de factura/Producto', 'Líneas de factura/Cantidad', 'Líneas de factura/Total']].dropna(subset=['Líneas de factura/Número'])
        facturas_afectadas.shape

        # Extraer el código de país y reemplazar NaN con "Desconocido" en un solo paso
        df_consolidado['pais'] = df_consolidado['Líneas de factura/Asociado/Estado'].str.extract(r'\(([A-Z]{2})\)').fillna("Desconocido")

        df_consolidado['Líneas de factura/Asociado/Ciudad'] = df_consolidado['Líneas de factura/Asociado/Ciudad'].astype(str).fillna("Desconocido")

        # linea modificada y sujeta a modificaciones en caso de nueva Linea de productos
        df_filtrado = df_consolidado[df_consolidado['Líneas de factura/Producto'].str.startswith(('[PCN','[KD','[TNG','[B8'))].copy()  


        # Ahora puedes modificar df_filtrado sin preocuparte por el warning
        df_filtrado.loc[:, 'Líneas de factura/Fecha de factura'] = pd.to_datetime(df_filtrado['Líneas de factura/Fecha de factura'])
        # df_filtrado['Líneas de factura/Fecha de factura'] = pd.to_datetime(df_filtrado['Líneas de factura/Fecha de factura'])

        df_filtrado = df_filtrado.reset_index(drop=True)
        # Convertir la columna 'Líneas de factura/Total' a tipo numérico
        df_filtrado['Líneas de factura/Total'] = pd.to_numeric(df_filtrado['Líneas de factura/Total'], errors='coerce')
        # df_filtrado.loc['', 'Líneas de factura/Total'] = pd.to_numeric(df_filtrado['Líneas de factura/Total'], errors='coerce')
        # Verificar si hay valores nulos después de la conversión
        print("Valores nulos en 'Líneas de factura/Total':", df_filtrado['Líneas de factura/Total'].isnull().sum())
        # Paso 1: Verificar valores nulos en la columna de fecha
        print("Valores nulos en 'Líneas de factura/Fecha de factura':", df_filtrado['Líneas de factura/Fecha de factura'].isnull().sum())
        df_filtrado = df_filtrado.dropna(subset=['Líneas de factura/Fecha de factura'])


        # Calcular la columna 'TOTAL' como el producto de 'Líneas de factura/Subtotal' y 'TRM'
        df_filtrado['TOTAL'] = df_filtrado['Líneas de factura/Subtotal'] * df_filtrado['TRM']

        df_filtrado['TOTAL CON IMP'] = df_filtrado['Líneas de factura/Total'] * df_filtrado['TRM']
        df_filtrado['Líneas de factura/Asociado/Ciudad'] = (
            df_filtrado['Líneas de factura/Asociado/Ciudad']
                .astype(str)
                .str.replace(r'[^A-Za-z\s]', '', regex=True)   # deja solo letras y espacios
                .str.replace(r'\s+', ' ', regex=True)                       # colapsa espacios múltiples
                .str.strip()                                                 # elimina espacios extremos
        )

        ###

        

        # Cargar catálogo Colombia
        # ciudad_url = "https://www.datos.gov.co/resource/gdxc-w37w.csv?$limit=5000"
        # DF_CIUDADES = pd.read_csv(ciudad_url)
        # ruta = self.validar_ruta()
        ruta = r'G:\Otros ordenadores\Mi portátil\VENTA MENSUAL\data\ciudad.xlsx' # linea temporal
        # ciudad_url = "https://www.datos.gov.co/resource/gdxc-w37w.csv?$limit=5000"

        # response = requests.get(ciudad_url, verify=True)
        # response.raise_for_status()

        # DF_CIUDADES = pd.read_csv(StringIO(response.text))


        DF_CIUDADES = pd.read_excel(ruta)



        # DF_CIUDADES = pd.read_excel(r"C:\Users\Dataa\Desktop\VENTAS\VENTA MENSUAL\CIUDAD.xlsx") # Dataset con nombres correctos
        DF_CIUDADES = DF_CIUDADES.rename(columns= {'nom_mpio':'Ciudad_Correcta'})
        df_resultado = df_filtrado.rename(columns= {'Líneas de factura/Asociado/Ciudad':'Ciudad'})


        # Normalización
        def normalizar(texto):
            if pd.isna(texto):
                return ""
            texto = str(texto).strip().lower()
            texto = ''.join(
                c for c in unicodedata.normalize('NFD', texto)
                if unicodedata.category(c) != 'Mn'
            )
            return texto

        DF_CIUDADES['Ciudad_norm'] = DF_CIUDADES['Ciudad_Correcta'].apply(normalizar)

        df_resultado['Ciudad'] = df_resultado['Ciudad'].apply(normalizar)
        lista_ciudades_norm = DF_CIUDADES['Ciudad_norm'].unique()


        df_resultado['validar_ciudad'] = np.where(
            df_resultado['Ciudad'].isin(lista_ciudades_norm),
            df_resultado['Ciudad'],
            ""
        )


        #  Diccionario de alias
        ALIASES = {
            'cali': 'CALI',
            'buga':'GUADALAJARA DE BUGA',
            'bogot': 'BOGOTA D.C.',
            'bogota':'BOGOTA D.C.'
        }

        df_resultado['departamento']  = df_resultado['Líneas de factura/Asociado/Estado'].str.split(' ').str[0].apply(normalizar)
        DF_CIUDADES['departamento'] = DF_CIUDADES['NOM_DEPTO'].apply(normalizar)

        def corregir_ciudad_avanzada(row, df_ciudades, columna_ciudad='Ciudad', columna_depto='departamento'):
            """
            Versión avanzada con múltiples estrategias de corrección.
            """
            ciudad = row[columna_ciudad]
            departamento = row[columna_depto] if columna_depto in row.index else None
            
            # 1. Coincidencia exacta (ya validada)
            if row.get('validar_ciudad', '') != "":
                match = df_ciudades[df_ciudades['Ciudad_norm'] == ciudad]
                if not match.empty:
                    return match.iloc[0]['Ciudad_Correcta']
            
            # 2. Verificar aliases primero
            if ciudad in ALIASES:
                return ALIASES[ciudad]
            
            # 3. Filtrar por departamento
            if departamento and departamento != "":
                ciudades_filtradas = df_ciudades[df_ciudades['departamento'] == departamento]
            else:
                ciudades_filtradas = df_ciudades
            
            if ciudades_filtradas.empty:
                ciudades_filtradas = df_ciudades
            
            lista_ciudades = ciudades_filtradas['Ciudad_norm'].tolist()
            
            # 4. Coincidencia aproximada con diferentes niveles
            for cutoff in [0.9, 0.85, 0.8]:  # Probar diferentes niveles de similitud
                coincidencias = get_close_matches(ciudad, lista_ciudades, n=1, cutoff=cutoff)
                if coincidencias:
                    match = ciudades_filtradas[ciudades_filtradas['Ciudad_norm'] == coincidencias[0]]
                    if not match.empty:
                        return match.iloc[0]['Ciudad_Correcta']
            
            # 5. Si no se encuentra, retornar original
            return ciudad

        # Aplicar versión avanzada
        df_resultado['Ciudad_Corregida'] = df_resultado.apply(
            lambda row: corregir_ciudad_avanzada(row, DF_CIUDADES), 
            axis=1
        )
        #  Aplicación
        # df_resultado = df_filtrado.rename(columns={
        #     'Líneas de factura/Asociado/Ciudad': 'Ciudad'
        # })



        # Diccionario para renombrar las columnas
        nuevos_nombres = {
            'Líneas de factura/Fecha de factura': 'Fecha_Factura',
            'Líneas de factura/Asociado': 'Cliente',
            'Líneas de factura/Número': 'Numero_Factura',
            'Líneas de factura/Producto': 'Producto',
            'Líneas de factura/Cantidad': 'Cantidad',
            'Líneas de factura/Total': 'Total',
            'Líneas de factura/Asociado/Número de Identificación': 'Identificacion_Cliente',
            'Líneas de factura/Asociado/Teléfono': 'Telefono',
            'Líneas de factura/Asociado/Correo electrónico': 'Email',
            'Líneas de factura/Asociado/Ciudad': 'Ciudad',
            'Líneas de factura/Asociado/Estado': 'Departamento',
            'Equipo de Ventas': 'Equipo_Ventas',
            'Líneas de factura/Referencia': 'Referencia',
            'pais': 'Pais',
            'Fecha': 'Fecha_TRM',
            'TRM': 'TRM',
            'TOTAL': 'Total($)',
            'TOTAL CON IMP': 'Total_Con_Impuestos'
        }


        # Renombrar las columnas
        df_resultado = df_resultado.rename(columns=nuevos_nombres)
        

        # Verificar el resultado

        # Extraer el día, mes y año en nuevas columnas
        df_resultado['Dia'] = df_resultado['Fecha_Factura'].dt.day
        df_resultado['Mes'] = df_resultado['Fecha_Factura'].dt.month
        df_resultado['Año'] = df_resultado['Fecha_Factura'].dt.year

        # Reorganizar las columnas si es necesario
        column_order = ['Numero_Factura','Fecha_Factura', 'Dia', 'Mes', 'Año', 'Cliente','Identificacion_Cliente','Producto', 'Cantidad', 
                        'Total', 'TRM', 'Total($)','Total_Con_Impuestos','Telefono', 'Email','Pais','Ciudad', 'Ciudad_Corregida', 'Departamento', 
                        'Equipo_Ventas', 'Referencia', 'Asesor Comercial', 'Tipo de cliente'] ## aqui se agregregan las nuevas columnas


        df_resultado = df_resultado[column_order]

        # Convertir la columna "Cliente" a mayúsculas
        df_resultado['Cliente'] = df_resultado['Cliente'].str.upper()

        # Eliminar espacios en blanco al principio y al final de cada valor en la columna "Cliente"
        df_resultado['Cliente'] = df_resultado['Cliente'].str.strip()
        # Convertir la columna "Producto" a mayúsculas
        df_resultado['Producto'] = df_resultado['Producto'].str.upper()
        # Eliminar espacios en blanco al principio y al final de cada valor en la columna "Producto"
        df_resultado['Producto'] = df_resultado['Producto'].str.strip()
        # Convertir los nombres de las columnas a mayúsculas
        df_resultado.columns = df_resultado.columns.str.upper()


        #  Limpieza de la columna de identificación en ambos DataFrames
        df_resultado['IDENTIFICACION_CLIENTE'] = (
            df_resultado['IDENTIFICACION_CLIENTE']
            .astype(str)  # Convertir a string
            .str.strip()  # Eliminar espacios al principio y al final
            .str.replace(r'\s+', '', regex=True)  # Eliminar espacios adicionales entre caracteres
        )

        columnas = df_resultado.columns.tolist()

        # Encontramos la posición de la columna "Producto"
        posicion_producto = columnas.index('PRODUCTO')

        # Movemos "Categoría" antes de "Producto"
        columnas.insert(posicion_producto, columnas.pop(columnas.index('TIPO DE CLIENTE')))

        # Reorganizamos el DataFrame
        df_resultado = df_resultado[columnas]

        # Rellenar los valores NaN en "Categoría" cuando EQUIPO_VENTAS sea "Shopify"
        df_resultado.loc[df_resultado['EQUIPO_VENTAS'] == 'Shopify', 'TIPO DE CLIENTE'] = 'SHOPIFY'
        # Rellenar los valores NaN en "Categoría" cuando EQUIPO_VENTAS sea "Shopify"
        df_resultado.loc[df_resultado['EQUIPO_VENTAS'] == 'Punto de venta', 'TIPO DE CLIENTE'] = 'CALL CENTER'

        cliente_call_center = df_resultado[df_resultado['TIPO DE CLIENTE']=='CLIENTE']
        # Reemplazar "CLIENTE" por "CALL CENTER" en la columna "CATEGORÍA"
        df_resultado.loc[df_resultado['TIPO DE CLIENTE']=='CLIENTE', 'TIPO DE CLIENTE'] = 'CALL CENTER'
        # df_resultado[df_resultado['CATEGORÍA'].isna()].to_excel(r"C:\Users\Dataa\Desktop\ventas_sin_categoria.xlsx")

        df_resultado.loc[~df_resultado['PAIS'].isin(['CO', 'Desconocido']), 'TIPO DE CLIENTE'] = df_resultado['PAIS']


        # Rellenar los valores vacíos en "Categoría" con "Call center"
        df_resultado['TIPO DE CLIENTE'] =df_resultado['TIPO DE CLIENTE'].fillna('CALL CENTER')   ### REVISAR


        df_resultado= df_resultado.rename(columns={'TIPO DE CLIENTE':'CATEGORÍA'})

        # Renombra las categorías

        categorias_renombrar = {
            'Catalogo': 'CATÁLOGO',
            'Distribuidor': 'DISTRIBUIDOR',
            'Empleado': 'EMPLEADO',
            'FARMACIAS': 'FARMACIA',
            'HOLE COSMETICS':'HOLE COSMETICS SAS',
            'Surticosmeticos': 'SURTICOSMETICOS'
        }

        df_resultado['CATEGORÍA'] = df_resultado['CATEGORÍA'].replace(categorias_renombrar)

        return  {'Base':df_resultado,
            'nombre_archivo':nombre_archivo,
            'facturas_afectadas':facturas_afectadas,
            'errores':etiqueta_mayorista,
            # 'asesores_sin_categoria':asesores_sin_categoria,
            'cliente_call_center':cliente_call_center
            }


    def explosion_ventas(self,ventas: Optional[bool] = None, kits: Optional[bool] = None, df_ventas=None, df_kits=None, iva: Optional[bool] = None):
        """
        Realiza la explosión de ventas para actualizar el BI.

        Descompone los kits en productos individuales, calcula cantidades e ingresos,
        y genera tablas dinámicas por producto, mes y origen (kit/individual).

    
        """
        if ventas:
            df_ventas = self.cargar_archivo()
        else:
            if df_ventas is None:
                raise ValueError("Debe proporcionar el DataFrame de ventas si 'ventas' es False.")
        if kits:
            df_kits = self.cargar_archivo()
        else:
            if df_kits is None:
                raise ValueError("Debe proporcionar el DataFrame de kits si 'kits' es False.")
    
        # Verifica si falta incluir kit en el archivo kits.xlsx
        df_explosion_prueba = pd.merge(df_ventas, df_kits, left_on="PRODUCTO", right_on="KIT", indicator=True, how='left')

        productos_con_kit = [
            producto for producto in df_explosion_prueba[df_explosion_prueba['_merge']=='left_only']['PRODUCTO_x'].unique()
            if 'KIT' in str(producto)
        ]
        print(f"Productos con 'KIT' sin correspondencia en df_kits: {productos_con_kit}")


        # Unir df_ventas con df_kits para explotar los kits
        df_explosion = pd.merge(df_ventas, df_kits, left_on="PRODUCTO", right_on="KIT")

        # Agregar una columna para indicar el origen (kit o individual)
        df_explosion["ORIGEN"] = "KIT"

        # Calcular las cantidades de productos
        df_explosion["CANTIDAD_PRODUCTO"] = df_explosion["CANTIDAD"]


        # Calcular el valor por producto en los kits
        # df_explosion["VALOR_POR_PRODUCTO"] = df_explosion["TOTAL($)"] / df_explosion.groupby("KIT")["PRODUCTO_x"].transform("count")
        conteo_facturas = df_explosion.groupby(['PRODUCTO_x','NUMERO_FACTURA'])['NUMERO_FACTURA'].transform('count')
        if iva:
            df_explosion["VALOR_POR_PRODUCTO"] = df_explosion['TOTAL_CON_IMPUESTOS'] / conteo_facturas
        else:
            df_explosion["VALOR_POR_PRODUCTO"] = df_explosion['TOTAL($)'] / conteo_facturas

        # Filtrar productos individuales
        df_ventas_individuales = df_ventas[~df_ventas["PRODUCTO"].str.startswith(("[PCNKIT","[TNGKIT","[B8KIT"))].reset_index(drop=True)
        df_ventas_individuales["ORIGEN"] = "INDIVIDUAL"

        # Calcular las cantidades de productos
        df_ventas_individuales["CANTIDAD_PRODUCTO"] = df_ventas_individuales["CANTIDAD"]
        if iva:
            df_ventas_individuales["VALOR_POR_PRODUCTO"] = df_ventas_individuales['TOTAL_CON_IMPUESTOS'] 
        else:
            df_ventas_individuales["VALOR_POR_PRODUCTO"] = df_ventas_individuales['TOTAL($)']   # Suponiendo que el IVA es del 19%

        df_ventas_individuales['PRODUCTO_y'] = df_ventas_individuales['PRODUCTO'] 

        df_final_completo = pd.concat([df_explosion, df_ventas_individuales], ignore_index=True)

        compras_producto_cliente = (
            df_final_completo[['CLIENTE', 'PRODUCTO_y', 'NUMERO_FACTURA']]
            .drop_duplicates()
            .groupby(['CLIENTE', 'PRODUCTO_y'])
            .agg(Veces_Compra=('NUMERO_FACTURA', 'count'))
            .sort_values(by='Veces_Compra', ascending=False)
        ).reset_index()



        ventas_final = df_final_completo.groupby(['CLIENTE', 'PRODUCTO_y','ORIGEN']).agg(
            PRODUCTO=('PRODUCTO_y', 'first'),
            PRIMERA_COMPRA=('FECHA_FACTURA', 'min'),
            TOTAL_COMPRAS=('NUMERO_FACTURA', 'nunique'),
            ULTIMA_COMPRA=('FECHA_FACTURA', 'max'),
            TELEFONO=('TELEFONO', 'first'),
            TOTAL_CANTIDAD=('CANTIDAD', 'sum')
        ).reset_index()
        ventas_final = ventas_final.merge(compras_producto_cliente, how='left', on=['CLIENTE', 'PRODUCTO_y'])


        df_final_completo['PRODUCTO_x'] = df_final_completo['PRODUCTO_x'].fillna(df_final_completo['PRODUCTO_y'])

        df_final_completo = df_final_completo.rename(columns={'TOTAL($)':'TOTAL($)_ORI', 'VALOR_POR_PRODUCTO':'TOTAL($)',
                                                              'CANTIDAD':'CANTIDAD_ORI','CANTIDAD_PRODUCTO':'CANTIDAD',
                                                              'PRODUCTO':'PRODUCTO_Z', 'PRODUCTO_y':'PRODUCTO'
                                                              })

        return {'base':df_final_completo,
                'Agrupado_cliente':ventas_final}
    
    def pipeline_bi(self, iva: Optional[bool] = None):
        """
        Ejecuta el pipeline completo para procesar y transformar la base de ventas,
        consolidar datos, y realizar la explosión de ventas para el BI.

        Returns:
            dict: Diccionario con las bases procesadas, base limpia y explosión de ventas.
        """
        # porcesar base de ventas y notas credito
        ventas_procesadas = self.transformar_base()
        ruta = self.validar_ruta()

        ruta_clean = ruta / 'CLEAN DATA' 


        ruta2 = Path(ventas_procesadas['nombre_archivo'])
        ruta_carpeta = ruta_clean / ruta2
        ruta_errores = ruta / 'file' / 'ventas_sin_categoria.csv'
        ruta_padres = ruta / 'data' / 'clientes_padres.xlsx'
        ruta_bgta= ruta / 'data' / 'Base_bogota.xlsx'
        ruta_zonas= ruta / 'data' / 'zonas.xlsx'
        ruta_zonas_cundi = ruta / 'data' / 'zonas_cundinamarca.xlsx'
        ruta_kits = ruta / 'data' / 'kits.xlsx'
        df_kits = pd.read_excel(ruta_kits)
        df_padres = pd.read_excel(ruta_padres)
        df_bogota= pd.read_excel(ruta_bgta)
        df_bogota = df_bogota.drop_duplicates(subset='DOCUMENTO')
        df_bogota['DOCUMENTO'] = df_bogota['DOCUMENTO'].astype(int)
        zonas = pd.read_excel(ruta_zonas)
        cundinamarca = pd.read_excel(ruta_zonas_cundi)
        ventas_procesadas['Base'] =  ventas_procesadas['Base'].merge(zonas, on=['DEPARTAMENTO', 'CATEGORÍA'], how='left')\
                        .merge(cundinamarca, left_on=['DEPARTAMENTO','CIUDAD_CORREGIDA', 'CATEGORÍA'], right_on=['DEPARTAMENTO','CIUDAD', 'CATEGORÍA'], how='left')


        ventas_procesadas['Base']['IDENTIFICACION_CLIENTE'] = pd.to_numeric(
            ventas_procesadas['Base']['IDENTIFICACION_CLIENTE'], errors='coerce'
        ).astype('float')

        df_bogota['DOCUMENTO'] = pd.to_numeric(
            df_bogota['DOCUMENTO'], errors='coerce'
        ).astype('float')


        ventas_procesadas['Base'].loc[
            (ventas_procesadas['Base']['DEPARTAMENTO'] == 'Cundinamarca (CO)') &
            (ventas_procesadas['Base']['CATEGORÍA'] == 'MAYORISTA NV') &
            (ventas_procesadas['Base']['zona'] == 'sin zona') &
            (ventas_procesadas['Base']['ZONA_CUNDINAMARCA'].notna()),
            'zona'
        ] = ventas_procesadas['Base']['ZONA_CUNDINAMARCA']

        ventas_procesadas['Base'] = ventas_procesadas['Base'].merge(df_bogota[['DOCUMENTO', 'CATEGORÍA', 'ZONA']], 
                                        left_on=['IDENTIFICACION_CLIENTE','CATEGORÍA'], right_on=['DOCUMENTO', 'CATEGORÍA'], how='left')

        ventas_procesadas['Base']['ZONA'] = ventas_procesadas['Base']['ZONA'].fillna(ventas_procesadas['Base']['zona'])

        ventas_procesadas['Base'] = ventas_procesadas['Base'].drop(columns=['zona', 'DOCUMENTO', 'ZONA_CUNDINAMARCA'])
        df_padres.drop_duplicates(subset='CLIENTE', inplace=True)
        ventas_procesadas['Base']['CLIENTE_ORI'] = ventas_procesadas['Base']['CLIENTE'] 
        df_padres = df_padres[['CLIENTE', 'CLIENTE PADRE']]
        ventas_procesadas['Base'] = ventas_procesadas['Base'].merge(df_padres, on='CLIENTE', how='left')
        ventas_procesadas['Base']['CLIENTE PADRE'] = ventas_procesadas['Base']['CLIENTE PADRE'].fillna(ventas_procesadas['Base']['CLIENTE'])
        ventas_procesadas['Base']['CLIENTE'] = ventas_procesadas['Base']['CLIENTE PADRE'] 
        ventas_procesadas['Base'].drop(columns=['CLIENTE PADRE'], inplace=True)

        try:
            ruta_clean.mkdir(parents=True, exist_ok=True)  # Crear la carpeta si no existe
            print(f"Carpeta '{ruta_clean}' creada o ya existe.")
            ventas_procesadas['Base'].to_csv(ruta_carpeta, index=False, sep=';', encoding='utf-8-sig', decimal=',')
            ruta_exploded = ruta / 'exploded_data'
            base_exploded = self.explosion_ventas(df_ventas=ventas_procesadas['Base'], df_kits=df_kits, iva=iva)
            ruta_exploded.mkdir(parents=True, exist_ok=True)  # Crear la carpeta si no existe
            print(f"Carpeta '{ruta_clean}' creada o ya existe.")
            base_exploded['base'].to_csv(ruta_exploded / f"exploded_{ruta2.name}", index=False, sep=';', encoding='utf-8-sig', decimal=',')
            with pd.ExcelWriter(ruta_errores, engine='openpyxl') as writer:
                ventas_procesadas['errores'].to_excel(writer, sheet_name='etiqueta a tipo', index=False)
                ventas_procesadas['cliente_call_center'].to_excel(writer, sheet_name='CLIENTE a CALL', index=False)
                # ventas_procesadas['asesores_sin_categoria'].to_excel(writer, sheet_name='Mayoristas sin categoria', index=False)
        except Exception as e:
            print(f"Error al crear la carpeta o guardar los archivos: {e}")
            


        # Consolidar ventas
        base_clean = self.consolidar_carpeta(ruta_carpeta=ruta_clean, extension='csv')
        ruta_base = ruta / 'file' / 'base_ventas.csv'
        import locale
        try:         
            # Intentamos usar el locale en español para obtener "ENERO", "FEBRERO", etc.
            locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
        except locale.Error:
            print("   - Advertencia: Locale 'es_ES.UTF-8' no disponible. Se usarán nombres de mes en inglés.")
            
        base_clean['FECHA_FACTURA'] = pd.to_datetime(base_clean['FECHA_FACTURA'])
        base_clean['MES'] = base_clean['FECHA_FACTURA'].dt.strftime('%B').str.upper()
        columnas_finales = [
                "Source.Name", "NUMERO_FACTURA", "FECHA_FACTURA", "AÑO", "MES", "DIA",
                "CLIENTE", "IDENTIFICACION_CLIENTE", "CATEGORÍA", "PRODUCTO", "CANTIDAD",
                "TOTAL", "TASA_CAMBIO", "TRM", "TOTAL($)", "TOTAL_CON_IMPUESTOS" "TELEFONO", "EMAIL", "PAIS",
                "CIUDAD", "CIUDAD_CORREGIDA", "DEPARTAMENTO", "EQUIPO_VENTAS", "REFERENCIA", "ZONA" , "CLIENTE_ORI"
            ]
            
        # Manejo defensivo por si la columna 'ASESOR COMERCIAL' no siempre existe
        if 'ASESOR COMERCIAL' in base_clean.columns:
            base_clean['ASESOR COMERCIAL'] = base_clean['ASESOR COMERCIAL'].astype(str)
            columnas_finales.append('ASESOR COMERCIAL')

        # Aseguramos que solo reordenamos las columnas que realmente existen en el DataFrame
        columnas_existentes = [col for col in columnas_finales if col in base_clean.columns]
        base_clean = base_clean[columnas_existentes]

        # Esta linea mantiene solo los pruductos comerciales
        base_clean = base_clean[base_clean['PRODUCTO'].str.startswith(('[PCN','[KD','[TNG','[B8'))]   ###### linea modificada 


        try:
            ruta_file = ruta / 'file' 
            ruta_file.mkdir(parents=True, exist_ok=True)  # Crear la carpeta si no existe
            print(f"Carpeta '{ruta_file}' creada o ya existe.")
            base_clean.to_csv(ruta_base, index=False, sep=';', encoding='utf-8-sig', decimal=',')
            
        except Exception as e:
            print(f"Error al crear la carpeta o guardar los archivos: {e}")

            
        # Consolidar ventas
        base_exploded = self.consolidar_carpeta(ruta_carpeta=ruta_exploded, extension='csv')
        ruta_base = ruta / 'file' / 'base_ventas_exploded.csv'
        import locale
        try:         
            # Intentamos usar el locale en español para obtener "ENERO", "FEBRERO", etc.
            locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
        except locale.Error:
            print("   - Advertencia: Locale 'es_ES.UTF-8' no disponible. Se usarán nombres de mes en inglés.")


        try:
            ruta_file = ruta / 'file' 
            ruta_file.mkdir(parents=True, exist_ok=True)  # Crear la carpeta si no existe
            print(f"Carpeta '{ruta_file}' creada o ya existe.")
            base_exploded.to_csv(ruta_base, index=False, sep=';', encoding='utf-8-sig', decimal=',')
            
        except Exception as e:
            print(f"Error al crear la carpeta o guardar los archivos: {e}")

            
        return {'ventas_procesadas':ventas_procesadas,
                'base_clean':base_clean,
                'explosion':base_exploded
                }

    def contabilidad(self):
        """
        Procese la información contable desde archivos Odoo, asigna niveles, centros de costo y conceptos,   
            Returns:   
                None: El resultado se guarda en un archivo CSV en la carpeta de contabilidad.
        """
        
        ruta = self.validar_ruta()
        ruta_contabilidad = ruta / 'data' / 'contabilidad'
        df_base = self.consolidar_carpeta(ruta_carpeta=ruta_contabilidad / 'odoo' )

        df_base = df_base.rename(columns={'Cuenta': 'Cuenta Origen'})
        df_base['Cuenta'] = df_base['Cuenta Origen'].str.split(' ', regex=True).str[0]
        df_base['Nombre cuenta'] = df_base['Cuenta Origen'].str.split(' ', regex=True).str[1:].apply(lambda x: ' '.join(x) if isinstance(x, list) else '')
        df_base['N1'] = df_base['Cuenta Origen'].astype(str).str[0]
        df_base['N2'] = df_base['Cuenta Origen'].astype(str).str[:2]
        df_base['N3'] = df_base['Cuenta Origen'].astype(str).str[:4]

        # Define la columna nivel
        df_base['Nivel']  =np.where(df_base['N2']=='41', 'Ingreso Operativo',
                np.where(df_base['N2']=='42', 'Otros ingresos',
                        np.where(df_base['N2']=='52', 'Gastos operacionales',
                                np.where(df_base['N2']=='53', 'Gastos No Operacionales',
                                            np.where(df_base['N2']=='61', 'Costo directo de ventas', 
                                                    'Revisar'
                                            )
                                )
                        )
                )
        )

        df_niveles =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='niveles')
        df_concepto_unico =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='cuentas_concepto_uni')
        df_concepto =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='concepto_depende_cc')
        df_cc =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='CC')


        influencer =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='INFLUENCER')

        df_base['N3'] = df_base['N3'].astype(int)

        df_base['Cuenta'] = df_base['Cuenta'].astype(int)

        df_base_merge = df_base.merge(df_niveles, left_on='N3', right_on='cuenta', how='left').drop(columns='cuenta')

        def extraer_clave(diccionario_str):
            if pd.isna(diccionario_str):
                return None
            try:
                diccionario = json.loads(diccionario_str)
                return list(diccionario.keys())[0]
            except Exception:
                return None

        df_base_merge = df_base_merge.rename(columns={'Distribución analítica': 'Distribución analítica ori'})


        # Extraer el número del cc
        df_base_merge['Distribución analítica'] = df_base_merge['Distribución analítica ori'].apply(
            lambda x: list(json.loads(x).keys())[0] if pd.notna(x) else None
        )

        # Set las columnas previas
        columnas = df_base_merge.columns.tolist()
        map_tipo = dict(zip(df_cc['cc'].astype(str), df_cc['TIPO']))
        # Crea la nuevas columnas
        df_base_merge = pd.concat([
            df_base_merge,
            df_base_merge['Distribución analítica']
                .apply(lambda x: {
                    map_tipo.get(v.strip()): v.strip()
                    for v in str(x).split(',')
                    if map_tipo.get(v.strip())
                } if pd.notna(x) else {})
                .apply(pd.Series)
        ], axis=1)


        # Ajustes manuales de asignación de centro de costo y concepto
        df_base_merge['N1'] = df_base_merge['N1'].astype(str)
        df_base_merge['N2'] = df_base_merge['N2'].astype(str)
        df_base_merge['N3'] = df_base_merge['N3'].astype(str)


        df_base_merge.loc[
            (df_base_merge['N3'] == '4135'),
            'Centro de costos', 
        ] = '6'

        df_base_merge.loc[
            (df_base_merge['N3'] == '4175') & 
            (df_base_merge['Diario']!="Facturas de cliente Cali"),
            'Centro de costos', 
        ] = '6'

        df_base_merge.loc[
            (df_base_merge['N1'] == '6') & (df_base_merge['Distribución analítica ori'].isna()),# revisar ##########
            'Centro de costos'
        ] =  '6'

        df_base_merge.loc[
            (df_base_merge['N2'] == '42') & (df_base_merge['Distribución analítica ori'].isna()),
            'Centro de costos'
        ] = '6'

        df_base_merge.loc[(df_base_merge['Centro de costos'].isna()) & 
                    (df_base_merge['Número'].str.startswith('BNK')) &
                        (df_base_merge['Cuenta Origen'].isin(['530515001 COMISIONES','530505002 GRAVAMEN CUATRO POR MIL', '530505001 CUOTA DE MANEJO']))
                    , 'Centro de costos'
                    ] = '7'

        df_base_merge.loc[(df_base_merge['Centro de costos'].isna()) & 
                    (df_base_merge['Número'].str.startswith('BNK')) &
                        (df_base_merge['Cuenta Origen'].isin(['539595001 AJUSTE A MILES']))
                    , 'Centro de costos'
                    ] = '6' 

        df_base_merge.loc[(df_base_merge['Centro de costos'].isna()) & 
                    (df_base_merge['Número'].str.startswith('STJ')) &
                    (~df_base_merge['Contacto'].isin(influencer['Contacto'].unique().tolist()))
                    , 'Centro de costos'
                    ] = '6'  # validar si es clientre cc ==comercial  o infulerce cc== marketing ==

        df_base_merge.loc[(df_base_merge['Centro de costos'].isna()) & 
                    (df_base_merge['Número'].str.startswith('STJ')) &
                    (df_base_merge['Contacto'].isin(influencer['Contacto'].unique().tolist()))
                    , 'Centro de costos'
                    ] = '4'  # validar si es clientre cc ==comercial  o infulerce cc== marketing ==

        # Reemplaza los valores de las nuevas columnas
        cols_nuevas = set(df_base_merge.columns) - set(columnas)

        map_nombre = dict(
            zip(df_cc['cc'].astype(str), df_cc['Nombre Cencosto'])
        )


        for col in cols_nuevas:
            df_base_merge[col] = df_base_merge[col].map(map_nombre)

        df_base_merge['Nombre Cencosto'] = df_base_merge['Centro de costos']
        df_cc = df_cc[['Nombre Cencosto', 'Origen']].drop_duplicates()

            # df_base_merge[df_base_merge['Distribución analítica']
            # .fillna('0').str.contains('5,')]

        # df_cc['cc'] = df_cc['cc'].astype(str)

        df_base_merge = df_base_merge.merge(df_cc[['Nombre Cencosto', 'Origen' ]],
                                            left_on='Nombre Cencosto', right_on='Nombre Cencosto', how='left')





        df_base_merge = df_base_merge.merge(df_concepto_unico, on='Cuenta', how='left')


        df_concepto['Nombre Cencosto'] = df_concepto['Nombre Cencosto'].str.upper().str.strip()

        df_base_merge['Nombre Cencosto'] = df_base_merge['Nombre Cencosto'].str.upper().str.strip()


        df_base_merge = df_base_merge.merge(df_concepto, on=['Cuenta','Nombre Cencosto' ], how='left')
        # Crea la columna conceto con base la los coceptos unicos y los que necesitan cc


        df_base_merge['Concepto_uni'] = df_base_merge['Concepto_uni'].fillna('Sin datos')
        df_base_merge['Concepto_cc'] = df_base_merge['Concepto_cc'].fillna('Sin datos')

        # df_base_merge['Concepto'] = np.where(df_base_merge['Concepto_uni'].isna(), df_base_merge['Concepto_cc'], df_base_merge['Concepto_uni'])
        df_base_merge = df_base_merge.reset_index(drop=True)
        df_base_merge['Concepto'] = np.where(
            df_base_merge['Concepto_uni']=='Sin datos',
            df_base_merge['Concepto_cc'],
            df_base_merge['Concepto_uni']
        )
        # Verifica las cuentas que no tienen concepto
        df_cuentas = df_base_merge[(df_base_merge['Concepto']=="Sin datos")&(df_base_merge['Nombre Cencosto'].notna())][['Cuenta','Nombre Cencosto', 'Nombre cuenta', 'Distribución analítica']]
        df_cuentas = df_cuentas.drop_duplicates(subset=['Cuenta', 'Nombre Cencosto',], keep='first')


        return df_base_merge, df_cuentas


    def archivos_contabilidad(self):
        """
        Crea y consolida los archivos procesados por odoo

        """
        ruta = self.validar_ruta()
        ruta_contabilidad = ruta / 'data' / 'contabilidad'

        df_base_merge, df_cuentas = self.contabilidad()

        max_date = df_base_merge['Fecha'].max()
        min_date = df_base_merge['Fecha'].min()
        min_date.strftime('%d-%m-%Y')
        ruta_base = ruta_contabilidad / 'base' / f'base_{min_date.strftime('%d-%m-%Y')}_{max_date.strftime('%d-%m-%Y')}.csv'
        df_base_merge.to_csv(ruta_base, sep=";", index=False, encoding='utf-8', decimal=',')


        centros_no_re = df_base_merge[(df_base_merge['Centro de costos'].isna())&
                    (~df_base_merge['Distribución analítica'].isna())
                    ][['Distribución analítica ori','Distribución analítica', 'Centro de costos']].drop_duplicates()
        # Centros de costo mal clasificados
        cc_corregir = df_base_merge[df_base_merge['Distribución analítica ori'].fillna('').str.count(':')>1]

        # Genera el archivo de los casos sin centro de costos
        sin_cc = df_base_merge[(df_base_merge['Nombre Cencosto'].isna() )]
        sin_cc.to_excel(ruta_contabilidad / 'sin_cc.xlsx', index=False)


        # Genera el archivo con los errores
        with pd.ExcelWriter(ruta_contabilidad / 'correciones.xlsx', engine='openpyxl') as writer:
            sin_cc.to_excel(writer, index=False, sheet_name='Sin CC')
            cc_corregir.to_excel(writer, index=False, sheet_name='Corregir CC')
            centros_no_re.to_excel(writer, index=False, sheet_name='CC_no_registrados')
            df_cuentas.to_excel(writer, index=False, sheet_name='Cuentas sin concep')


        # Crea un archivo para cada persona de contabilidad
        digitadores = sin_cc['Creado por'].unique()
        ruta_errores = ruta_contabilidad / 'correcciones'
        dicc = {}
        for i in digitadores:
            base = sin_cc[sin_cc['Creado por']==i]
            base.to_csv(ruta_errores / f'{i}.csv', index=False, sep=';', decimal=',', encoding='utf-8')
            dicc[i] = f'{i}.csv'

        df_base_consol =  self.consolidar_carpeta(extension='csv', encoding='utf-8', sep=';', decimal=',', ruta_carpeta= ruta_contabilidad / 'base')


        df_base_consol = df_base_consol.loc[:, ~df_base_consol.columns.str.contains('^Unnamed')]

        df_base_consol.to_csv(ruta_contabilidad / 'base_consolidada.csv', encoding='utf-8', sep=';', decimal=',', index=False)

        return df_base_consol, dicc
        
    def informe_diario_mayoristas(
            self,
            ruta_carpeta: Optional[str] = None,
            extension: str = 'csv',
            producto_pen: Optional[List[str]] = None,
            ruta_presupuesto: Optional[str] = None,
            clientes: Optional[Dict[str, List[Dict[str, str]]]] = None,
        ):
        """
        PENDIENTE
        
        """
 

        if ruta_carpeta:
            ruta = Path(ruta_carpeta)
        else:
            ruta = self.validar_ruta() / 'CLEAN DATA'

        df_ventas = self.consolidar_carpeta(
            ruta_carpeta=ruta,
            extension=extension
        )

        df_ventas['FECHA_FACTURA']=pd.to_datetime(df_ventas['FECHA_FACTURA'])

        # Asignamos zona Distribuidor
        df_ventas.loc[df_ventas['CATEGORÍA']=='DISTRIBUIDOR', 'ZONA'] = 'DISTRIBUIDOR'
        # revisar logica de ultimos 6 meses

      
        # Flatten dict
        map_cliente_zona = {
            cliente: zona
            for lista in clientes.values()
            for d in lista
            for cliente, zona in d.items()
        }

        # Asignar zona vectorizado
        df_ventas['ZONA'] = (
            df_ventas['CLIENTE']
            .map(map_cliente_zona)
            .fillna(df_ventas['ZONA'])
        )

        ventas_filtradas = df_ventas[(df_ventas['ZONA'].notna())&(df_ventas['ZONA']!="sin zona")]

        productos_sin_kit = df_ventas.loc[
            ~df_ventas['PRODUCTO'].str.contains('KIT|COSMETIQUERA|GORRITO|BAG', case=False, na=False),
            'PRODUCTO'
        ].unique().tolist()

        co_holidays = holidays.Colombia()
        hoy = pd.Timestamp.now().normalize()

        mes_actual = hoy.replace(day=1)
        ayer = hoy - pd.Timedelta(days=2)

        mes_anterior = mes_actual - pd.DateOffset(days=1)
        meses_prev = (mes_anterior - pd.DateOffset(months=5)).replace(day=1)

        # Ventas meses anteriores
        df_meses_anteriores = ventas_filtradas.loc[
            ventas_filtradas['FECHA_FACTURA'].between(meses_prev, mes_anterior)
        ]
        # Clientes activos últimos 5 meses
        clientes_activos = (
            df_meses_anteriores[['CLIENTE', 'CATEGORÍA', 'ZONA',]]
            .drop_duplicates(ignore_index=True)
        )


        # Ventas del mes actual
        df_mes_actual = df_ventas.loc[(df_ventas['FECHA_FACTURA'] >= mes_actual)&(df_ventas['ZONA']!='sin zona')&(df_ventas['ZONA'].notna())]
        mes_actual_df = df_mes_actual.copy()

        # Ajustar "ayer" si cae en fin de semana
        while ayer.dayofweek >= 5 or ayer.date() in co_holidays:
            ayer -= pd.Timedelta(days=1)
        # Datos agrupados por zona
        clientes_mes_actual = df_mes_actual.groupby(['CLIENTE', 'ZONA', 'CATEGORÍA'	])['TOTAL($)'].sum().reset_index()
        clientes_mes_actual_ayer = df_mes_actual[df_mes_actual['FECHA_FACTURA']<=ayer].groupby(['CLIENTE', 'ZONA','CATEGORÍA'])['TOTAL($)'].sum().reset_index()



        clientes_activos = clientes_activos.merge(clientes_mes_actual, on=['CLIENTE', 'ZONA'], how='left', indicator=True)


        df_ventas['TOTAL($)'] = df_ventas['TOTAL($)'].astype(int)


        cobertura_hoy = (
            clientes_activos.assign(compro=lambda x: x['_merge'] == 'both')
            .groupby('ZONA')
            .agg(
                total_clientes=('CLIENTE', 'nunique'),
                clientes_compraron=('compro', 'sum'),
                ventas=('TOTAL($)', 'sum')
            )
            .assign(
                porcentaje_cobertura=lambda x: (x['clientes_compraron'] / x['total_clientes'])*100
            )
        ).reset_index()

        clientes_activos.rename(columns={'_merge': 'hoy'}, inplace=True)
        clientes_activos = clientes_activos.merge(clientes_mes_actual_ayer, on=['CLIENTE', 'ZONA'], how='left', indicator=True)


        cobertura_ayer = (
            clientes_activos.assign(compro=lambda x: x['_merge'] == 'both')
            .groupby('ZONA')
            .agg(
                total_clientes=('CLIENTE', 'nunique'),
                clientes_compraron=('compro', 'sum'),
                ventas=('TOTAL($)_y', 'sum')
            )
            .assign(
                porcentaje_cobertura=lambda x: (x['clientes_compraron'] / x['total_clientes'])*100
            )
        ).reset_index()
        cobertura_hoy['sin compra'] = cobertura_hoy['total_clientes'] - cobertura_hoy['clientes_compraron']
        cobertura_ayer['sin compra'] = cobertura_ayer['total_clientes'] - cobertura_ayer['clientes_compraron']

        df_pareto = (
            df_meses_anteriores
            .groupby(['ZONA', 'CLIENTE'])['TOTAL($)']
            .sum()
            .reset_index()
        )

        df_pareto = df_pareto.sort_values(['ZONA', 'TOTAL($)'], ascending=[True, False])

        # 2. Generar Ranking
        df_pareto['rank_zona'] = df_pareto.groupby('ZONA')['TOTAL($)'].rank(ascending=False, method='min').astype(int)

        df_pareto['venta_acum_zona'] = df_pareto.groupby('ZONA')['TOTAL($)'].cumsum()
        df_pareto['total_zona'] = df_pareto.groupby('ZONA')['TOTAL($)'].transform('sum')

        df_pareto['pct_acum_zona'] = df_pareto['venta_acum_zona'] / df_pareto['total_zona']

        df_pareto['categoria_pareto_zona'] = df_pareto['pct_acum_zona'].apply(
            lambda x: 'A' if x <= 0.8 else 'B'
        )

        df_pareto = df_pareto.merge(clientes_mes_actual, on=['CLIENTE','ZONA'], indicator=True, how='left', suffixes=('_hist', '_actual'))
        # Ordenamos por importancia histórica y tomamos los 5 mejores por zona
        top_5_fugados_por_zona = (
            df_pareto[df_pareto['_merge']=='left_only']
            .sort_values(['ZONA', 'TOTAL($)_hist'], ascending=[True, False])
            .groupby('ZONA')
            .head(5)
        )

        # Limpiamos las columnas para que el reporte sea legible
        reporte_critico = top_5_fugados_por_zona[['ZONA', 'CLIENTE', 'TOTAL($)_hist', 'rank_zona']]


        # Penetracion por productos especificos
        if producto_pen:
            productos_analisis = producto_pen
        else:
            productos_analisis = [ '[PCN32] SHAMPOO CONTROL CASPA',
            '[PCN33] TONICO CONTROL CASPA']

        clientes_producto_analisis = df_mes_actual[df_mes_actual['PRODUCTO'].isin(productos_analisis)][['CLIENTE', 'ZONA']].drop_duplicates().reset_index(drop=True)
        resultado = clientes_mes_actual.merge(
            clientes_producto_analisis[['CLIENTE', 'ZONA']],
            on=['CLIENTE', 'ZONA'],
            how='left',
            indicator=True
        )

        no_estan = resultado[resultado['_merge'] == 'left_only']


        no_estan = no_estan.shape[0]
        resultado_agru = resultado.groupby( 'ZONA')['CLIENTE'].count().reset_index()
        no_esta_agru = resultado[resultado['_merge'] == 'left_only'].groupby( 'ZONA')['CLIENTE'].count().reset_index()


        penetracion_producto = resultado_agru.merge(no_esta_agru, on='ZONA', how='left', suffixes=('_compraron', '_sin_compra'))


        penetracion_producto['CLIENTE COMPRARON PRODUCTO'] = penetracion_producto['CLIENTE_compraron'] - penetracion_producto['CLIENTE_sin_compra']
        penetracion_producto['PENETRACION'] =( penetracion_producto['CLIENTE COMPRARON PRODUCTO']  /penetracion_producto['CLIENTE_compraron'])*100
        penetracion_producto['PENETRACION'] = penetracion_producto['PENETRACION'].round(1)

        zonas = penetracion_producto['ZONA']



        penetracion_producto = penetracion_producto.merge(cobertura_hoy[['ZONA','total_clientes' ]], on='ZONA', how='left')

        penetracion_producto['PENETRACION'] = ((penetracion_producto['CLIENTE COMPRARON PRODUCTO'] / penetracion_producto['total_clientes'])*100).round(1)

        penetracion_producto['CLIENTE_sin_compra'] = penetracion_producto['total_clientes'] - penetracion_producto['CLIENTE COMPRARON PRODUCTO']
        cobertura_hoy[cobertura_hoy['ZONA']=='ANTIOQUIA']['porcentaje_cobertura'].round(1).to_list()[0]


        reporte_critico = reporte_critico.rename(columns={'rank_zona': 'Ranking'})


        # Presupuesto

        if ruta_presupuesto:
            presupuesto = pd.read_excel(ruta_presupuesto)
        else:
            ruta = self.validar_ruta()
            ruta_presu = ruta / 'data' / 'PRESUPUESTO GENERAL.xlsx' # modificar
            presupuesto = pd.read_excel(ruta_presu)


        # Asignar zona vectorizado
        presupuesto['ZONA'] = (
            presupuesto['CLIENTE']
            .map(map_cliente_zona)
            .fillna(presupuesto['ZONA'])
        )


        presupuesto['MES'] = presupuesto['FECHA'].dt.month
        presupuesto = presupuesto[presupuesto['MES']==hoy.month]

        presupuesto = presupuesto.groupby(['FECHA',   'ZONA' ])['PRESUPUESTO'].sum().reset_index()

        # Ventas mes actual
        filtro= mes_actual_df.groupby('ZONA')['TOTAL($)'].sum().reset_index()
        cobertura_hoy = cobertura_hoy.merge(filtro, on='ZONA', how='left').fillna(0).rename(columns={'ventas': 'ventas clientes activos', 'TOTAL($)':'ventas'})
        # Ventas mes actual
        filtro= mes_actual_df[mes_actual_df['FECHA_FACTURA']<=ayer].groupby( 'ZONA')['TOTAL($)'].sum().reset_index()
        cobertura_ayer = cobertura_ayer.merge(filtro, on='ZONA', how='left').fillna(0).rename(columns={'ventas': 'ventas clientes activos', 'TOTAL($)':'ventas'})

        cobertura_ayer = cobertura_ayer.merge(presupuesto[['ZONA', 'PRESUPUESTO']], how='left')
        cobertura_ayer['falta presu'] = cobertura_ayer['PRESUPUESTO'] - cobertura_ayer['ventas']
        cobertura_ayer['cumplimiento%'] = ((cobertura_ayer['ventas'] / cobertura_ayer['PRESUPUESTO'])*100).round(1)
        cobertura_hoy = cobertura_hoy.merge(presupuesto[['ZONA', 'PRESUPUESTO']], how='left')
        cobertura_hoy['falta presu'] = cobertura_hoy['PRESUPUESTO'] - cobertura_hoy['ventas']
        cobertura_hoy['cumplimiento%'] = ((cobertura_hoy['ventas'] / cobertura_hoy['PRESUPUESTO'])*100).round(1)


        # Separa los clietes de las zonas
        cobertura_clientes = cobertura_hoy[cobertura_hoy['ZONA'].isin(map_cliente_zona.values())].reset_index(drop=True)
        cobertura_hoy = cobertura_hoy[~cobertura_hoy['ZONA'].isin(map_cliente_zona.values())].reset_index(drop=True)
        cobertura_ayer = cobertura_ayer[~cobertura_ayer['ZONA'].isin(map_cliente_zona.values())].reset_index(drop=True)
        penetracion_producto = penetracion_producto[~penetracion_producto['ZONA'].isin(map_cliente_zona.values())].reset_index(drop=True)
        # Quita lo que no son zonas
        zonas = (set(zonas) - set(map_cliente_zona.values()))
        # Penetracion por clienta
        productos_por_cliente = (
            df_mes_actual
            .groupby(['CLIENTE', 'ZONA'])['PRODUCTO']
            .nunique()
            .reset_index()
            .rename(columns={'PRODUCTO': 'productos_comprados'})
        )

        total_portafolio = len(productos_sin_kit)
        productos_por_cliente['penetracion'] = (
            productos_por_cliente['productos_comprados'] / total_portafolio
        )

        cobertura_clientes = cobertura_clientes[['ZONA','PRESUPUESTO','ventas','cumplimiento%','falta presu']]


        map_zona_clientes = defaultdict(list)

        for cliente, zona in map_cliente_zona.items():
            map_zona_clientes[zona].append(cliente)


        # Asignar zona vectorizado
        cobertura_clientes['CLIENTE'] = cobertura_clientes['ZONA'].map(
            lambda z: map_zona_clientes.get(z, [''])[0])
        

        map_zona_correo = {}

        for correo, lista in clientes.items():
            for d in lista:
                for _, zona in d.items():
                    map_zona_correo[zona] = correo

        cobertura_clientes['RESPONSABLE'] = cobertura_clientes['ZONA'].map(map_zona_correo)

        cobertura_clientes = cobertura_clientes.merge(productos_por_cliente[['CLIENTE', 'productos_comprados', 'penetracion']], on='CLIENTE', how='left').fillna(0)
        responsables = cobertura_clientes['RESPONSABLE'].drop_duplicates().to_list()
        # Productos comercializados -2 por los pruductos de exportación
        cobertura_clientes['productos_pocion'] = df_ventas[
            ~df_ventas['PRODUCTO'].str.contains('KIT|COSMET', case=False, na=False)
        ]['PRODUCTO'].unique().shape[0]-2
        cobertura_clientes['RESPONSABLE'].unique()
        # Agregar fila totales

        totales = (
            cobertura_clientes
            .groupby('RESPONSABLE', as_index=False).agg({
            'PRESUPUESTO': 'sum',
            'ventas': 'sum',
            'falta presu': 'sum',
            'productos_comprados':'max',
            'productos_pocion':'max'

            })
        
        )
        totales['ZONA'] = 'TOTAL'
        totales['CLIENTE'] = 'TOTAL'
        totales['cumplimiento%'] = (
            totales['ventas'] / totales['PRESUPUESTO']
        ).replace([float('inf'), -float('inf')], 0).fillna(0) * 100

        totales['penetracion'] = (
            totales['productos_comprados'] / totales['productos_pocion']
        ).fillna(0)
        cobertura_clientes = pd.concat([cobertura_clientes, totales], ignore_index=True)

        # Agrega la fila final de Mayoristas

        def agregar_mayoristas(df: pd.DataFrame) -> pd.DataFrame:
            df = df.copy()


            base = df[df['ZONA'] != 'DISTRIBUIDOR']


            totales = base.drop(columns=['ZONA']).sum(numeric_only=True)

            total_clientes = totales['total_clientes']
            clientes_compraron = totales['clientes_compraron']

        
            porcentaje_cobertura = (clientes_compraron / total_clientes) * 100
            sin_compra = total_clientes - clientes_compraron
            cumplimiento = (totales['ventas'] / totales['PRESUPUESTO']) * 100


            fila_mayoristas = pd.DataFrame([{
                'ZONA': 'MAYORISTAS',
                'total_clientes': total_clientes,
                'clientes_compraron': clientes_compraron,
                'ventas clientes activos': totales['ventas clientes activos'],
                'porcentaje_cobertura': porcentaje_cobertura,
                'sin compra': sin_compra,
                'ventas': totales['ventas'],
                'PRESUPUESTO': totales['PRESUPUESTO'],
                'falta presu': totales['PRESUPUESTO'] - totales['ventas'],
                'cumplimiento%': cumplimiento
            }])


            df = pd.concat([df, fila_mayoristas], ignore_index=True)

            df['porcentaje_cobertura'] = df['porcentaje_cobertura'].round(1)
            df['cumplimiento%'] = df['cumplimiento%'].round(1)

            return df
        cobertura_hoy = agregar_mayoristas(cobertura_hoy)
        cobertura_ayer = agregar_mayoristas(cobertura_ayer)




        base = penetracion_producto[penetracion_producto['ZONA'] != 'DISTRIBUIDOR']


        total_clientes = base['total_clientes'].sum()
        clientes_compraron = base['CLIENTE_compraron'].sum()
        clientes_sin_compra = base['CLIENTE_sin_compra'].sum()
        clientes_producto = base['CLIENTE COMPRARON PRODUCTO'].sum()


        penetracion = (clientes_producto / total_clientes) * 100


        fila_mayoristas = pd.DataFrame([{
            'ZONA': 'MAYORISTAS',
            'CLIENTE_compraron': clientes_compraron,
            'CLIENTE_sin_compra': clientes_sin_compra,
            'CLIENTE COMPRARON PRODUCTO': clientes_producto,
            'PENETRACION': round(penetracion, 1),
            'total_clientes': total_clientes
        }])


        penetracion_producto = pd.concat([penetracion_producto, fila_mayoristas], ignore_index=True)
        
        # Crear informes por zona
        clientes_activos = clientes_activos[['CLIENTE', 'ZONA', 'CATEGORÍA_x', 'TOTAL($)_x']].rename(columns={'CATEGORÍA_x':'CATEGORÍA', 'TOTAL($)_x':'TOTAL($)'})
        clientes_activos['ESTADO'] = 'ACTIVO'
        clientes_mes_actual['ESTADO'] = 'MES ACTUAL'
        base_vendedores = pd.concat([clientes_activos, clientes_mes_actual], ignore_index=True,)\
        .drop_duplicates(subset=['CLIENTE', 'ZONA', 'CATEGORÍA', 'TOTAL($)'], keep='first')\
        .merge(clientes_producto_analisis, on=['CLIENTE', 'ZONA'], how='left', indicator=True)

        base_vendedores = base_vendedores.rename(columns={'_merge': 'PRODUCTO_ANALISIS'})
        base_vendedores['PRODUCTO_ANALISIS'] = base_vendedores['PRODUCTO_ANALISIS'].astype(str)

        base_vendedores.loc[
            base_vendedores['PRODUCTO_ANALISIS'] == 'both',
            'PRODUCTO_ANALISIS'
        ] = 'COMPRO PRODUCTO ANALISIS'

        base_vendedores.loc[
            base_vendedores['PRODUCTO_ANALISIS'] == 'left_only',
            'PRODUCTO_ANALISIS'
        ] = 'NO COMPRO PRODUCTO ANALISIS'

        informes_por_zona = {}

        # Generar informe por zona
        for zona in cobertura_hoy['ZONA'].to_list():
            penetracion_valor = f"{penetracion_producto[penetracion_producto['ZONA']==zona]['PENETRACION'].to_list()[0]}%"
            cobertura_valor = f"{cobertura_hoy[cobertura_hoy['ZONA']==zona]['porcentaje_cobertura'].round(1).to_list()[0]}"
            cobertura_valor_ayer = f"{cobertura_ayer[cobertura_ayer['ZONA']==zona]['porcentaje_cobertura'].round(1).to_list()[0]}"
            clientes_valor = f"{cobertura_hoy[cobertura_hoy['ZONA']==zona]['sin compra'].round(1).to_list()[0]}"
            clientes_valor_ayer = f"{cobertura_ayer[cobertura_ayer['ZONA']==zona]['sin compra'].round(1).to_list()[0]}"
            penetracion_valor_clientes = f"{int(penetracion_producto[penetracion_producto['ZONA']==zona]['CLIENTE_sin_compra'].to_list()[0])}"
            cumplimiento_hoy =   f"{cobertura_hoy[cobertura_hoy['ZONA']==zona]['cumplimiento%'].round(1).to_list()[0]}"
            cumplimiento_ayer =   f"{cobertura_ayer[cobertura_ayer['ZONA']==zona]['cumplimiento%'].round(1).to_list()[0]}"
            falta_hoy_valor =   float(f"{cobertura_hoy[cobertura_hoy['ZONA']==zona]['falta presu'].round(1).to_list()[0]}")
            falta_ayer_valor =   float(f"{cobertura_ayer[cobertura_ayer['ZONA']==zona]['falta presu'].round(1).to_list()[0]}")
            presupuesto= int(cobertura_hoy[cobertura_hoy['ZONA']==zona]['PRESUPUESTO'].to_list()[0])
            ventas = int(cobertura_hoy[cobertura_hoy['ZONA']==zona]['ventas'].to_list()[0])
            clientes_activos = int(cobertura_hoy[cobertura_hoy['ZONA']==zona]['total_clientes'].to_list()[0])
        
            # --- GENERAR SECCIÓN DE TABLA TOP SOLO SI NO ES MAYORISTA ---
            if zona != 'MAYORISTAS':
                df_top = (
                    reporte_critico[reporte_critico['ZONA'] == zona][['Ranking', 'CLIENTE']]
                    .sort_values('Ranking')
                )

                # Generar filas HTML con estilos alternados
                filas_html = ""
                for idx, row in df_top.iterrows():
                    # Alternar colores de fondo
                    bg_color = "#f8f9fa" if row['Ranking'] % 2 == 0 else "#ffffff"
                    
                    # Badge para el ranking
                    if row['Ranking'] <= 3:
                        badge_color = "#e74c3c"  # Rojo para top 3
                        badge_icon = "🔴"
                    elif row['Ranking'] <= 5:
                        badge_color = "#f39c12"  # Naranja para 4-5
                        badge_icon = "🟠"
                    else:
                        badge_color = "#95a5a6"  # Gris para el resto
                        badge_icon = "⚪"
                    
                    filas_html += f"""
                    <tr style="background-color: {bg_color}; transition: background-color 0.2s;">
                        <td style="padding: 14px 12px; border-bottom: 1px solid #e8e8e8; text-align: center; width: 80px;">
                            <span style="background-color: {badge_color}; color: white; padding: 6px 12px; border-radius: 20px; font-weight: bold; font-size: 13px; display: inline-block; min-width: 35px;">
                                {badge_icon} #{row['Ranking']}
                            </span>
                        </td>
                        <td style="padding: 14px 16px; border-bottom: 1px solid #e8e8e8; color: #2c3e50; font-size: 14px; font-weight: 500;">
                            {row['CLIENTE']}
                        </td>
                    </tr>
                    """
                
                # Construir tabla completa 
                tabla_html = f"""
                <div style="background: linear-gradient(to bottom, #ffffff, #f8f9fa); border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                    <table width="100%" style="border-collapse: collapse; font-family: 'Segoe UI', Arial, sans-serif;">
                        <thead>
                            <tr style="background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);">
                                <th style="padding: 16px 12px; color: #ffffff; text-align: center; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; border-right: 1px solid rgba(255,255,255,0.1);">
                                    Ranking
                                </th>
                                <th style="padding: 16px; color: #ffffff; text-align: left; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">
                                    Cliente
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {filas_html}
                        </tbody>
                    </table>
                </div>
                """
                
                # Sección completa de clientes críticos
                seccion_clientes_criticos = f"""
                    <tr>
                        <td style="padding: 0 20px 30px 20px;">
                            <div style="border-top: 1px solid #eee; padding-top: 20px;">
                                <h3 style="color: #1a5276; font-size: 16px; margin-bottom: 15px; text-align: left; display: flex; align-items: center;">
                                    <span style="background-color: #e74c3c; color: white; padding: 4px 8px; border-radius: 4px; margin-right: 10px; font-size: 12px;">CRÍTICO</span>
                                    CLIENTES TOP SIN COMPRA
                                </h3>
                                {tabla_html}
                            </div>
                        </td>
                    </tr>
                """
            else:
                seccion_clientes_criticos = ""  # No mostrar nada para MAYORISTA

            # HTML DEL CORREO COMPLETO
            cuerpo_correo = f"""
            <!DOCTYPE html>
            <html>
            <head><meta charset="UTF-8"></head>
            <body style="margin:0; padding:0; font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f7f9;">
                <table align="center" border="0" cellpadding="0" cellspacing="0" width="600" style="border-collapse: collapse; background-color: #ffffff; margin-top: 20px; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
                    <tr>
                        <td bgcolor="#1a5276" style="padding: 25px; text-align: center; color: #ffffff;">
                            <h1 style="margin: 0; font-size: 20px; letter-spacing: 1px;">INFORME DIARIO DE VENTAS {hoy.month_name(locale='es_ES').upper()}</h1>
                            <p style="margin: 5px 0 0 0; font-size: 13px; opacity: 0.9;">Resultado Acumulado al {hoy.date()} | <strong>ZONA {zona}</strong></p>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding: 20px;">
                            <table width="100%" border="0" cellspacing="0" cellpadding="5">
                                <tr>
                                    <td width="50%">
                                        <div style="background-color: #f8f9fa; border-left: 4px solid #3498db; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Cobertura</span><br>
                                            <span style="font-size: 18px; font-weight: bold; color: #2c3e50;">Hoy: {cobertura_valor}%</span><br>
                                            <span style="font-size: 12px; color: #95a5a6;">{ayer.date()}: {cobertura_valor_ayer}%</span>
                                        </div>
                                    </td>
                                    <td width="50%">
                                        <div style="background-color: #f8f9fa; border-left: 4px solid #e67e22; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Activos sin Compra</span><br>
                                            <span style="font-size: 18px; font-weight: bold; color: #2c3e50;">Hoy: {clientes_valor}</span><br>
                                            <span style="font-size: 12px; color: #95a5a6;">{ayer.date()}: {clientes_valor_ayer}</span>
                                        </div>
                                    </td>
                                </tr>
                                <tr><td colspan="2" style="height: 10px;"></td></tr>
                                <tr>
                                    <td width="50%">
                                        <div style="background-color: #ebf5fb; border-left: 4px solid #2ecc71; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Penetración {productos_analisis}</span><br>
                                            <span style="font-size: 22px; font-weight: bold; color: #27ae60;">{penetracion_valor}</span>
                                        </div>
                                    </td>
                                    <td width="50%">
                                        <div style="background-color: #ebf5fb; border-left: 4px solid #2ecc71; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Sin Compra ({productos_analisis})</span><br>
                                            <span style="font-size: 22px; font-weight: bold; color: #27ae60;">{penetracion_valor_clientes}/{clientes_activos} </span>
                                        </div>
                                    </td>
                                </tr>
                                <tr><td colspan="2" style="height: 10px;"></td></tr>
                                <tr>
                                    <td width="50%">
                                        <div style="background-color: #f8f9fa; border-left: 4px solid #3498db; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Cumplimiento %</span><br>
                                            <span style="font-size: 18px; font-weight: bold; color: #2c3e50;">Hoy: {cumplimiento_hoy}%</span><br>
                                            <span style="font-size: 12px; color: #95a5a6;">{ayer.date()}: {cumplimiento_ayer}%</span>
                                        </div>
                                    </td>
                                    <td width="50%">
                                        <div style="background-color: #f8f9fa; border-left: 4px solid #e67e22; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Millones Faltantes</span><br>
                                            <span style="font-size: 18px; font-weight: bold; color: #2c3e50;">Hoy: ${falta_hoy_valor:,.0f}</span><br>
                                            <span style="font-size: 12px; color: #95a5a6;">{ayer.date()}: ${falta_ayer_valor:,.0f}</span>
                                        </div>
                                    </td>
                                </tr>
                                <tr><td colspan="2" style="height: 10px;"></td></tr>
                                <tr>
                                    <td width="50%">
                                        <div style="background-color: #f8f9fa; border-left: 4px solid #3498db; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Ventas</span><br>
                                            <span style="font-size: 18px; font-weight: bold; color: #2c3e50;">${ventas:,.0f}</span><br>
                                        </div>
                                    </td>
                                    <td width="50%">
                                        <div style="background-color: #f8f9fa; border-left: 4px solid #e67e22; padding: 15px; border-radius: 4px;">
                                            <span style="font-size: 11px; color: #7f8c8d; text-transform: uppercase; font-weight: bold;">Presupuesto</span><br>
                                            <span style="font-size: 18px; font-weight: bold; color: #2c3e50;">${presupuesto:,.0f}</span><br>
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    {seccion_clientes_criticos}
                    <tr>
                        <td bgcolor="#f4f7f9" style="padding: 15px; text-align: center; font-size: 11px; color: #95a5a6; border-top: 1px solid #e0e0e0;">
                            Este es un reporte automático generado por el área de Analisis de datos.
                        </td>
                    </tr>
                </table>

            </body>
            </html>
            """
            # GUARDAR EL HTML EN EL DICCIONARIO
            informes_por_zona[zona] = cuerpo_correo

        # Informe cuentas clave por responsable    
        for i in responsables:

            filas_html = ""
            df = cobertura_clientes[cobertura_clientes['RESPONSABLE'] == i]

            for _, row in df.iterrows():

                cumpl_color = (
                    "#d5f5e3" if row['cumplimiento%'] >= 100
                    else "#fcf3cf" if row['cumplimiento%'] >= 80
                    else "#fadbd8"
                )

                falta_color = "#c0392b" if row['falta presu'] > 0 else "#1e8449"

                filas_html += f"""
                <tr style="transition: background-color 0.2s;">
                    <td style="padding:12px 16px;border-bottom:1px solid #e8e8e8;
                            font-weight:500;color:#2c3e50;font-size:13px;">
                        {row['ZONA']}
                    </td>

                    <td style="padding:12px 16px;border-bottom:1px solid #e8e8e8;
                            text-align:right;font-weight:600;color:#34495e;font-size:13px;">
                        ${row['PRESUPUESTO']:,.0f}
                    </td>

                    <td style="padding:12px 16px;border-bottom:1px solid #e8e8e8;
                            text-align:right;font-weight:600;color:#34495e;font-size:13px;">
                        ${row['ventas']:,.0f}
                    </td>

                    <td style="padding:10px 14px;border-bottom:1px solid #e8e8e8;
                            text-align:center;font-weight:700;
                            background-color:{cumpl_color};
                            color:#2c3e50;font-size:13px;
                            border-radius:4px;">
                        {row['cumplimiento%']:.1f}%
                    </td>

                    <td style="padding:12px 16px;border-bottom:1px solid #e8e8e8;
                            text-align:right;font-weight:700;
                            color:{falta_color};font-size:13px;">
                        ${row['falta presu']:,.0f}
                    </td>

                    <td style="padding:12px 16px;border-bottom:1px solid #e8e8e8;
                            text-align:center;font-weight:600;color:#34495e;font-size:13px;">
                        {int(row['productos_comprados'])}/{int(row['productos_pocion'])}
                    </td>
                </tr>
                """

            tabla_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
            </head>

            <body style="margin:0;padding:20px 0;background-color:#f4f6f7;font-family:'Segoe UI', -apple-system, system-ui, sans-serif;">

            <div style="
                max-width:900px;
                margin:0 auto;
                background:#ffffff;
                border-radius:8px;
                overflow:hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            ">

                <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">

                    <thead>
                        <tr>
                            <td colspan="6" bgcolor="#1a5276" style="padding: 25px; text-align: center; color: #ffffff;">
                                <h1 style="margin: 0; font-size: 20px; letter-spacing: 1px;">INFORME DIARIO DE VENTAS {hoy.month_name(locale='es_ES').upper()}</h1>
                                <p style="margin: 5px 0 0 0; font-size: 13px; opacity: 0.9;">Resultado Acumulado al {hoy.date()} | <strong>ZONA {i}</strong></p>
                            </td>
                        </tr>
                        <tr style="background: linear-gradient(135deg, #1a5276 0%, #2471a3 100%);">
                            <th style="padding:16px;color:#ffffff;text-align:left;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;width:28%;">
                                Cliente
                            </th>
                            <th style="padding:16px;color:#ffffff;text-align:right;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;width:16%;">
                                Presupuesto
                            </th>
                            <th style="padding:16px;color:#ffffff;text-align:right;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;width:16%;">
                                Ventas
                            </th>
                            <th style="padding:16px;color:#ffffff;text-align:center;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;width:13%;">
                                Cumpl. %
                            </th>
                            <th style="padding:16px;color:#ffffff;text-align:right;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;width:15%;">
                                Falta $
                            </th>
                            <th style="padding:16px;color:#ffffff;text-align:center;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;width:12%;">
                                Penetración
                            </th>
                        </tr>
                    </thead>

                    <tbody style="background-color:#ffffff;">
                        {filas_html}
                    </tbody>
                </table>

                <div style="background-color:#f8f9fa;padding:16px;text-align:center;border-top:1px solid #e0e0e0;">
                    <p style="margin:0;font-size:11px;color:#7f8c8d;line-height:1.6;">
                        Reporte automático generado por el Área de Análisis de Datos
                    </p>
                </div>
            </div>

            </body>
            </html>
            """
            informes_por_zona[i] = tabla_html   


   

        return {'Cuerpo_HTML':informes_por_zona, 'Base_Vendedores': base_vendedores}
    
    def informe_cartera(self, categorias: list) -> pd.DataFrame:
        """
        """
        # Base Cartera
        
        # Archivo CSV de Google Sheets
        url = "https://docs.google.com/spreadsheets/d/1uqGx-MkrUQR3znLq6HE2xiPts1RMv7P5GUQK4IJWdRQ/export?format=csv&gid=2087536586"

        df = pd.read_csv(url)

        ruta = self.validar_ruta()
        ruta_archivo = ruta / 'cartera' / 'Asiento contable (account.move).xlsx'
        # Archivo Odoo
        df_cartera = pd.read_excel(ruta_archivo)


        # Archivo base cartera
        df_cartera = df_cartera[df_cartera['Tipo de cliente'].isin(categorias)]

        ruta_base = ruta / 'data' / 'base_cartera.xlsx'

        df_base_responsable = pd.read_excel(ruta_base, sheet_name='Responsables')

        # df_cartera = df_cartera.merge(df_base_responsable, left_on='Tipo de cliente', right_on='TIPO CLIENTE', how='left' )
        
        df_base_responsable = pd.read_excel(ruta_base, sheet_name='Responsables')
        tipo_repetido = df_base_responsable[df_base_responsable['TIPO CLIENTE'].duplicated()]['TIPO CLIENTE'].unique()
        tipo_repetido
        responsables_tipo = df_base_responsable[~df_base_responsable['TIPO CLIENTE'].isin(tipo_repetido)]
        responsables_clientes = df_base_responsable[df_base_responsable['TIPO CLIENTE'].isin(tipo_repetido)]
        df_cartera = df_cartera.merge(responsables_tipo[['TIPO CLIENTE', 'RESPONSABLE', 'UBICACIÓN']], left_on='Tipo de cliente', right_on='TIPO CLIENTE', how='left' ).merge(responsables_clientes[[ 'CLIENTE', 'RESPONSABLE']], left_on='Nombre del contacto a mostrar en la factura', right_on='CLIENTE', how='left')
        df_cartera['RESPONSABLE'] = df_cartera['RESPONSABLE_x'].fillna(df_cartera['RESPONSABLE_y'])
        df_cartera['TIPO CLIENTE'] = df_cartera['TIPO CLIENTE'].fillna(df_cartera['Tipo de cliente'])
        responsable_default =df_base_responsable[df_base_responsable['TIPO CLIENTE'] == 'Default']['RESPONSABLE'].values[0]

        df_cartera['RESPONSABLE'] = df_cartera['RESPONSABLE'].fillna(responsable_default)


        df_cartera = df_cartera[
                (df_cartera['Fecha de factura'] >= '2025-01-01')&
                (df_cartera['Número'].str.startswith('F'))]



        responsables = df_base_responsable['RESPONSABLE'].unique().tolist()


        df_cartera = df_cartera[['Número', 'Nombre del contacto a mostrar en la factura', 'Fecha de factura', 'Fecha de vencimiento', 'Importe pendiente firmado', 'RESPONSABLE', 'TIPO CLIENTE']]
        df_cartera['Dias de credito'] = (pd.to_datetime(df_cartera['Fecha de vencimiento']) - pd.to_datetime(df_cartera['Fecha de factura'])).dt.days    
        # df_cartera = df_cartera[df_cartera['Dias de credito'] != 0]
        df_cartera['Dias de atraso'] = (pd.to_datetime('now') - pd.to_datetime(df_cartera['Fecha de vencimiento'])  ).dt.days    
        dias = df_cartera['Dias de atraso']

        conditions = [
            dias < -7,                         # Más de 7 días antes de vencer
            dias.between(-7, 0, inclusive='both'),  # Próximo a vencer
            dias.between(1, 10, inclusive='both'),  # Hasta 10 días vencido
            dias.between(11, 30, inclusive='both'),
            dias.between(31, 60, inclusive='both'),
            dias.between(61, 90, inclusive='both'),
            dias > 90
        ]

        choices = [
            'Corriente',
            'Proximo',
            'Corriente',
            '11_30',
            '31_60',
            '61_90',
            '90+'
        ]

        df_cartera['Rango Mora'] = np.select(conditions, choices, default='Sin clasificar')
        # Impervinculo a formulario de Google Forms para cada Factura
        df_cartera['Link_forms'] = df_cartera.apply( 
            lambda row: (f'=HIPERVINCULO("https://docs.google.com/forms/d/e/1FAIpQLSfGupw7MUupqTAZr61Qgk6UcEPuJfZsKci9yBaahwOTVfGQ-Q/viewform?usp=pp_url&entry.1478832904={row['Nombre del contacto a mostrar en la factura']}&entry.1421149375={row['Número']}";"Link")'
            if row['Rango Mora'] not in ['Corriente', 'Proximo'] else ''), 
            axis=1
        )

        df_cartera.sort_values(by=['Nombre del contacto a mostrar en la factura', 'Fecha de vencimiento'], ascending=True, inplace=True)

        df_cartera.rename(columns={'Nombre del contacto a mostrar en la factura': 'CLIENTE', 
                                'Fecha de factura': 'FECHA FACTURA', 'Fecha de vencimiento': 'FECHA VENCIMIENTO',
                                    'Importe pendiente firmado': 'IMPORTE PENDIENTE', 'RESPONSABLE': 'RESPONSABLE', 
                                    'Dias de credito': 'DIAS CREDITO', 'Dias de atraso': 'DIAS ATRASO', 
                                    'Rango Mora': 'RANGO MORA', 'Link_forms': 'LINK FORMS'}, inplace=True)


        # Genera los archivos CSV para cada responsable
        for i in responsables:
            if df_cartera['RESPONSABLE'].shape[0] > 1:
                df_cartera[df_cartera['RESPONSABLE'] == i].to_csv(ruta / 'cartera' / f'CARTERA_{i}.csv', index=False, encoding='utf-8-sig', sep=';', decimal=',')
            
        df_cartera['IMPORTE PENDIENTE'] = df_cartera['IMPORTE PENDIENTE'].fillna(0).astype(int)
        df_cartera_pivot = df_cartera.pivot_table(index=['TIPO CLIENTE','CLIENTE','RESPONSABLE'], columns='RANGO MORA', values='IMPORTE PENDIENTE', aggfunc='sum', fill_value=0).reset_index()
        df_cartera_pivot = df_cartera_pivot[['TIPO CLIENTE','RESPONSABLE','CLIENTE', 'Corriente', 'Proximo', '11_30', '31_60', '61_90', '90+' ]]
        df_cartera_pivot = df_cartera_pivot.copy()

        df_cartera_pivot['TOTAL'] = df_cartera_pivot[
            ['Corriente', 'Proximo', '11_30', '31_60', '61_90', '90+']
        ].sum(axis=1)
        df_cartera_pivot.columns = df_cartera_pivot.columns.str.upper()
        df_cartera_pivot_grouped = df_cartera_pivot.groupby(['TIPO CLIENTE','RESPONSABLE']).agg({
            'CLIENTE':'count',
            'CORRIENTE': 'sum',
            'PROXIMO': 'sum',
            '11_30': 'sum',
            '31_60': 'sum',
            '61_90': 'sum',
            '90+': 'sum',
            'TOTAL': 'sum'
        }).reset_index()
        df_cartera_pivot_grouped.sort_values(by='TOTAL', ascending=False, inplace=True)

        from IPython.display import display, HTML
        from datetime import datetime
        import locale

        # Configurar locale en español
        locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')

        fecha = datetime.today().strftime('%d de %B de %Y')


        # ── HELPERS ───────────────────────────────────────────────────────────────────
        def get_status_class(row):
            critico = row['90+'] + row['61_90']
            medio   = row['31_60'] + row['11_30']
            if critico > 0:
                return "background:#fff0f0;color:#c0392b;"
            elif medio > 0:
                return "background:#fffbeb;color:#b45309;"
            else:
                return "background:#f0faf4;color:#15803d;"

        def fmt(v):
            return f"${v:,.0f}"


        # ── INFORME POR RESPONSABLE ───────────────────────────────────────────────────
        def build_email_html(responsable, df):
            t = df[['CORRIENTE','PROXIMO','11_30','31_60','61_90','90+','TOTAL']].sum()

            filas = ""
            for _, row in df.iterrows():
                total_style = get_status_class(row)
                filas += f"""
                <tr>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;font-weight:600;color:#1a1f2e;white-space:nowrap;">{row['TIPO CLIENTE'].upper()}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#2e7d32;white-space:nowrap;">{fmt(row['CORRIENTE'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#0077b6;white-space:nowrap;">{fmt(row['PROXIMO'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#e65100;white-space:nowrap;">{fmt(row['11_30'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#c62828;white-space:nowrap;">{fmt(row['31_60'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#b71c1c;white-space:nowrap;">{fmt(row['61_90'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;font-weight:700;color:#7b1fa2;white-space:nowrap;">{fmt(row['90+'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;font-weight:700;border-radius:4px;white-space:nowrap;{total_style}">{fmt(row['TOTAL'])}</td>
                </tr>"""

            iniciales = "".join([p[0] for p in responsable.split()[:2]]).upper()
            n_clientes = df['CLIENTE'].sum() if 'CLIENTE' in df.columns else "—"

            if t['TOTAL'] > 0:
                icv_30 = (t['31_60'] + t['61_90'] + t['90+']) / t['TOTAL'] * 100
                icv_90 = t['90+'] / t['TOTAL'] * 100
            else:
                icv_30 = icv_90 = 0

            html = f"""
            <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:1100px;margin:0 auto;
                        background:#ffffff;border-radius:12px;overflow:hidden;
                        box-shadow:0 4px 24px rgba(0,0,0,0.08);">

            <!-- HEADER -->
            <div style="background:linear-gradient(135deg,#0f2044 0%,#1a3a6e 60%,#1e4d8c 100%);padding:28px 36px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td>
                    <div style="color:#ffffff;font-size:20px;font-weight:700;">📋 Informe de Cartera</div>
                    <div style="color:#93afd4;font-size:13px;margin-top:4px;">Estado de cuentas y vencimientos</div>
                    </td>
                    <td align="right">
                    <span style="background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);
                                color:#cddff5;padding:6px 14px;border-radius:20px;font-size:12px;">
                        📅 {fecha}
                    </span>
                    </td>
                </tr>
                </table>
                <div style="margin-top:18px;display:flex;gap:20px;flex-wrap:wrap;">
                <span style="color:#93afd4;font-size:12px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:#d5f5e3;border:1px solid #a9dfbf;margin-right:5px;"></span>Cartera sana
                </span>
                <span style="color:#93afd4;font-size:12px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:#fffbeb;border:1px solid #f9e79f;margin-right:5px;"></span>Mora media (11–60 días)
                </span>
                <span style="color:#93afd4;font-size:12px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:#fff0f0;border:1px solid #f5b7b1;margin-right:5px;"></span>Mora crítica (61+ días)
                </span>
                </div>
            </div>

            <!-- RESPONSABLE -->
            <div style="padding:24px 36px 8px;">
                <table cellpadding="0" cellspacing="0">
                <tr>
                    <td>
                    <div style="width:38px;height:38px;border-radius:50%;
                                background:linear-gradient(135deg,#1a3a6e,#1e4d8c);
                                color:white;font-size:14px;font-weight:700;
                                text-align:center;line-height:38px;">{iniciales}</div>
                    </td>
                    <td style="padding-left:12px;">
                    <div style="font-size:15px;font-weight:700;color:#0f2044;">{responsable}</div>
                    <div style="font-size:12px;color:#8492a6;">{n_clientes} clientes</div>
                    </td>
                    <td style="padding-left:24px;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;">ICV 30</div>
                    <div style="font-size:18px;font-weight:700;color:#c62828;">{icv_30:.2f}%</div>
                    </td>
                    <td style="padding-left:24px;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;">ICV 90+</div>
                    <div style="font-size:18px;font-weight:700;color:#7b1fa2;">{icv_90:.2f}%</div>
                    </td>
                </tr>
                </table>
            </div>

            <!-- TABLA -->
            <div style="padding:12px 36px 28px;overflow-x:auto;">
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border-collapse:collapse;font-size:11.5px;
                            border:1px solid #e8ecf2;border-radius:8px;overflow:hidden;">
                <thead>
                    <tr style="background:#f7f9fc;">
                    <th style="padding:11px 10px;text-align:left;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;">Canal</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">Corriente</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">Próximo</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">11–30 d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">31–60 d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">61–90 d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">90+ d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;">Total</th>
                    </tr>
                </thead>
                <tbody>
                    {filas}
                    <tr style="background:#0f2044;">
                    <td style="padding:12px 10px;color:#e8f0fc;font-weight:700;font-size:13px;">SUBTOTAL</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['CORRIENTE'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['PROXIMO'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['11_30'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['31_60'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['61_90'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['90+'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#7dd3fc;font-weight:700;font-size:14px;white-space:nowrap;">{fmt(t['TOTAL'])}</td>
                    </tr>
                </tbody>
                </table>
            </div>

            <!-- FOOTER -->
            <div style="background:#f7f9fc;padding:16px 36px;border-top:1px solid #e0e5ee;
                        display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                <span style="font-size:11.5px;color:#8492a6;">Generado automáticamente · Análisis de Datos </span>
                <span style="font-size:11.5px;color:#8492a6;">Valores en COP · Incluye todos los rangos de mora</span>
            </div>
            </div>
            """
            return html


        # ── INFORME CONSOLIDADO ───────────────────────────────────────────────────────
        def build_email_html_consolidado(df):
            t = df[['CORRIENTE','PROXIMO','11_30','31_60','61_90','90+','TOTAL']].sum()

            filas = ""
            for _, row in df.iterrows():
                total_style = get_status_class(row)
                filas += f"""
                <tr>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;font-weight:600;color:#1a1f2e;white-space:nowrap;">{row['TIPO CLIENTE'].upper()}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#2e7d32;white-space:nowrap;">{fmt(row['CORRIENTE'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#0077b6;white-space:nowrap;">{fmt(row['PROXIMO'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#e65100;white-space:nowrap;">{fmt(row['11_30'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#c62828;white-space:nowrap;">{fmt(row['31_60'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;color:#b71c1c;white-space:nowrap;">{fmt(row['61_90'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;font-weight:700;color:#7b1fa2;white-space:nowrap;">{fmt(row['90+'])}</td>
                <td style="padding:8px 10px;border-bottom:1px solid #eef0f5;text-align:right;font-weight:700;border-radius:4px;white-space:nowrap;{total_style}">{fmt(row['TOTAL'])}</td>
                </tr>"""

            n_responsables = df['RESPONSABLE'].nunique() if 'RESPONSABLE' in df.columns else "—"
            n_clientes     = df['CLIENTE'].sum() if 'CLIENTE' in df.columns else "—"

            if t['TOTAL'] > 0:
                icv_30 = (t['31_60'] + t['61_90'] + t['90+']) / t['TOTAL'] * 100
                icv_90 = t['90+'] / t['TOTAL'] * 100
            else:
                icv_30 = icv_90 = 0

            html = f"""
            <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:1100px;margin:0 auto;
                        background:#ffffff;border-radius:12px;overflow:hidden;
                        box-shadow:0 4px 24px rgba(0,0,0,0.08);">

            <!-- HEADER -->
            <div style="background:linear-gradient(135deg,#0f2044 0%,#1a3a6e 60%,#1e4d8c 100%);padding:28px 36px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td>
                    <div style="color:#ffffff;font-size:20px;font-weight:700;">📋 Informe de Cartera — Consolidado</div>
                    <div style="color:#93afd4;font-size:13px;margin-top:4px;">Vista global · Todos los responsables</div>
                    </td>
                    <td align="right">
                    <span style="background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);
                                color:#cddff5;padding:6px 14px;border-radius:20px;font-size:12px;">
                        📅 {fecha}
                    </span>
                    </td>
                </tr>
                </table>
                <div style="margin-top:18px;display:flex;gap:20px;flex-wrap:wrap;">
                <span style="color:#93afd4;font-size:12px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:#d5f5e3;border:1px solid #a9dfbf;margin-right:5px;"></span>Cartera sana
                </span>
                <span style="color:#93afd4;font-size:12px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:#fffbeb;border:1px solid #f9e79f;margin-right:5px;"></span>Mora media (11–60 días)
                </span>
                <span style="color:#93afd4;font-size:12px;">
                    <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:#fff0f0;border:1px solid #f5b7b1;margin-right:5px;"></span>Mora crítica (61+ días)
                </span>
                </div>
            </div>

            <!-- MÉTRICAS RESUMEN -->
            <div style="padding:20px 36px 8px;">
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                <td style="text-align:center;padding:12px 8px;background:#f7f9fc;border-radius:8px;border:1px solid #e0e5ee;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Responsables</div>
                    <div style="font-size:22px;font-weight:700;color:#0f2044;">{n_responsables}</div>
                </td>
                <td style="width:12px;"></td>
                <td style="text-align:center;padding:12px 8px;background:#f7f9fc;border-radius:8px;border:1px solid #e0e5ee;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Clientes</div>
                    <div style="font-size:22px;font-weight:700;color:#0f2044;">{n_clientes}</div>
                </td>
                <td style="width:12px;"></td>
                <td style="text-align:center;padding:12px 8px;background:#f7f9fc;border-radius:8px;border:1px solid #e0e5ee;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Cartera Total</div>
                    <div style="font-size:22px;font-weight:700;color:#0f2044;">{fmt(t['TOTAL'])}</div>
                </td>
                <td style="width:12px;"></td>
                <td style="text-align:center;padding:12px 8px;background:#fff0f0;border-radius:8px;border:1px solid #f5b7b1;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">ICV 30</div>
                    <div style="font-size:22px;font-weight:700;color:#c62828;">{icv_30:.2f}%</div>
                </td>
                <td style="width:12px;"></td>
                <td style="text-align:center;padding:12px 8px;background:#fdf4ff;border-radius:8px;border:1px solid #e9d5ff;">
                    <div style="font-size:11px;color:#8492a6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">ICV 90+</div>
                    <div style="font-size:22px;font-weight:700;color:#7b1fa2;">{icv_90:.2f}%</div>
                </td>
                </tr>
            </table>
            </div>
            <!-- TABLA -->
            <div style="padding:12px 36px 28px;overflow-x:auto;">
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border-collapse:collapse;font-size:11.5px;
                            border:1px solid #e8ecf2;border-radius:8px;overflow:hidden;">
                <thead>
                    <tr style="background:#f7f9fc;">
                    <th style="padding:11px 10px;text-align:left;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;">Canal</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">Corriente</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">Próximo</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">11–30 d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">31–60 d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">61–90 d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;white-space:nowrap;">90+ d</th>
                    <th style="padding:11px 10px;text-align:right;font-weight:600;color:#5a6478;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #e0e5ee;">Total</th>
                    </tr>
                </thead>
                <tbody>
                    {filas}
                    <tr style="background:#0f2044;">
                    <td style="padding:12px 10px;color:#e8f0fc;font-weight:700;font-size:13px;">TOTAL GENERAL</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['CORRIENTE'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['PROXIMO'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['11_30'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['31_60'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['61_90'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#e8f0fc;font-weight:700;white-space:nowrap;">{fmt(t['90+'])}</td>
                    <td style="padding:12px 10px;text-align:right;color:#7dd3fc;font-weight:700;font-size:14px;white-space:nowrap;">{fmt(t['TOTAL'])}</td>
                    </tr>
                </tbody>
                </table>
            </div>

            <!-- FOOTER -->
            <div style="background:#f7f9fc;padding:16px 36px;border-top:1px solid #e0e5ee;
                        display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                <span style="font-size:11.5px;color:#8492a6;">Generado automáticamente · Análisis de Datos </span>
                <span style="font-size:11.5px;color:#8492a6;">Valores en COP · Incluye todos los rangos de mora</span>
            </div>
            </div>
            """
            return html


        # ── EJECUCIÓN ─────────────────────────────────────────────────────────────────
        informes_por_responsable = {}



        html_consolidado = build_email_html_consolidado(df_cartera_pivot_grouped)
        informes_por_responsable['__CONSOLIDADO__'] = html_consolidado


        # POR RESPONSABLE
        for responsable in df_cartera_pivot_grouped['RESPONSABLE'].unique():
            df = df_cartera_pivot_grouped[df_cartera_pivot_grouped['RESPONSABLE'] == responsable].copy()
            html = build_email_html(responsable, df)
            informes_por_responsable[responsable] = html

        return informes_por_responsable, df_cartera
    






    # -------------------------------------------------
    # LIMPIAR VALORES MONEDA
    # -------------------------------------------------

    def limpiar_moneda(self, valor):
        if not valor:
            return None
        return float(valor.replace('.', '').replace(',', '.'))


    # -------------------------------------------------
    # EXTRAER TIPO DE CERTIFICADO (ICA, FUENTE, ETC)
    # -------------------------------------------------

    def extraer_tipo_certificado(self,texto):

        match = re.search(
            r"CERTIFICADO DE RETENCIÓN EN\s+([A-ZÁÉÍÓÚÑ ]+)",
            texto
        )

        if not match:
            return "CERT"

        tipo = match.group(1)

        # cortar si aparece NIT o salto de línea
        tipo = tipo.split("NIT")[0]
        tipo = tipo.split("\n")[0]

        # limpiar artículos
        tipo = re.sub(r"\b(LA|EL|DEL|DE)\b", "", tipo)

        tipo = tipo.strip()

        # reemplazar espacios por _
        tipo = re.sub(r"\s+", "_", tipo)

        return tipo


    # -------------------------------------------------
    # FUNCIÓN PRINCIPAL
    # -------------------------------------------------

    def separar_pdfs_estrategico(
            self,
            carpeta_salida,
            ruta_pdf_input=None,
            find_texto="NIT:",
            nit_carpeta=True,
            list_path=None,
            df=False,
            enumerar=0):

        os.makedirs(carpeta_salida, exist_ok=True)

        registros = []

        # -------------------------------------------------
        # ABRIR DOCUMENTO
        # -------------------------------------------------

        if list_path:
            doc = fitz.open()
            for pdf in list_path:
                with fitz.open(pdf) as temp:
                    doc.insert_pdf(temp)
        else:
            doc = fitz.open(ruta_pdf_input)

        total_paginas = len(doc)

        print(f"🚀 Procesando {total_paginas} páginas...")

        procesados = 0
        contador_global = enumerar

        for i in range(total_paginas):

            pagina = doc[i]
            texto = pagina.get_text()

            # -------------------------------------------------
            # TIPO CERTIFICADO DINÁMICO
            # -------------------------------------------------

            nombre_base_extraido = self.extraer_tipo_certificado(texto)

            # -------------------------------------------------
            # EXTRAER NIT
            # -------------------------------------------------

            nits_encontrados = re.findall(
                rf"{find_texto}\s*(\d+-?\d*)",
                texto
            )

            if len(nits_encontrados) >= 2:
                nit_cliente = nits_encontrados[1].replace("-", "").strip()
            else:
                nit_cliente = "REVISAR_MANUAL"

            # -------------------------------------------------
            # EXTRAER TOTALES
            # -------------------------------------------------

            monto_match = re.search(
                r"MONTO DEL PAGO SUJETO A RETENCIÓN:\s*\$\s*([\d\.,]+)",
                texto
            )

            retenido_match = re.search(
                r"RETENIDO Y CONSIGNADO:\s*\$\s*([\d\.,]+)",
                texto
            )

            monto_pago = self.limpiar_moneda(
                monto_match.group(1)
            ) if monto_match else None

            retenido = self.limpiar_moneda(
                retenido_match.group(1)
            ) if retenido_match else None

            # -------------------------------------------------
            # CARPETA DESTINO
            # -------------------------------------------------

            ruta_destino_final = carpeta_salida

            if nit_carpeta:
                ruta_destino_final = os.path.join(
                    carpeta_salida,
                    nit_cliente
                )
                os.makedirs(ruta_destino_final, exist_ok=True)

            # -------------------------------------------------
            # NOMBRE ARCHIVO
            # -------------------------------------------------

            if enumerar > 0:

                contador_global += 1
                id_archivo = contador_global

                nombre_archivo = (
                    f"{id_archivo}_{nit_cliente}_{nombre_base_extraido}.pdf"
                )

                ruta_final = os.path.join(
                    ruta_destino_final,
                    nombre_archivo
                )

            else:

                contador = 1
                id_archivo = None

                nombre_archivo = (
                    f"{nit_cliente}_{nombre_base_extraido}.pdf"
                )

                ruta_final = os.path.join(
                    ruta_destino_final,
                    nombre_archivo
                )

                while os.path.exists(ruta_final):

                    contador += 1

                    nombre_archivo = (
                        f"{nit_cliente}_{nombre_base_extraido}_{contador}.pdf"
                    )

                    ruta_final = os.path.join(
                        ruta_destino_final,
                        nombre_archivo
                    )

            # -------------------------------------------------
            # GUARDAR PDF
            # -------------------------------------------------

            nuevo_doc = fitz.open()

            nuevo_doc.insert_pdf(
                doc,
                from_page=i,
                to_page=i
            )

            nuevo_doc.save(ruta_final)

            nuevo_doc.close()

            # -------------------------------------------------
            # DATAFRAME
            # -------------------------------------------------

            if df and enumerar > 0:

                registros.append({
                    "ID": id_archivo,
                    "NIT": nit_cliente,
                    "CERTIFICADO": nombre_base_extraido,
                    "MONTO_PAGO_SUJETO_RETENCION": monto_pago,
                    "RETENIDO_CONSIGNADO": retenido,
                    "archivo": nombre_archivo
                })

            procesados += 1

            if procesados % 10 == 0 or procesados == total_paginas:
                print(f"📊 {procesados}/{total_paginas} páginas procesadas...")

        doc.close()

        print(f"✅ Finalizado: archivos en {carpeta_salida}\n")

        # -------------------------------------------------
        # RETORNAR DATAFRAME
        # -------------------------------------------------

        if df and enumerar > 0:
            return pd.DataFrame(registros)