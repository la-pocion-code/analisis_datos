"""
consolidador.py
===============
Consolida archivos CSV/Excel de una carpeta, exporta NITs únicos,
carga un archivo adicional, hace merge y genera archivos procesados.

Dependencias:
    pip install pandas openpyxl xlrd
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from pathlib import Path


class ConsolidadorApp:

    EXTENSIONES = ('.csv', '.xlsx', '.xlsm', '.xls')

    def __init__(self, root):
        self.root = root
        self.root.title("Consolidador de Archivos")
        self.root.geometry("640x540")
        self.root.resizable(False, False)

        self.df_consolidado    = None
        self.carpeta_origen    = None
        self.nombres_origen    = []   # stems (sin extensión) de cada archivo leído
        self.columnas_origen   = {}   # stem -> columnas originales del archivo

        self._build_ui()

    # ─────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────
    def _build_ui(self):
        PAD = dict(padx=10, pady=5)

        # ── Configuración ──────────────────────
        frm_cfg = ttk.LabelFrame(self.root, text="Configuración", padding=10)
        frm_cfg.pack(fill='x', **PAD)

        ttk.Label(frm_cfg, text="Separador CSV:").grid(row=0, column=0, sticky='w')
        self.sep = tk.StringVar(value=";")
        ttk.Entry(frm_cfg, textvariable=self.sep, width=5).grid(row=0, column=1, sticky='w', padx=5)

        ttk.Label(frm_cfg, text="Encoding CSV:").grid(row=0, column=2, sticky='w', padx=(20, 0))
        self.enc = tk.StringVar(value="latin1")
        ttk.Entry(frm_cfg, textvariable=self.enc, width=10).grid(row=0, column=3, sticky='w', padx=5)

        ttk.Label(frm_cfg, text="Columna NIT fuente (separa con , si varía):").grid(row=1, column=0, sticky='w', pady=(8, 0))
        self.col_nit = tk.StringVar(value="NIT")
        ttk.Entry(frm_cfg, textvariable=self.col_nit, width=40).grid(row=1, column=1, columnspan=3, sticky='w', padx=5, pady=(8, 0))

        ttk.Label(frm_cfg, text="Columna NIT (archivo adicional):").grid(row=2, column=0, sticky='w', pady=(4, 0))
        self.col_nit_adicional = tk.StringVar(value="NIT")
        ttk.Entry(frm_cfg, textvariable=self.col_nit_adicional, width=25).grid(row=2, column=1, columnspan=3, sticky='w', padx=5, pady=(4, 0))

        # ── Paso 1 ─────────────────────────────
        frm1 = ttk.LabelFrame(self.root, text="Paso 1 — Seleccionar carpeta y consolidar", padding=10)
        frm1.pack(fill='x', **PAD)

        ttk.Button(frm1, text="Seleccionar carpeta", command=self.consolidar).pack(side='left')
        self.lbl1 = ttk.Label(frm1, text="", foreground="gray")
        self.lbl1.pack(side='left', padx=10)

        # ── Paso 2 ─────────────────────────────
        frm2 = ttk.LabelFrame(self.root, text="Paso 2 — Exportar NITs únicos", padding=10)
        frm2.pack(fill='x', **PAD)

        ttk.Button(frm2, text="Exportar NITs a Excel", command=self.exportar_nits).pack(side='left')
        self.lbl2 = ttk.Label(frm2, text="", foreground="gray")
        self.lbl2.pack(side='left', padx=10)

        # ── Paso 3 ─────────────────────────────
        frm3 = ttk.LabelFrame(self.root, text="Paso 3 — Cargar archivo adicional y procesar", padding=10)
        frm3.pack(fill='x', **PAD)

        ttk.Button(frm3, text="Cargar archivo adicional", command=self.cargar_y_procesar).pack(side='left')
        self.lbl3 = ttk.Label(frm3, text="", foreground="gray")
        self.lbl3.pack(side='left', padx=10)

        # ── Log ────────────────────────────────
        frm_log = ttk.LabelFrame(self.root, text="Log", padding=5)
        frm_log.pack(fill='both', expand=True, **PAD)

        self.log_txt = tk.Text(frm_log, height=10, state='disabled',
                               font=('Courier', 9), bg='#1e1e1e', fg='#d4d4d4')
        scroll = ttk.Scrollbar(frm_log, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=scroll.set)
        self.log_txt.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────
    def _log(self, msg: str):
        self.log_txt.configure(state='normal')
        self.log_txt.insert('end', msg + '\n')
        self.log_txt.see('end')
        self.log_txt.configure(state='disabled')
        self.root.update_idletasks()

    def _resolver_col_nit(self, df: pd.DataFrame) -> str | None:
        """Devuelve el primer nombre de columna NIT que exista en el DataFrame.
        El input puede tener varios nombres separados por coma.
        """
        candidatos = [c.strip() for c in self.col_nit.get().split(',') if c.strip()]
        for c in candidatos:
            if c in df.columns:
                return c
        return None

    @staticmethod
    def _normalizar_nit(serie: pd.Series) -> pd.Series:
        """Convierte cualquier tipo de NIT a string limpio.
        Maneja int, float (890123456.0), string y valores nulos.
        """
        def _limpiar(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ''
            s = str(v).strip()
            if s.lower() in ('nan', 'none', '<na>', 'nat', ''):
                return ''
            try:
                # int(float()) resuelve "890123456.0", 890123456.0 y 890123456
                return str(int(float(s)))
            except (ValueError, OverflowError):
                # Si no es numérico puro (ej: "890123456-1"), devolver limpio
                return s.upper()

        return serie.apply(_limpiar)

    def _leer_archivo(self, ruta: str) -> pd.DataFrame:
        ext = Path(ruta).suffix.lower()
        if ext == '.csv':
            try:
                return pd.read_csv(ruta, sep=self.sep.get(), encoding=self.enc.get())
            except UnicodeDecodeError:
                return pd.read_csv(ruta, sep=self.sep.get(), encoding='utf-8')
        if ext in ('.xlsx', '.xlsm', '.xls'):
            return pd.read_excel(ruta)
        return None

    def _verificar_paso1(self) -> bool:
        if self.df_consolidado is None:
            messagebox.showwarning("Atención", "Primero consolida la carpeta (Paso 1).")
            return False
        return True

    # ─────────────────────────────────────────
    # PASO 1 — CONSOLIDAR
    # ─────────────────────────────────────────
    def consolidar(self):
        carpeta = filedialog.askdirectory(title="Seleccionar carpeta con archivos")
        if not carpeta:
            return

        archivos = sorted([
            f for f in os.listdir(carpeta)
            if f.lower().endswith(self.EXTENSIONES)
            and not f.startswith('~')           # ignorar temporales de Excel
        ])

        if not archivos:
            messagebox.showwarning("Sin archivos", "No se encontraron archivos CSV o Excel en la carpeta.")
            return

        self._log(f"\n📂 Carpeta: {carpeta}")
        self._log(f"   {len(archivos)} archivos encontrados\n")

        dfs = []
        self.nombres_origen = []

        for nombre in archivos:
            ruta = os.path.join(carpeta, nombre)
            stem = Path(nombre).stem
            try:
                df = self._leer_archivo(ruta)
                if df is not None and not df.empty:
                    col_encontrada = self._resolver_col_nit(df)
                    if col_encontrada:
                        # Renombrar a columna estándar '__nit_src__' para unificar
                        df = df.rename(columns={col_encontrada: '__nit_src__'})
                        self._log(f"  ✓  {nombre:<45} {len(df):>7,} filas  (NIT: '{col_encontrada}')")
                    else:
                        df['__nit_src__'] = None
                        candidatos = [c.strip() for c in self.col_nit.get().split(',')]
                        self._log(f"  ✓  {nombre:<45} {len(df):>7,} filas")
                        self._log(f"     ⚠  Ninguna columna NIT encontrada {candidatos}")
                        self._log(f"     ℹ  Columnas: {list(df.columns)}")
                    df.insert(0, 'archivo_origen', stem)
                    # Guardar columnas originales (sin archivo_origen) para el export
                    self.columnas_origen[stem] = [c for c in df.columns if c != 'archivo_origen']
                    dfs.append(df)
                    self.nombres_origen.append(stem)
                else:
                    self._log(f"  ⚠  {nombre} — vacío, se omite")
            except Exception as e:
                self._log(f"  ✗  {nombre}: {e}")

        if not dfs:
            messagebox.showerror("Error", "No se pudo leer ningún archivo.")
            return

        self.df_consolidado = pd.concat(dfs, ignore_index=True)
        self.carpeta_origen  = carpeta

        resumen = f"{len(dfs)} archivos | {len(self.df_consolidado):,} filas totales"
        self.lbl1.config(text=f"✓  {resumen}", foreground="green")
        self._log(f"\n✅ Consolidación completada: {resumen}")

    # ─────────────────────────────────────────
    # PASO 2 — EXPORTAR NITs ÚNICOS
    # ─────────────────────────────────────────
    def exportar_nits(self):
        if not self._verificar_paso1():
            return

        ruta = filedialog.asksaveasfilename(
            title="Guardar NITs únicos",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile="nits_unicos.xlsx"
        )
        if not ruta:
            return

        df_nits = (
            self.df_consolidado[['__nit_src__']]
            .assign(__nit_src__=lambda d: self._normalizar_nit(d['__nit_src__']))
            .replace('', pd.NA)
            .drop_duplicates()
            .dropna()
            .sort_values('__nit_src__')
            .reset_index(drop=True)
            .rename(columns={'__nit_src__': 'NIT'})
        )
        df_nits.to_excel(ruta, index=False)

        msg = f"{len(df_nits):,} NITs únicos"
        self.lbl2.config(text=f"✓  {msg}", foreground="green")
        self._log(f"\n📄 NITs exportados → {Path(ruta).name}  ({msg})")

    # ─────────────────────────────────────────
    # PASO 3 — MERGE Y ARCHIVOS PROCESADOS
    # ─────────────────────────────────────────
    def cargar_y_procesar(self):
        if not self._verificar_paso1():
            return

        ruta_adicional = filedialog.askopenfilename(
            title="Seleccionar archivo adicional",
            filetypes=[("Excel / CSV", "*.xlsx *.xls *.csv")]
        )
        if not ruta_adicional:
            return

        try:
            df_adicional = self._leer_archivo(ruta_adicional)
        except Exception as e:
            messagebox.showerror("Error al leer", str(e))
            return

        self._log(f"\n📎 Archivo adicional: {Path(ruta_adicional).name}  —  {len(df_adicional):,} filas")

        col_adicional = self.col_nit_adicional.get().strip()

        if col_adicional not in df_adicional.columns:
            messagebox.showerror("Error", f"Columna '{col_adicional}' no encontrada en el archivo adicional.")
            return

        # ── Clave neutral normalizada para el merge ──────────────────────────
        # __nit_src__ ya fue unificado en el paso de consolidación
        df_base = self.df_consolidado.copy()
        df_adic = df_adicional.copy()

        df_base['__key__'] = self._normalizar_nit(df_base['__nit_src__'])
        df_adic['__key__'] = self._normalizar_nit(df_adic[col_adicional])

        # Quitar la columna NIT original del adicional (ya está en __key__)
        # para evitar duplicado con la columna NIT del fuente
        df_adic = df_adic.drop(columns=[col_adicional], errors='ignore')

        # Renombrar cualquier columna del adicional que ya exista en la base
        # (excepto __key__) para no perder datos de ninguno de los dos lados
        cols_base = set(df_base.columns)
        rename = {
            c: f"{c}_adic"
            for c in df_adic.columns
            if c != '__key__' and c in cols_base
        }
        if rename:
            df_adic = df_adic.rename(columns=rename)
            self._log(f"   Columnas renombradas en adicional: {list(rename.keys())}")

        # ── Diagnóstico ──────────────────────────────────────────────────────
        nits_base = set(df_base['__key__'].replace('', pd.NA).dropna().unique())
        nits_adic = set(df_adic['__key__'].replace('', pd.NA).dropna().unique())
        coinciden = nits_base & nits_adic
        self._log(f"   NITs en fuente:     {len(nits_base):,}")
        self._log(f"   NITs en adicional:  {len(nits_adic):,}")
        self._log(f"   NITs con match:     {len(coinciden):,}")
        if not coinciden:
            self._log("   ⚠  Muestra fuente:    " + str(sorted(nits_base)[:5]))
            self._log("   ⚠  Muestra adicional: " + str(sorted(nits_adic)[:5]))
            messagebox.showwarning(
                "Sin coincidencias",
                "No hay NITs que coincidan entre los dos archivos.\n\n"
                "Revisa el log para comparar los valores de ambas columnas."
            )
            return

        # ── Merge — siempre left para conservar TODAS las filas originales ──
        df_merged = df_base.merge(df_adic, on='__key__', how='left')
        df_merged = df_merged.drop(columns=['__key__'])

        # Verificar que no se perdieron filas
        if len(df_merged) != len(self.df_consolidado):
            self._log(f"   ⚠  Filas originales: {len(self.df_consolidado):,} | Resultado: {len(df_merged):,}")
            self._log("      Hay NITs duplicados en el archivo adicional — se expandieron filas.")
        else:
            self._log(f"   ✓  Filas conservadas: {len(df_merged):,} (igual al original)")

        # Diagnóstico por archivo origen
        col_check = [c for c in df_adic.columns if c != '__key__']
        if col_check:
            self._log("   Match por archivo:")
            for nom in self.nombres_origen:
                mask      = df_merged['archivo_origen'] == nom
                total     = mask.sum()
                con_match = df_merged.loc[mask, col_check[0]].notna().sum()
                self._log(f"     {nom:<45} {con_match:>6,} / {total:,} con match")

        # Pedir carpeta destino (por defecto la de origen, pero editable)
        carpeta_resultado = filedialog.askdirectory(
            title="Seleccionar carpeta donde guardar los archivos procesados",
            initialdir=self.carpeta_origen
        )
        if not carpeta_resultado:
            return
        carpeta_resultado = os.path.join(carpeta_resultado, "resultado")
        os.makedirs(carpeta_resultado, exist_ok=True)
        self._log(f"\n📁 Carpeta resultado: {carpeta_resultado}\n")

        # Columnas que vienen del archivo adicional (no estaban en el consolidado)
        cols_consolidado  = set(self.df_consolidado.columns)
        cols_adicionales  = [c for c in df_merged.columns if c not in cols_consolidado and c != '__key__']

        creados = 0
        for nombre in self.nombres_origen:
            df_parte = df_merged[df_merged['archivo_origen'] == nombre].copy()

            # Solo columnas propias del archivo + las nuevas del adicional
            cols_propias = self.columnas_origen.get(nombre, [])
            cols_finales = cols_propias + [c for c in cols_adicionales if c not in cols_propias]
            cols_finales = [c for c in cols_finales if c in df_parte.columns]
            df_parte = df_parte[cols_finales]

            nombre_salida = f"{nombre}_procesado.xlsx"
            ruta_salida   = os.path.join(carpeta_resultado, nombre_salida)

            try:
                df_parte.to_excel(ruta_salida, index=False)
                creados += 1
                self._log(f"  ✓  {nombre_salida:<55} {len(df_parte):>7,} filas")
            except Exception as e:
                self._log(f"  ✗  {nombre_salida}: {e}")

        msg = f"{creados} archivos creados en /resultado"
        self.lbl3.config(text=f"✓  {msg}", foreground="green")
        self._log(f"\n✅ Proceso terminado. {msg}")
        messagebox.showinfo("¡Listo!", f"Proceso completado.\n\n{msg}\n\nCarpeta:\n{carpeta_resultado}")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    ConsolidadorApp(root)
    root.mainloop()
