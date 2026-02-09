import pandas as pd
from pathlib import Path
import os 
from thefuzz import process
import pandas as pd
import os
import re
import tkinter as tk
from tkinter import filedialog
import numpy as np
import json




class ReportClass():
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




    def notas_creditos(self):

        """
        Procesa las ventas descargadas de Odoo y descuenta las notas crédito.

        Permite seleccionar los archivos de ventas y notas crédito, realiza el cruce
        por número de factura y producto, descuenta las cantidades y totales afectados,
        y genera archivos procesados y un listado de facturas afectadas.

        Returns:
            dict: Diccionario con el DataFrame consolidado, nombre del archivo de salida,
                  y listado de facturas afectadas por notas crédito.
        """

        # Ocultar la ventana principal de tkinter
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        # Paso 1: Seleccionar el archivo de ventas
        nombre_archivo_ventas = filedialog.askopenfilename(
            title="Selecciona el archivo de ventas (.xlsx)",
            filetypes=[("Archivos de Excel", "*.xlsx")]
        )

        # Verificar si se seleccionó un archivo
        if not nombre_archivo_ventas:
            print("No se seleccionó el archivo de ventas.")
            exit()

        # Paso 2: Cargar el archivo de ventas
        try:
            df_ventas = pd.read_excel(nombre_archivo_ventas)
            print(f"Archivo de ventas '{os.path.basename(nombre_archivo_ventas)}' cargado correctamente.")
        except Exception as e:
            print(f"Error al cargar el archivo de ventas: {e}")
            exit()

        # Paso 3: Seleccionar el archivo de notas crédito
        nombre_archivo_notas_credito = filedialog.askopenfilename(
            title="Selecciona el archivo de notas crédito (.xlsx)",
            filetypes=[("Archivos de Excel", "*.xlsx")]
        )

        # Verificar si se seleccionó un archivo
        if not nombre_archivo_notas_credito:
            print("No se seleccionó el archivo de notas crédito.")
            exit()

        # Paso 4: Cargar el archivo de notas crédito
        try:
            df_notas_credito = pd.read_excel(nombre_archivo_notas_credito)
            print(f"Archivo de notas crédito '{os.path.basename(nombre_archivo_notas_credito)}' cargado correctamente.")
        except Exception as e:
            print(f"Error al cargar el archivo de notas crédito: {e}")
            exit()

        df_notas_credito['NUMERO_FACTURA'] = df_notas_credito['Líneas de factura/Referencia'].apply(
            lambda x: re.search(r'(?:FEVY|FVE|FYEX|[234]YPO|YPOS|PSYA|PSYB)\d*', x).group(0) if pd.notna(x) and re.search(r'(?:FEVY|FVE|FYEX|[234]YPO|YPOS|PSYA|PSYB)\d*', x) else None
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
        ####



        df_notas_credito = df_notas_credito.drop(columns=['Líneas de factura/Número'])
        df_notas_credito = df_notas_credito.rename(columns={'NUMERO_FACTURA': 'Líneas de factura/Número'})
        # Paso 6: Convertir las cantidades y totales de las notas crédito a valores negativos
        df_notas_credito['Líneas de factura/Cantidad'] = -df_notas_credito['Líneas de factura/Cantidad']
        df_notas_credito['Líneas de factura/Total'] = -df_notas_credito['Líneas de factura/Total']



        # Paso 8: Crear una columna temporal que combine NUMERO_FACTURA y PRODUCTO
        df_ventas['NUMERO_FACTURA-PRODUCTO'] = df_ventas['Líneas de factura/Número'] + '-' + df_ventas['Líneas de factura/Producto']
        df_notas_credito['NUMERO_FACTURA-PRODUCTO'] = df_notas_credito['Líneas de factura/Número'] + '-' + df_notas_credito['Líneas de factura/Producto']
        # Paso 9: Filtrar las notas crédito para incluir solo las que coinciden con ventas existentes
        notas_credito_validas = df_notas_credito['NUMERO_FACTURA-PRODUCTO'].isin(df_ventas['NUMERO_FACTURA-PRODUCTO'])
        df_notas_credito_filtrado = df_notas_credito[notas_credito_validas]
        # Paso 8: Combinar ambos datasets (ventas y notas crédito)
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

        # Paso 10: Agrupar por la columna temporal NUMERO_FACTURA-PRODUCTO
        df_consolidado = df_consolidado.groupby(
            'NUMERO_FACTURA-PRODUCTO',  # Agrupar por la combinación de factura y producto
            as_index=False
        ).agg({
            'Líneas de factura/Fecha de factura': 'first',
            'Líneas de factura/Asociado': 'first',
            'Líneas de factura/Número': 'first',
            'Líneas de factura/Producto': 'first',
            'Líneas de factura/Cantidad': 'sum',  # Sumar las cantidades
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
            'Etiqueta contacto': 'first'

        })
        # Paso 12: Eliminar la columna temporal NUMERO_FACTURA-PRODUCTO
        df_consolidado.drop(columns=['NUMERO_FACTURA-PRODUCTO'], inplace=True)
        # Paso 13: Filtrar solo las filas donde la cantidad sea mayor que 0 (eliminar ventas canceladas)
        df_consolidado = df_consolidado[df_consolidado['Líneas de factura/Cantidad'] > 0]
        # Paso 14: Generar el nombre del archivo de salida
        nombre_archivo= os.path.splitext(os.path.split(nombre_archivo_ventas)[-1])[0]

        # Paso 15: Guardar el archivo consolidado
        try:
            ruta = self.validar_ruta()
            ruta_salida = ruta / 'RAW DATA' / 'PROCESADO' / f'{nombre_archivo}_procesado.xlsx'
            if not ruta_salida.parent.exists():
                ruta_salida.parent.mkdir(parents=True, exist_ok=True)
            df_consolidado.to_excel(ruta_salida, index=False)
            print(f"Archivo consolidado guardado como '{ruta_salida}'.")
        except Exception as e:
            print(f"Error al guardar el archivo consolidado: {e}")

        # Paso 16: Obtener el listado de facturas afectadas por notas crédito
        facturas_afectadas = df_notas_credito_filtrado[['Líneas de factura/Número', 'Líneas de factura/Producto', 'Líneas de factura/Cantidad', 'Líneas de factura/Total']].dropna(subset=['Líneas de factura/Número'])

        # Paso 17: Mostrar el listado de facturas afectadas
        print("Facturas afectadas por notas crédito:")
        print(facturas_afectadas.shape)


        try:
            nombre_archivo_facturas_afectadas = ruta / 'RAW DATA' / 'FACTURAS AFECTADAS' / f'{nombre_archivo}_facturas_afectadas.xlsx' 
            facturas_afectadas.to_excel(nombre_archivo_facturas_afectadas, index=False)
            print(f"Listado de facturas afectadas guardado como '{nombre_archivo_facturas_afectadas}'.")
        except Exception as e:
            print(f"Error al guardar el listado de facturas afectadas: {e}")

        return {'archivo_salida': df_consolidado,
                'nombre_archivo':ruta_salida,
                'facturas_afectadas' :facturas_afectadas,
                'etiqueta_mayorista': etiqueta_mayorista}
    
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




    def transformar_base(self, origen=False):
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

     
        if origen:
            notas_creditos = self.notas_creditos()
            nombre_archivo = notas_creditos['nombre_archivo']
            df = notas_creditos['archivo_salida']
        else:
            # Ocultar la ventana principal de tkinter
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            # Paso 1: Seleccionar el archivo de ventas
            nombre_archivo_ventas = filedialog.askopenfilename(
                title="Por favor, ingresa el nombre del archivo de ventas (incluye la extensión .xlsx): ",
                filetypes=[("Archivos de Excel", "*.xlsx")]
            )

            # Verificar si se seleccionó un archivo
            if not nombre_archivo_ventas:
                print("No se seleccionó el archivo.")
                exit()

            # Paso 2: Cargar el archivo de ventas
            try:
                df = pd.read_excel(nombre_archivo_ventas)
                nombre_archivo = os.path.basename(nombre_archivo_ventas)
                print(f"Archivo  '{nombre_archivo}' cargado correctamente.")
            except Exception as e:
                print(f"Error al cargar el archivo: {e}")
                exit()

        etiqueta_mayorista = notas_creditos['etiqueta_mayorista']


        # Extraer el código de país y reemplazar NaN con "Desconocido" en un solo paso
        df['pais'] = df['Líneas de factura/Asociado/Estado'].str.extract(r'\(([A-Z]{2})\)').fillna("Desconocido")

        # Crear la columna 'total' con la lógica especificada
        df['Líneas de factura/Asociado/Ciudad'] = df['Líneas de factura/Asociado/Ciudad'].astype(str).fillna("Desconocido")

        df_filtrado = df.copy()
      
        # # Guarda en la variables las ventas sin tipo de cliente y con etiqueta mayorista
        # etiqueta_mayorista = df_filtrado[(df_filtrado['Tipo de cliente'].isna())&
        #             (df_filtrado['Etiqueta contacto']=='MAYORISTA NV')
        #             ] 
        # # Copia de la etiqueta los clientes mayoristas que aparecen en blanco
        # df_filtrado.loc[(df_filtrado['Tipo de cliente'].isna())&
        #             (df_filtrado['Etiqueta contacto']=='MAYORISTA NV'), 'Tipo de cliente'
        #             ] = 'MAYORISTA NV'

        # equipo_por_factura = (
        #     df_filtrado
        #     .groupby('Líneas de factura/Número')['Equipo de Ventas']
        #     .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
        #     .to_dict()
        # )

        # df_filtrado['Equipo de Ventas'] = df_filtrado['Líneas de factura/Número'].map(equipo_por_factura)



        # asesor_por_factura = (
        #     df_filtrado
        #     .groupby('Líneas de factura/Número')['Asesor Comercial']
        #     .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
        #     .to_dict()
        # )
        # df_filtrado['Asesor Comercial'] = df_filtrado['Líneas de factura/Número'].map(asesor_por_factura)

        # tipo_por_factura = (
        #     df_filtrado
        #     .groupby('Líneas de factura/Número')['Tipo de cliente']
        #     .agg(lambda x: x.dropna().iloc[0] if not x.dropna().empty else None)
        #     .to_dict()
        # )
        # df_filtrado['Tipo de cliente'] = df_filtrado['Líneas de factura/Número'].map(tipo_por_factura)


        df_filtrado = df_filtrado[df_filtrado['Líneas de factura/Producto'].str.startswith(('[PCN','[KD','[TNG','[B8'))].copy()   ###### linea modificada
        print("FILAS CATALOGO:", df_filtrado[df_filtrado['Líneas de factura/Asociado']=='NOVAVENTA S.A.S']['Tipo de cliente'].value_counts())
   
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
     

        #  Leer el CSV desde la URL
        url = "https://www.datos.gov.co/resource/32sa-8pi3.csv"
        df_TRM = pd.read_csv(url)

        #  Cambiar tipos de datos
        df_TRM['valor'] = pd.to_numeric(df_TRM['valor'], errors='coerce')
        df_TRM['unidad'] = df_TRM['unidad'].astype(str)
        df_TRM['vigenciadesde'] = pd.to_datetime(df_TRM['vigenciadesde'], errors='coerce')
        df_TRM['vigenciahasta'] = pd.to_datetime(df_TRM['vigenciahasta'], errors='coerce')

        #  Crear una nueva columna con el año de 'vigenciadesde'
        df_TRM['Año'] = df_TRM['vigenciadesde'].dt.year

        #  Filtrar por el año 2025
        today = pd.to_datetime('now')
        df_TRM = df_TRM[df_TRM['Año'] == today.year]
        df_TRM['TRM'] = df_TRM['valor']

        # Crear una lista para almacenar las filas expandidas
        expanded_rows = []

        # Iterar sobre cada fila de df_TRM y generar las fechas dentro del rango de vigencia
        for _, row in df_TRM.iterrows():
            date_range = pd.date_range(start=row['vigenciadesde'], end=row['vigenciahasta'], freq='D')
            for date in date_range:
                expanded_rows.append({'Fecha': date, 'TRM': row['TRM']})

        # Crear un nuevo DataFrame a partir de la lista
        df_TRM_expandido = pd.DataFrame(expanded_rows)

        # Eliminar duplicados (si los hay)
        df_TRM_expandido = df_TRM_expandido.drop_duplicates(subset=['Fecha'])

        # Ordenar por fecha
        df_TRM_expandido = df_TRM_expandido.sort_values('Fecha')

        # Verificar el nuevo DataFrame
     
    
        df_filtrado['Líneas de factura/Fecha de factura'] = pd.to_datetime(df_filtrado['Líneas de factura/Fecha de factura'])
        df_TRM_expandido['Fecha'] = pd.to_datetime(df_TRM_expandido['Fecha'])
        df_filtrado = df_filtrado.sort_values(by='Líneas de factura/Fecha de factura')
        df_TRM_expandido = df_TRM_expandido.sort_values(by='Fecha')
    
        # Intentar el merge_asof
        try:
            df_resultado = pd.merge_asof(
                df_filtrado,
                df_TRM_expandido[['Fecha', 'TRM']],  # Mantener solo las columnas necesarias
                left_on='Líneas de factura/Fecha de factura',
                right_on='Fecha',
                direction='backward'  # Tomar la TRM vigente más reciente anterior o igual a la fecha
            )
            
            # Verificar si la columna TRM está vacía
            if df_resultado['TRM'].isnull().all():
                print("Advertencia: La columna TRM está vacía después del merge. Verifica las fechas y los datos.")
            else:
                print("Merge completado correctamente.")
        except Exception as e:
            print(f"Error al realizar el merge: {str(e)}")
            import traceback
            traceback.print_exc()

        df_resultado['total'] = df_resultado.apply(
            lambda row: row['Líneas de factura/Total'] if row['pais'] in ['CO', 'Desconocido'] else row['Líneas de factura/Total'] * row['TRM'],
            axis=1
        )
        

        ciudad_url = "https://www.datos.gov.co/resource/gdxc-w37w.csv?$limit=5000"
        DF_CIUDADES = pd.read_csv(ciudad_url)
        # DF_CIUDADES = pd.read_excel(r"C:\Users\Dataa\Desktop\VENTAS\VENTA MENSUAL\CIUDAD.xlsx") # Dataset con nombres correctos
        DF_CIUDADES = DF_CIUDADES.rename(columns= {'nom_mpio':'Ciudad_Correcta'})
        df_resultado = df_resultado.rename(columns= {'Líneas de factura/Asociado/Ciudad':'Ciudad'})
       
        # 2️⃣ Definir los nombres de columnas
        col_ciudad_correcta = "Ciudad_Correcta"  # Nombre en DF_CIUDADES
        col_ciudad_ventas = "Ciudad"  # Nombre en DF_VENTAS

        # 3️⃣ Convertir todas las ciudades a string y manejar NaN
        df_resultado[col_ciudad_ventas] = df_resultado[col_ciudad_ventas].astype(str).fillna("Desconocido")


        # 4️⃣ Lista de ciudades correctas (convertidas a string)
        lista_ciudades_correctas = DF_CIUDADES[col_ciudad_correcta].astype(str).unique()
       
        # 5️⃣ Función para encontrar la mejor coincidencia
        def corregir_ciudad(ciudad_mal):
            if ciudad_mal.lower() == "nan" or ciudad_mal.strip() == "":
                return "Desconocido"  # Manejar valores vacíos o NaN
            mejor_match, score = process.extractOne(ciudad_mal, lista_ciudades_correctas)
            return mejor_match if score >= 60 else ciudad_mal  # Si el match es bajo, dejar el original

        # 5️⃣ Aplicar la función a la columna de ciudades en ventas
        df_resultado["Ciudad_Corregida"] = df_resultado[col_ciudad_ventas].apply(corregir_ciudad)
       
   
      
        # 6️⃣ Convertir la columna "Ciudad_Corregida" a mayúsculas
        df_resultado["Ciudad_Corregida"] = df_resultado["Ciudad_Corregida"].str.upper()
       
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
            'total': 'Total($)',
            'Ciudad_Corregida': 'Ciudad_Corregida'
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
                        'Total', 'TRM', 'Total($)','Telefono', 'Email','Pais','Ciudad', 'Ciudad_Corregida', 'Departamento', 
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


        # 2. Limpieza de la columna de identificación en ambos DataFrames
        # Limpieza en tu DataFrame actual
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

        # validar clientes nuevos, pendiente por terminar
        try:
            ruta = self.validar_ruta()
            ruta_historoica = ruta / 'file' / f'ventas_historicas.xlsx'

            

            ventas_historicas_agru=pd.read_excel(ruta_historoica)
            ventas_historicas_agru['FECHA_FACTURA'] = pd.to_datetime(ventas_historicas_agru['FECHA_FACTURA'])

            ventas_historicas_agru['IDENTIFICACION_CLIENTE'] =  ventas_historicas_agru['IDENTIFICACION_CLIENTE'].apply(self.limpiar_documento)
            ventas_historicas_agru['IDENTIFICACION_CLIENTE'].astype(str)



            df_resultado = df_resultado.merge(ventas_historicas_agru, on='IDENTIFICACION_CLIENTE', how='left', suffixes=('', '_FECHA_MIN'))

            now = pd.to_datetime('now')

            df_resultado['CLIENTES NUEVOS'] = np.where(
                ((df_resultado['FECHA_FACTURA_FECHA_MIN'].dt.month == now.month) & 
                (df_resultado['FECHA_FACTURA_FECHA_MIN'].dt.year == now.year))|((df_resultado['FECHA_FACTURA_FECHA_MIN'].isna())&(df_resultado['CATEGORÍA']=='MAYORISTA NV')),
                "Cliente sin historico",
                ""
            )

            df_resultado = df_resultado.drop(columns=['FECHA_FACTURA_FECHA_MIN'])
        except Exception as e:
            print(f"Error al validar clientes nuevos: {e}")

        return  {'Base':df_resultado,
                'nombre_archivo':notas_creditos['nombre_archivo'],
                'facturas_afectadas':notas_creditos['facturas_afectadas'],
                'errores':etiqueta_mayorista,
                # 'asesores_sin_categoria':asesores_sin_categoria,
                'cliente_call_center':cliente_call_center
                 }




    def explosion_ventas(self, ruta=None, sheet_name=None,):
        """
        Realiza la explosión de ventas para actualizar el BI.

        Descompone los kits en productos individuales, calcula cantidades e ingresos,
        y genera tablas dinámicas por producto, mes y origen (kit/individual).

        Args:
            ruta (str, optional): Ruta del archivo de ventas. Por defecto usa la carpeta compartida.
            sheet_name (str, optional): Nombre de la hoja de Excel con las ventas.

        Returns:
            None: El resultado se utiliza para actualizar reportes y BI.
        """
        if ruta: # Si se proporciona una ruta, úsala
            ruta = Path(ruta)
            ruta_kits = ruta / 'data' / 'kits.xlsx'
            df_kits = pd.read_excel(ruta_kits)
        else:
            ruta = self.validar_ruta()
            ruta_kits = ruta / 'data' / 'kits.xlsx'
            df_kits = pd.read_excel(ruta_kits)

        ruta_base =ruta / 'file' / 'BASE VENTAS 2025.xlsx'

        # Cargar el archivo de Excel
        df_ventas = pd.read_excel(ruta_base)  # Reemplaza con la ruta de tu archivo

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
        df_explosion["VALOR_POR_PRODUCTO"] = df_explosion['TOTAL($)'] / conteo_facturas



        # Agrupar y sumar las cantidades de productos de kits
        df_resultado_kits = df_explosion.groupby(["PRODUCTO_y", "MES", "ORIGEN", 'CATEGORÍA'])["CANTIDAD_PRODUCTO"].sum().reset_index()
        df_resultado_kits.columns = ["PRODUCTO", "MES","ORIGEN",'CATEGORÍA', "CANTIDAD_TOTAL"]

        # # Agrupar por categorias
        # df_resultado_kits_cat = df_explosion.groupby(["PRODUCTO_y", "MES", "ORIGEN",'CATEGORÍA'])["CANTIDAD_PRODUCTO"].sum().reset_index()
        # df_resultado_kits.columns = ["PRODUCTO", "MES","ORIGEN",'CATEGORÍA', "CANTIDAD_TOTAL"]


        # Filtrar productos individuales
        df_ventas_individuales = df_ventas[~df_ventas["PRODUCTO"].str.startswith(("[PCNKIT","[TNGKIT","[B8KIT"))].reset_index(drop=True)
        df_ventas_individuales["ORIGEN"] = "INDIVIDUAL"



        # Seleccionar y renombrar columnas para que coincidan con df_resultado_kits
        df_ventas_individuales = df_ventas_individuales[["PRODUCTO", "MES","ORIGEN" , 'CATEGORÍA', "CANTIDAD", "TOTAL($)"]]
        df_ventas_individuales.columns = ["PRODUCTO", "MES", "ORIGEN", 'CATEGORÍA',"CANTIDAD_TOTAL", "INGRESO_TOTAL"]
        

        # Combinar los resultados de kits y productos individuales
        df_final = pd.concat([df_resultado_kits, df_ventas_individuales], ignore_index=True)


        ## Categoria producto ingresos

        df_ingresos_kits_cate = df_explosion.groupby(["PRODUCTO_y", "MES", "CATEGORÍA"]).agg({
            "CANTIDAD_PRODUCTO": "sum",
            "VALOR_POR_PRODUCTO": "sum"
        }).reset_index()
        df_ingresos_kits_cate.columns = ["PRODUCTO", "MES", "CATEGORÍA", "CANTIDAD_TOTAL", "INGRESO_TOTAL"]
        df_ingresos_cate= pd.concat([df_ingresos_kits_cate, df_ventas_individuales], ignore_index=True)


        ## ingresos

        # Agrupar y sumar las cantidades de productos de kits
        df_ingresos_kits = df_explosion.groupby(["PRODUCTO_y", "MES"]).agg({
            "CANTIDAD_PRODUCTO": "sum",
            "VALOR_POR_PRODUCTO": "sum"
        }).reset_index()
        df_ingresos_kits.columns = ["PRODUCTO", "MES", "CANTIDAD_TOTAL", "INGRESO_TOTAL"]
        df_ingresos= pd.concat([df_ingresos_kits, df_ventas_individuales], ignore_index=True)


        # Lista con el orden correcto de los meses en español
        orden_meses = [
            'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
            'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'
        ]

        # Convertir la columna MES a tipo categoría con orden explícito
        df_final['MES'] = pd.Categorical(df_final['MES'], categories=orden_meses, ordered=True)

        # Ahora ordenar por MES
        df_final = df_final.sort_values(by='MES')

        # Categorias pivot table
        pivot_table_por_mes_categoria = df_final.pivot_table(
            index=["PRODUCTO", "CATEGORÍA"],  # Filas: productos
            columns="MES",     # Columnas: meses
            values="CANTIDAD_TOTAL",  # Valores: cantidades
            aggfunc="sum",     # Función de agregación: suma
            fill_value=0,
            observed=True 
                    # Rellenar valores faltantes con 0
        ).reset_index()



        # Crear la pivot table
        pivot_table_por_mes = df_final.pivot_table(
            index="PRODUCTO",  # Filas: productos
            columns="MES",     # Columnas: meses
            values="CANTIDAD_TOTAL",  # Valores: cantidades
            aggfunc="sum",     # Función de agregación: suma
            fill_value=0,
            observed=True 
                    # Rellenar valores faltantes con 0
        ).reset_index()


        # Crear la pivot table
        pivot_table_mes_origen = df_final.pivot_table(
            index="PRODUCTO",  # Filas: productos
            columns=["MES", "ORIGEN"],  # Columnas: meses y origen (kit o individual)
            values="CANTIDAD_TOTAL",  # Valores: cantidades
            aggfunc="sum",     # Función de agregación: suma
            fill_value=0,
            observed=True      # Rellenar valores faltantes con 0
        ).reset_index()


        # Crear la pivot table con el nuevo formato
        pivot_table_resumida = df_final.pivot_table(
            index=["PRODUCTO", "ORIGEN"],  # Filas: Producto y tipo (Kit o Individual)
            columns="MES",                 # Columnas: Meses
            values="CANTIDAD_TOTAL",        # Valores: Cantidad total
            aggfunc="sum",                  # Sumar cantidades
            fill_value=0,
            observed=True                    # Reemplazar NaN con 0
        ).reset_index()


        # Convertir la columna MES a tipo categoría con orden explícito
        df_ingresos['MES'] = pd.Categorical(df_ingresos['MES'], categories=orden_meses, ordered=True)

        # Ahora ordenar por MES
        df_ingresos = df_ingresos.sort_values(by='MES')


        # Crear la pivot table para las cantidades
        pivot_ingresos_cantidades = df_ingresos.pivot_table(
            index="PRODUCTO",  # Filas: productos
            columns="MES",     # Columnas: meses
            values="CANTIDAD_TOTAL",  # Valores: cantidades
            aggfunc="sum",     # Función de agregación: suma
            fill_value=0,
            observed=True       # Rellenar valores faltantes con 0
        ).reset_index()

        # Crear la pivot table para los ingresos
        pivot_table_ingresos = df_ingresos.pivot_table(
            index="PRODUCTO",  # Filas: productos
            columns="MES",     # Columnas: meses
            values="INGRESO_TOTAL",  # Valores: ingresos
            aggfunc="sum",     # Función de agregación: suma
            fill_value=0, 
            observed=True       # Rellenar valores faltantes con 0
        ).reset_index()


        # Crear la pivot table categorias
        pivot_table_ingresos_cate = df_ingresos.pivot_table(
            index=["PRODUCTO", "CATEGORÍA"],  # Filas: productos
            columns="MES",     # Columnas: meses
            values="INGRESO_TOTAL",  # Valores: cantidades
            aggfunc="sum",     # Función de agregación: suma
            fill_value=0,
            observed=True        # Rellenar valores faltantes con 0
        ).reset_index()

        ruta_file = ruta / 'file' 
        cant_ing_cate = pivot_table_por_mes_categoria.melt(
            id_vars=["PRODUCTO", "CATEGORÍA"],
            var_name="MES", 
            value_name="CANTIDAD_TOTAL"
        )
        ingre_cate=pivot_table_ingresos_cate.melt(
        id_vars=["PRODUCTO", "CATEGORÍA"],
        var_name="MES",
        value_name="INGRESO_TOTAL"
        )
        df_categorias= cant_ing_cate.merge(ingre_cate, on=["PRODUCTO", "CATEGORÍA", "MES"], how="left")


        # Diccionario de meses en español
        meses_es = {
            'enero': 1, 'febrero': 2, 'marzo': 3,
            'abril': 4, 'mayo': 5, 'junio': 6,
            'julio': 7, 'agosto': 8, 'septiembre': 9,
            'octubre': 10, 'noviembre': 11, 'diciembre': 12
        }

        # Convertir los nombres de mes en mayúsculas a minúsculas antes de mapear
        df_categorias['Mes_num'] = df_categorias['MES'].str.lower().map(meses_es)

        # Crear columna de fecha con el día 1 y año 2025
        df_categorias['Fecha'] = pd.to_datetime({
            'year': 2025,
            'month': df_categorias['Mes_num'],
            'day': 1
        })


        try:
            ruta_file.mkdir(parents=True, exist_ok=True)  # Crear la carpeta si no existe
            print(f"Carpeta '{ruta_file}' creada o ya existe.")
            # Guardar la pivot table en un archivo de Excel
            pivot_table_por_mes.to_excel(ruta_file / 'pivot_table_por_mes.xlsx', index=False) ## Es igual a "pivot_table_cantidades_por_mes.xlsx"
            # Guardar la pivot table en un archivo de Excel
            pivot_table_mes_origen.to_excel(ruta_file / "pivot_table_por_mes_y_origen.xlsx")
            # Guardar la pivot table en un archivo de Excel
            pivot_table_resumida.to_excel(ruta_file / "pivot_table_resumida.xlsx", index=False)

            pivot_ingresos_cantidades.to_excel(ruta_file /"pivot_table_cantidades_por_mes.xlsx", index=False)

            pivot_table_ingresos.to_excel(ruta_file / "pivot_table_ingresos_por_mes.xlsx", index=False)

            df_categorias.to_excel(ruta_file / "categorias_df.xlsx", index=False)
            print("Archivos guardados correctamente.")
        except Exception as e:
            print(f"Error al crear la carpeta o guardar los archivos: {e}")


        return {'pivot_table_por_mes':pivot_table_por_mes,
                'pivot_table_mes_origen':pivot_table_mes_origen,
                'pivot_table_resumida':pivot_table_resumida,
                'pivot_ingresos_cantidades':pivot_ingresos_cantidades,
                'pivot_table_ingresos':pivot_table_ingresos,
                }
    
    def pipeline_bi(self):
        """
        Ejecuta el pipeline completo para procesar y transformar la base de ventas,
        consolidar datos, y realizar la explosión de ventas para el BI.

        Returns:
            dict: Diccionario con las bases procesadas, base limpia y explosión de ventas.


        """
        # porcesar base de ventas y notas credito
        ventas_procesadas = self.transformar_base(origen=True)
        ruta = self.validar_ruta()

        ruta_clean = ruta / 'CLEAN DATA' 

        ruta2 = Path(ventas_procesadas['nombre_archivo'])
        ruta_carpeta = ruta_clean / f'VENTAS_{ruta2.stem}.xlsx'
        ruta_errores = ruta / 'file' / 'ventas_sin_categoria.xlsx'
        ruta_padres = ruta / 'data' / 'clientes_padres.xlsx'
        ruta_bgta= ruta / 'data' / 'Base_bogota.xlsx'
        ruta_zonas= ruta / 'data' / 'zonas.xlsx'
        ruta_zonas_cundi = ruta / 'data' / 'zonas_cundinamarca.xlsx'
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

        # df_bogota['DOCUMENTO'] = df_bogota['DOCUMENTO'].astype(str)
        # ventas_procesadas['Base']['IDENTIFICACION_CLIENTE'] = ventas_procesadas['Base']['IDENTIFICACION_CLIENTE'].str.strip()

        ventas_procesadas['Base'] = ventas_procesadas['Base'].merge(df_bogota[['DOCUMENTO', 'CATEGORÍA', 'ZONA']], 
                                      left_on=['IDENTIFICACION_CLIENTE','CATEGORÍA'], right_on=['DOCUMENTO', 'CATEGORÍA'], how='left')

        ventas_procesadas['Base']['ZONA'] = ventas_procesadas['Base']['ZONA'].fillna(ventas_procesadas['Base']['zona'])

        ventas_procesadas['Base'] = ventas_procesadas['Base'].drop(columns=['CLIENTES NUEVOS',	'zona',	'DOCUMENTO', 'ZONA_CUNDINAMARCA'])
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
            ventas_procesadas['Base'].to_excel(ruta_carpeta, index=False)
            with pd.ExcelWriter(ruta_errores, engine='openpyxl') as writer:
                ventas_procesadas['errores'].to_excel(writer, sheet_name='etiqueta a tipo', index=False)
                ventas_procesadas['cliente_call_center'].to_excel(writer, sheet_name='CLIENTE a CALL', index=False)
                # ventas_procesadas['asesores_sin_categoria'].to_excel(writer, sheet_name='Mayoristas sin categoria', index=False)
        except Exception as e:
            print(f"Error al crear la carpeta o guardar los archivos: {e}")
        # Consolidar ventas
        base_clean = self.consolidar_carpeta(ruta_carpeta=ruta_clean)
        ruta_base = ruta / 'file' / 'BASE VENTAS 2025.xlsx'
        import locale
        try:         
            # Intentamos usar el locale en español para obtener "ENERO", "FEBRERO", etc.
            locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
        except locale.Error:
            print("   - Advertencia: Locale 'es_ES.UTF-8' no disponible. Se usarán nombres de mes en inglés.")
            
        base_clean['MES'] = base_clean['FECHA_FACTURA'].dt.strftime('%B').str.upper()
        columnas_finales = [
                "Source.Name", "NUMERO_FACTURA", "FECHA_FACTURA", "AÑO", "MES", "DIA",
                "CLIENTE", "IDENTIFICACION_CLIENTE", "CATEGORÍA", "PRODUCTO", "CANTIDAD",
                "TOTAL", "TASA_CAMBIO", "TRM", "TOTAL($)", "TELEFONO", "EMAIL", "PAIS",
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
            base_clean.to_excel(ruta_base, index=False)
            
        except Exception as e:
            print(f"Error al crear la carpeta o guardar los archivos: {e}")
        explosion = self.explosion_ventas()
        return {'ventas_procesadas':ventas_procesadas,
                'base_clean':base_clean,
                'explosion':explosion
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