import pandas as pd
from pathlib import Path
import os 
from thefuzz import process
from rapidfuzz import process
import pandas as pd
import os
import re
import tkinter as tk
from tkinter import filedialog
import numpy as np
import json
import unicodedata
from typing import Optional


class ReportClassNew():
    """
    Esta clase contiene las funciones que su utilizan para actulizar el bi
    y las ventas procesadas.    
    """



    def consolidar_carpeta(self, extension='xlsx', sep=None, encoding=None, decimal=',',sheet_name=None, ruta_carpeta=None):
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
        df_concatenado = pd.concat(lista_dataframes, ignore_index=True)
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



        # Cargar catálogo Colombia
        ciudad_url = "https://www.datos.gov.co/resource/gdxc-w37w.csv?$limit=5000"
        DF_CIUDADES = pd.read_csv(ciudad_url)
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
        lista_ciudades_norm = DF_CIUDADES['Ciudad_norm'].unique()



        #  Diccionario de alias
        ALIASES = {
            "cali": "Cali",
        }

        #  Validación de entrada
        def es_valido(texto):
            texto = normalizar(texto)
            return (
                len(texto) >= 3 and
                re.search(r"[a-z]", texto)
            )



        #  Fuzzy matcher con alias + umbral
        def corregir_ciudad(ciudad_mal):
            if not es_valido(ciudad_mal):
                return "DESCONOCIDO"

            ciudad_norm = normalizar(ciudad_mal)

            # ---- Alias primero ----
            if ciudad_norm in ALIASES:
                return ALIASES[ciudad_norm]

            # ---- Fuzzy matching ----
            mejor_match, score, idx = process.extractOne(
                ciudad_norm,
                lista_ciudades_norm
            )

            # subir el umbral
            if score >= 82:
                return DF_CIUDADES.iloc[idx]['Ciudad_Correcta'].upper()

            return str(ciudad_mal).upper()


        #  Aplicación
        df_resultado = df_filtrado.rename(columns={
            'Líneas de factura/Asociado/Ciudad': 'Ciudad'
        })

        df_resultado["Ciudad_Corregida"] = df_resultado["Ciudad"].apply(corregir_ciudad)



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
            'Ciudad': 'Ciudad',
            'Líneas de factura/Asociado/Estado': 'Departamento',
            'Equipo de Ventas': 'Equipo_Ventas',
            'Líneas de factura/Referencia': 'Referencia',
            'pais': 'Pais',
            'Fecha': 'Fecha_TRM',
            'TRM': 'TRM',
            'TOTAL': 'Total($)',
            'Ciudad_Corregida': 'Ciudad_Corregida',
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
                                .merge(cundinamarca,  on=['DEPARTAMENTO','CIUDAD', 'CATEGORÍA'], how='left')


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
                        np.where(df_base['N2']=='52', 'Gastos5 operacionales',
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
        df_concepto_doble =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='doble concepto')

        influencer =pd.read_excel(ruta_contabilidad / 'base_cuentas.xlsx', sheet_name='INFLUENCER')

        df_base['N3'] = df_base['N3'].astype(int)
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

        df_base_merge['Distribución analítica'] = df_base_merge['Distribución analítica ori'].apply(extraer_clave)


        # Ajustes manuales de asignación de centro de costo y concepto
        df_base_merge['N1'] = df_base_merge['N1'].astype(str)
        df_base_merge['N2'] = df_base_merge['N2'].astype(str)
        df_base_merge['N3'] = df_base_merge['N3'].astype(str)

        df_base_merge.loc[
            (df_base_merge['N3'] == '4135'),
            'Distribución analítica', 
        ] = '6'

        df_base_merge.loc[
            (df_base_merge['N3'] == '4175') & 
            (df_base_merge['Diario']!="Facturas de cliente Cali"),
            'Distribución analítica', 
        ] = '6'

        df_base_merge.loc[
            (df_base_merge['N1'] == '6') & (df_base_merge['Distribución analítica ori'].isna()),
            'Distribución analítica'
        ] =  '6'

        df_base_merge.loc[
            (df_base_merge['N2'] == '42') & (df_base_merge['Distribución analítica ori'].isna()),
            'Distribución analítica'
        ] = '6'

        df_base_merge.loc[(df_base_merge['Distribución analítica'].isna()) & 
                    (df_base_merge['Número'].str.startswith('BNK')) &
                        (df_base_merge['Cuenta Origen'].isin(['530515001 COMISIONES','530505002 GRAVAMEN CUATRO POR MIL', '530505001 CUOTA DE MANEJO']))
                    , 'Distribución analítica'
                    ] = '7'

        df_base_merge.loc[(df_base_merge['Distribución analítica'].isna()) & 
                    (df_base_merge['Número'].str.startswith('BNK')) &
                        (df_base_merge['Cuenta Origen'].isin(['539595001 AJUSTE A MILES']))
                    , 'Distribución analítica'
                    ] = '6' 

        df_base_merge.loc[(df_base_merge['Distribución analítica'].isna()) & 
                    (df_base_merge['Número'].str.startswith('STJ')) &
                    (~df_base_merge['Contacto'].isin(influencer['Contacto'].unique().tolist()))
                    , 'Distribución analítica'
                    ] = '6'  # validar si es clientre cc ==comercial  o infulerce cc== marketing ==

        df_base_merge.loc[(df_base_merge['Distribución analítica'].isna()) & 
                    (df_base_merge['Número'].str.startswith('STJ')) &
                    (df_base_merge['Contacto'].isin(influencer['Contacto'].unique().tolist()))
                    , 'Distribución analítica'
                    ] = '4'  # validar si es clientre cc ==comercial  o infulerce cc== marketing ==

        mask = df_base_merge['Distribución analítica'].fillna('').str.contains('5,')

        df_base_merge.loc[mask, 'Distribución analítica'] = (
            df_base_merge.loc[mask, 'Distribución analítica']
            .apply(lambda x: x.split(',')[1].strip())
        )

            # df_base_merge[df_base_merge['Distribución analítica']
            # .fillna('0').str.contains('5,')]

        df_cc['cc'] = df_cc['cc'].astype(str)

        df_base_merge = df_base_merge.merge(df_cc[['cc','Nombre Cencosto', 'ADM/VTAS','Origen' ]],
                                            left_on='Distribución analítica', right_on='cc', how='left').drop(columns='cc')

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

        df_base_merge.columns

        df_concepto_doble['id'] = df_concepto_doble['Nombre Cencosto'] + df_concepto_doble['Cuenta'].astype(str)
        df_concepto_doble = df_concepto_doble.drop_duplicates(subset=['id'], keep='first')
        df_base_merge = df_base_merge.merge(df_concepto_doble, on=['Cuenta','Nombre Cencosto'],  how='left')


        # Verifica las cuentas que no tienen concepto
        df_cuentas = df_base_merge[df_base_merge['Concepto'].isna()][['Cuenta','Nombre Cencosto', 'Distribución analítica']]
        df_cuentas = df_cuentas.drop_duplicates(subset=['Cuenta', 'Nombre Cencosto',], keep='first')
        df_concepto = df_concepto.drop_duplicates(subset=['Cuenta', 'Nombre Cencosto'])


        df_concepto_doble = df_concepto_doble.drop_duplicates(subset=['Cuenta'])

        df_cuentas= df_cuentas.merge(df_concepto, on=['Cuenta','Nombre Cencosto'], how='left').merge(df_concepto_doble, on=['Cuenta','Nombre Cencosto'], how='left')
        df_cuentas = df_cuentas.fillna('Sin datos')


        df_cuentas['Estado Cuenta'] = np.where(
            (df_cuentas['Concepto_cc']=="Sin datos") & (df_cuentas['Concepto_doble']== 'Sin datos'),
            'Cuenta Nueva',
            np.where(
                df_cuentas['Concepto_doble'].notna(),
                'Cuenta Doble Concepto',
                'Revisar'
            )
        )

        df_cuentas = df_cuentas[['Cuenta', 'Nombre Cencosto','Estado Cuenta', 'Distribución analítica']]

        # Elimina las columnas que no son necesarias
        df_base_merge = df_base_merge.drop(columns=['Concepto_uni', 'Concepto_cc'])

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

        centros_no_re = df_base_merge[(df_base_merge['Nombre Cencosto'].isna())&
                    (~df_base_merge['Distribución analítica'].isna())
                    ][['Distribución analítica ori','Distribución analítica', 'Nombre Cencosto' ]].drop_duplicates()
        # Centros de costo mal clasificados
        cc_corregir = df_base_merge[df_base_merge['Distribución analítica ori'].fillna('').str.count(':')>1]

        # Genera el archivo de los casos sin centro de costos
        sin_cc = df_base_merge[df_base_merge['Distribución analítica'].isna()]
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
        # pd.read_csv(r"C:\Users\Dataa\Desktop\VENTAS\VENTA MENSUAL\data\contabilidad\base\base_ene_jun_2025.csv",encoding='utf-8', sep=';')
        df_base_consol = df_base_consol.loc[:, ~df_base_consol.columns.str.contains('^Unnamed')]

        df_base_consol.to_csv(ruta_contabilidad / 'base_consolidada.csv', encoding='utf-8', sep=';', decimal=',', index=False)


        # df_base_consol.to_excel(ruta_contabilidad / 'base_consolidada.xlsx',  index=False)

        return df_base_consol, dicc