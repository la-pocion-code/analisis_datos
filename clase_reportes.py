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



class ReportClass():
    """
    Esta clase contiene las funciones que su utilizan para actulizar el bi
    y las ventas procesadas.    
    """





    def consolidar_carpeta(self, extension='xlsx', sheet_name=None, ruta_carpeta=None):
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
                        df = pd.read_csv(ruta_completa)
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

        # Paso 5: Extraer el número de factura de la columna "Referencia" en las notas crédito
        df_notas_credito['NUMERO_FACTURA'] = df_notas_credito['Líneas de factura/Referencia'].apply(
            lambda x: re.search(r'(FEVY\d+)', x).group(1) if pd.notna(x) and re.search(r'(FEVY\d+)', x) else None
        )
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
            'Líneas de factura/Moneda/Tasa actual': 'first',
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

        # ruta = self.validar_ruta()
        # Paso 18: Guardar el listado de facturas afectadas en un archivo Excel (opcional)
       
        try:
            nombre_archivo_facturas_afectadas = ruta / 'RAW DATA' / 'FACTURAS AFECTADAS' / f'{nombre_archivo}_facturas_afectadas.xlsx' 
            facturas_afectadas.to_excel(nombre_archivo_facturas_afectadas, index=False)
            print(f"Listado de facturas afectadas guardado como '{nombre_archivo_facturas_afectadas}'.")
        except Exception as e:
            print(f"Error al guardar el listado de facturas afectadas: {e}")

        return {'archivo_salida': df_consolidado,
                'nombre_archivo':ruta_salida,
                'facturas_afectadas' :facturas_afectadas}
    
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

        # Extraer el código de país y reemplazar NaN con "Desconocido" en un solo paso
        df['pais'] = df['Líneas de factura/Asociado/Estado'].str.extract(r'\(([A-Z]{2})\)').fillna("Desconocido")

        # Crear la columna 'total' con la lógica especificada
        df['Líneas de factura/Asociado/Ciudad'] = df['Líneas de factura/Asociado/Ciudad'].astype(str).fillna("Desconocido")


        # # Paso 4: Eliminar registros que no corresponden a ventas de la poción
        # productos_a_eliminar = [
        #     '[FLETEGRAV19] FLETE GRAVADO IVA 19% (en ventas)',
        #     '[ALOJAMIENTO] SERV. ALOJAMIENTO',
        #     '[reintegro] Reintegro de costos y gastos',
        #     '[BKRAFT4] Bolsa de Papel Kraft Boutique 4',
        #     '[PLEGP] CAJAS PLEGADIZAS POCION',
        #     '[SCHT05] SACHET MSC ANCESTRAL 15 ML',
        #     '[MAE20] Laminado Sachet Shampoo La Pocion 60ml',
        #     '[SERVICIO ALOJAMIENTO EXTERIOR] SERVICIO ALOJAMIENTO EXTERIOR',
        #     '[ACTIVOF] VENTA ACTIVO FIJO',
        #     '[EXP01] FLETE INTERNACIONAL EXP',
        #     '[EXP02] SEGURO INTERNACIONAL EXP',
        #     '[MPE02] ENVASE PET MILK X 440 ML',
        #     '[FLETE NG] FLETE NG',
        #     '[EXP02] SEGURO INTERNACIONAL EXP',
        #     '[EXP01] FLETE INTERNACIONAL EXP',
        #     '[EXP01] Flete Internacional EXP',
        #     '[EXP02] Seguro Internacional EXP',
        #     '[MPE02] ENVASE PET MILK X 440 ML',
        #     '[ARRENDAMIENTO INMUEBLE GRAVADO 19%] ARRENDAMIENTO INMUEBLE GRAVADO 19%' ## preguntar si se elimina
            
        # ]


        # df_filtrado = df[~df['Líneas de factura/Producto'].isin(productos_a_eliminar)]


        # Esta linea mantiene solo los pruductos comerciales
        df_filtrado = df[df['Líneas de factura/Producto'].str.startswith(('[PCN','[KD','[TNG','[B8'))]   ###### linea modificada
        # Paso 6: Mostrar resumen
      
        # '''# Paso 1: Agrupar por 'Líneas de factura/Número' y propagar el valor de 'Equipo de Ventas' hacia abajo
        # df_filtrado['Equipo de Ventas'] = df_filtrado.groupby('Líneas de factura/Número')['Equipo de Ventas'].transform(lambda x: x.ffill())
        # # Crear una copia explícita del DataFrame
        # df_filtrado = df_filtrado.copy()

                # Guarda en la variables las ventas sin tipo de cliente y con etiqueta mayorista
        etiqueta_mayorista = df_filtrado[(df_filtrado['Tipo de cliente'].isna())&
                    (df_filtrado['Etiqueta contacto']=='MAYORISTA NV')
                    ] 
        # Copia de la etiqueta los clientes mayoristas que aparecen en blanco
        df_filtrado.loc[(df_filtrado['Tipo de cliente'].isna())&
                    (df_filtrado['Etiqueta contacto']=='MAYORISTA NV'), 'Tipo de cliente'
                    ] = 'MAYORISTA NV'

        # # Paso 2: Verificar el resultado
        # print(df_filtrado[['Líneas de factura/Número', 'Equipo de Ventas']].head(20))  # Mostrar las primeras 20 filas para verificar'''
        equipo_por_factura = df_filtrado.groupby('Líneas de factura/Número')['Equipo de Ventas'].first().to_dict()

        # Ahora, rellenamos los valores en la columna EQUIPO_VENTAS
        df_filtrado.loc[:,'Equipo de Ventas'] = df['Líneas de factura/Número'].map(equipo_por_factura)

        asesora_por_factura = df_filtrado.groupby('Líneas de factura/Número')['Asesor Comercial'].first().to_dict()

        # Ahora, rellenamos los valores en la columna EQUIPO_VENTAS
        # df_filtrado['Asesor Comercial'] = df['Líneas de factura/Número'].map(asesora_por_factura)
        df_filtrado.loc[:, 'Asesor Comercial'] = df['Líneas de factura/Número'].map(asesora_por_factura)

        asesora_por_factura = df_filtrado.groupby('Líneas de factura/Número')['Tipo de cliente'].first().to_dict()

        # Ahora, rellenamos los valores en la columna EQUIPO_VENTAS
        # df_filtrado['Asesor Comercial'] = df['Líneas de factura/Número'].map(asesora_por_factura)
        df_filtrado.loc[:, 'Tipo de cliente'] = df['Líneas de factura/Número'].map(asesora_por_factura)



   
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

        # df_TRM = pd.read_excel(r"C:\Users\Dataa\Desktop\VENTAS\VENTA MENSUAL\TRM.xlsx")

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
            
   

        # # Limpiar y convertir la columna 'TRM'
        # df_resultado['TRM'] = (
        #     df_resultado['TRM']
        #     .str.replace('.', '', regex=False)  # Eliminar puntos (separadores de miles)
        #     .str.replace(',', '.', regex=False)  # Reemplazar comas por puntos (separadores decimales)
        #     .astype(float)  # Convertir a tipo numérico
        # )

        # Verificar los valores únicos después de la conversión
 
        # Crear la columna `total`
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
            'Líneas de factura/Moneda/Tasa actual': 'Tasa_Cambio',
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
                        'Total', 'Tasa_Cambio','TRM', 'Total($)','Telefono', 'Email','Pais','Ciudad', 'Ciudad_Corregida', 'Departamento', 
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


        # # 1. Cargar el dataset BD_CLIENTES
        # BD_CLIENTES = pd.read_excel(r"C:\Users\Dataa\Desktop\VENTAS\VENTA MENSUAL\BD_CLIENTES.xlsx")

        # 2. Limpieza de la columna de identificación en ambos DataFrames
        # Limpieza en tu DataFrame actual
        df_resultado['IDENTIFICACION_CLIENTE'] = (
            df_resultado['IDENTIFICACION_CLIENTE']
            .astype(str)  # Convertir a string
            .str.strip()  # Eliminar espacios al principio y al final
            .str.replace(r'\s+', '', regex=True)  # Eliminar espacios adicionales entre caracteres
        )

        df_resultado['IDENTIFICACION_CLIENTE'] = df_resultado['IDENTIFICACION_CLIENTE'].apply(self.limpiar_documento)

        # # Limpieza en BD_CLIENTES
        # BD_CLIENTES['Número de Identificación'] = (
        #     BD_CLIENTES['Número de Identificación']
        #     .astype(str)  # Convertir a string
        #     .str.strip()  # Eliminar espacios al principio y al final
        #     .str.replace(r'\s+', '', regex=True)  # Eliminar espacios adicionales entre caracteres
        # )
        # print(f"- Registros originales: {len(df_resultado)}")
        # duplicados = BD_CLIENTES['Número de Identificación'].duplicated(keep=False)
        # print(BD_CLIENTES[duplicados])
        # BD_CLIENTES = BD_CLIENTES.drop_duplicates(subset=['Número de Identificación'], keep='first')
        # print(f"Registros originales: {len(df_resultado)}")



        # df_resultado = pd.merge(
        #     df_resultado,
        #     BD_CLIENTES[['Número de Identificación', 'Etiquetas']],
        #     left_on='IDENTIFICACION_CLIENTE',
        #     right_on='Número de Identificación',
        #     how='left'
        # )
        # print(f"Registros después del merge: {len(df_resultado)}")

    

        # # 3. Renombrar la columna "Etiquetas" a "Categoría"
        # df_resultado.rename(columns={'Etiquetas': 'CATEGORÍA'}, inplace=True)

        # 4. Ubicar la columna "Categoría" antes de "Producto"
        # Primero, obtenemos la lista de columnas
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

        
        # Agrgrer varificacion de vendedores mayoristas como asesor comercial
        asesores_moyorista = df_resultado[df_resultado['TIPO DE CLIENTE'] == 'MAYORISTA NV']['ASESOR COMERCIAL'].drop_duplicates().tolist()
        asesores_moyorista = [a for a in asesores_moyorista if a is not None]
        asesores_sin_categoria = df_resultado[(df_resultado['TIPO DE CLIENTE'].isna())&(df_resultado['ASESOR COMERCIAL'].isin(asesores_moyorista))]
        df_resultado.loc[(df_resultado['TIPO DE CLIENTE'].isna())&(df_resultado['ASESOR COMERCIAL'].isin(asesores_moyorista)), 'TIPO DE CLIENTE'] = 'MAYORISTA NV'




        # Mostrar las primeras filas para verificar los cambios
    
        # Rellenar los valores vacíos en "Categoría" con "Call center"
        df_resultado['TIPO DE CLIENTE'] =df_resultado['TIPO DE CLIENTE'].fillna('CALL CENTER')   ### REVISAR
    

        # 9. Eliminar las columnas "REFERENCIA" y "Número de Identificación"
        # df_resultado.drop(columns=['Número de Identificación'], inplace=True)
        # print(f"- Registros originales: {len(df_resultado)}")

        # df_final = df_resultado[df_resultado["PRODUCTO"] != "[MPE02] ENVASE PET MILK X 440 ML"]

        # # Guardar el DataFrame en un archivo Excel
        # df_final.to_excel(f"VENTAS_{nombre_archivo}", index=False)

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
                'asesores_sin_categoria':asesores_sin_categoria,
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

        
        ruta_bgta= ruta / 'data' / 'Base_bogota.xlsx'
        ruta_zonas= ruta / 'data' / 'zonas.xlsx'
        df_bogota= pd.read_excel(ruta_bgta)
        zonas = pd.read_excel(ruta_zonas)

        ventas_procesadas['Base'] = ventas_procesadas['Base'].merge(zonas, on=['DEPARTAMENTO', 'CATEGORÍA'], how='left')
        df_bogota['DOCUMENTO'] = df_bogota['DOCUMENTO'].astype(str)
        ventas_procesadas['Base']['IDENTIFICACION_CLIENTE'] = ventas_procesadas['Base']['IDENTIFICACION_CLIENTE'].str.strip()
        ventas_procesadas['Base'] = ventas_procesadas['Base'].merge(df_bogota[['DOCUMENTO', 'CATEGORÍA', 'ZONA']], 
                                      left_on=['IDENTIFICACION_CLIENTE','CATEGORÍA'], right_on=['DOCUMENTO', 'CATEGORÍA'], how='left')

        ventas_procesadas['Base']['ZONA'] = ventas_procesadas['Base']['ZONA'].fillna(ventas_procesadas['Base']['zona'])

        ventas_procesadas['Base'] = ventas_procesadas['Base'].drop(columns=['CLIENTES NUEVOS',	'zona',	'DOCUMENTO'])

        try:
            ruta_clean.mkdir(parents=True, exist_ok=True)  # Crear la carpeta si no existe
            print(f"Carpeta '{ruta_clean}' creada o ya existe.")
            ventas_procesadas['Base'].to_excel(ruta_carpeta, index=False)
            with pd.ExcelWriter(ruta_errores, engine='openpyxl') as writer:
                ventas_procesadas['errores'].to_excel(writer, sheet_name='etiqueta a tipo', index=False)
                ventas_procesadas['cliente_call_center'].to_excel(writer, sheet_name='CLIENTE a CALL', index=False)
                ventas_procesadas['asesores_sin_categoria'].to_excel(writer, sheet_name='Mayoristas sin categoria', index=False)
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
                "CIUDAD", "CIUDAD_CORREGIDA", "DEPARTAMENTO", "EQUIPO_VENTAS", "REFERENCIA"
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

        # ruta_bgta= ruta / 'data' / 'Base_bogota.xlsx'
        # ruta_zonas= ruta / 'data' / 'zonas.xlsx'
        # df_bogota= pd.read_excel(ruta_bgta)
        # zonas = pd.read_excel(ruta_zonas)

        # base_clean = base_clean.merge(zonas, on=['DEPARTAMENTO', 'CATEGORÍA'], how='left')
        # df_bogota['DOCUMENTO'] = df_bogota['DOCUMENTO'].astype(str)
        # base_clean['IDENTIFICACION_CLIENTE'] = base_clean['IDENTIFICACION_CLIENTE'].str.strip()
        # base_clean = base_clean.merge(df_bogota[['DOCUMENTO', 'CATEGORÍA', 'ZONA']], 
        #                               left_on=['IDENTIFICACION_CLIENTE','CATEGORÍA'], right_on=['DOCUMENTO', 'CATEGORÍA'], how='left')

        # base_clean['ZONA'] = base_clean['ZONA'].fillna(base_clean['zona'])


        # base_clean = base_clean[['NUMERO_FACTURA', 'FECHA_FACTURA', 'AÑO', 'MES', 'DIA', 'CLIENTE',
        #     'IDENTIFICACION_CLIENTE', 'CATEGORÍA', 'PRODUCTO', 'CANTIDAD', 'TOTAL',
        #     'TASA_CAMBIO', 'TRM', 'TOTAL($)', 'TELEFONO', 'EMAIL', 'PAIS', 'CIUDAD',
        #     'CIUDAD_CORREGIDA', 'DEPARTAMENTO', 'EQUIPO_VENTAS', 'REFERENCIA',
        #     'ASESOR COMERCIAL', 'ZONA']]
        
     

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