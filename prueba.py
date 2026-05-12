"""
=============================================================================
  CONCILIACIÓN ECOMMERCE - Shopify vs ADDI / MercadoPago / PayU / Odoo
  Desarrollado para usuarios no técnicos
  Compatible con PyInstaller --onefile --noconsole
=============================================================================
"""

import sys
import os
import threading
import traceback
import zipfile
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
#  PALETA DE COLORES Y ESTILOS
# ─────────────────────────────────────────────────────────────────────────────
COLORS = {
    "bg":           "#0F1117",   # fondo principal oscuro
    "surface":      "#1A1D27",   # tarjetas / paneles
    "surface2":     "#22263A",   # inputs / filas alternas
    "accent":       "#4F8EF7",   # azul brillante
    "accent2":      "#3DCFB6",   # verde-teal para éxito
    "warning":      "#F7A74F",   # naranja advertencia
    "danger":       "#F75F5F",   # rojo error
    "text":         "#E8ECF4",   # texto principal
    "text_dim":     "#6B7280",   # texto secundario
    "border":       "#2D3148",   # bordes
    "optional":     "#A78BFA",   # violeta para opcionales
}

FONT_TITLE  = ("Segoe UI", 18, "bold")
FONT_SUB    = ("Segoe UI", 11, "bold")
FONT_BODY   = ("Segoe UI", 10)
FONT_SMALL  = ("Segoe UI", 9)
FONT_MONO   = ("Consolas", 9)


# ─────────────────────────────────────────────────────────────────────────────
#  LÓGICA DE PROCESAMIENTO  (separada de la interfaz)
# ─────────────────────────────────────────────────────────────────────────────

def consolidar_archivos(archivos: list[str], extension: str, sep: str = ",") -> pd.DataFrame:
    """Lee y concatena una lista de archivos CSV o XLSX en un único DataFrame."""
    dfs = []
    for ruta in archivos:
        ruta = Path(ruta)
        if not ruta.exists():
            raise FileNotFoundError(f"No se encontró el archivo: {ruta}")
        try:
            if extension.lower() in ("csv",):
                df = pd.read_csv(ruta, sep=sep, low_memory=False, encoding="utf-8-sig")
            else:
                df = pd.read_excel(ruta, engine="openpyxl")
            dfs.append(df)
        except Exception as e:
            raise ValueError(f"Error leyendo '{ruta.name}': {e}")

    if not dfs:
        raise ValueError("No se pudieron leer archivos válidos.")
    return pd.concat(dfs, ignore_index=True)


def cargar_addi(archivos_addi: list[str]) -> pd.DataFrame:
    """
    Carga archivos ADDI: detecta ZIPs (hoja 'Transacciones + cancelaciones')
    o CSVs directos.
    """
    dfs = []
    zips  = [f for f in archivos_addi if f.lower().endswith(".zip")]
    csvs  = [f for f in archivos_addi if f.lower().endswith(".csv")]

    if zips:
        for archivo_zip in zips:
            if not zipfile.is_zipfile(archivo_zip):
                continue
            with zipfile.ZipFile(archivo_zip, "r") as z:
                internos = [
                    f for f in z.namelist()
                    if "pagos/" in f.lower() and "resumen general.xlsx" in f.lower()
                ]
                if not internos:
                    continue
                with z.open(internos[0]) as excel_file:
                    df = pd.read_excel(
                        excel_file,
                        sheet_name="Transacciones + cancelaciones",
                        engine="openpyxl",
                    )
                    df = df[~df[df.columns[1]].isna()]
                    df.columns = df.iloc[0].values
                    df = df.iloc[1:].reset_index(drop=True)
                    dfs.append(df)
    elif csvs:
        for f in csvs:
            df = pd.read_csv(f, low_memory=False, encoding="utf-8-sig")
            dfs.append(df)

    if not dfs:
        raise ValueError("No se encontraron datos válidos de ADDI.")
    return pd.concat(dfs, ignore_index=True)


def validar_columna(df: pd.DataFrame, columna: str, nombre_archivo: str):
    """Lanza error claro si una columna requerida no existe."""
    if columna not in df.columns:
        cols = ", ".join(df.columns[:8])
        raise KeyError(
            f"No se encontró la columna '{columna}' en {nombre_archivo}.\n"
            f"Columnas disponibles: {cols} ..."
        )


def procesar_conciliacion(
    archivos_shopify: list[str],
    archivos_mercadopago: list[str],
    archivos_addi: list[str],
    archivos_payu: list[str],
    archivos_odoo: list[str],
    carpeta_salida: str,
    log_callback,          # función para enviar mensajes al log de la UI
) -> dict:
    """
    Función principal de procesamiento.
    Retorna un dict con resultados por pasarela.
    """

    salida = Path(carpeta_salida)
    salida.mkdir(parents=True, exist_ok=True)
    resultados = {}
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── 1. Cargar Shopify ──────────────────────────────────────────────────
    log_callback("📂 Cargando datos de Shopify...")
    ventas_shopi = consolidar_archivos(archivos_shopify, extension="csv")

    # Limpiar y preparar Shopify
    if "Created at" in ventas_shopi.columns:
        ventas_shopi["Created at"] = pd.to_datetime(
            ventas_shopi["Created at"], errors="coerce"
        )
        ventas_shopi.sort_values("Created at", inplace=True, ascending=True)
        ventas_shopi["Created at"] = ventas_shopi["Created at"].dt.date

    if "Payment ID" in ventas_shopi.columns:
        ventas_shopi = ventas_shopi[ventas_shopi["Payment ID"].notna()]

    if "Payment Reference" in ventas_shopi.columns:
        ventas_shopi["Payment Reference"] = (
            ventas_shopi["Payment Reference"].astype(str).str.strip()
        )

    log_callback(f"   ✔ Shopify: {len(ventas_shopi):,} registros cargados.")

    # ── 2. Merge con Odoo (opcional) ──────────────────────────────────────
    if archivos_odoo:
        log_callback("📂 Cargando datos de Odoo...")
        ventas_odoo = consolidar_archivos(archivos_odoo, extension="csv", sep=";")
        if {"NUMERO_FACTURA", "REFERENCIA"}.issubset(ventas_odoo.columns):
            ventas_shopi = ventas_shopi.merge(
                ventas_odoo[["NUMERO_FACTURA", "REFERENCIA"]],
                left_on="Name",
                right_on="REFERENCIA",
                how="left",
            )
            log_callback(f"   ✔ Odoo: merge completado ({len(ventas_odoo):,} registros).")
        else:
            log_callback("   ⚠ Odoo: columnas 'NUMERO_FACTURA'/'REFERENCIA' no encontradas — se omite.")

    # ── 3. Conciliación MercadoPago ────────────────────────────────────────
    if archivos_mercadopago:
        log_callback("💳 Procesando MercadoPago...")
        mp = consolidar_archivos(archivos_mercadopago, extension="xlsx")

        col_ref_mp  = "Código de referencia (external_reference)"
        col_monto_mp = "Valor del producto (transaction_amount)"
        col_estado_mp = "Estado de la operación (status)"

        if col_ref_mp in mp.columns and "Payment Reference" in ventas_shopi.columns:
            mp = mp.merge(
                ventas_shopi[["Payment Reference", "Total", "Shipping"]].dropna(
                    subset=["Payment Reference"]
                ),
                left_on=col_ref_mp,
                right_on="Payment Reference",
                how="left",
            )

        if col_monto_mp in mp.columns and "Total" in mp.columns:
            mp["Validacion"] = np.where(
                mp["Total"] == mp[col_monto_mp], "Conciliado",
                np.where(
                    ~mp.get(col_estado_mp, pd.Series(dtype=str)).isin(["approved"]),
                    "Rechazada/Cancelada",
                    np.where(mp["Total"].isna(), "No está en Shopify", "No Conciliado"),
                ),
            )
        else:
            mp["Validacion"] = "No Conciliado (columnas faltantes)"

        ruta_mp = salida / f"conciliado_mercadopago_{timestamp}.xlsx"
        mp.to_excel(ruta_mp, index=False)
        resultados["MercadoPago"] = {"archivo": ruta_mp, "registros": len(mp)}
        conciliados = (mp["Validacion"] == "Conciliado").sum()
        log_callback(
            f"   ✔ MercadoPago: {len(mp):,} registros | "
            f"{conciliados:,} conciliados → {ruta_mp.name}"
        )

    # ── 4. Conciliación ADDI ──────────────────────────────────────────────
    if archivos_addi:
        log_callback("💳 Procesando ADDI...")
        addi = cargar_addi(archivos_addi)

        # Detectar columna clave de ADDI (puede variar)
        col_orden_addi = next(
            (c for c in addi.columns if "orden" in c.lower() or "id orden" in c.lower()),
            None,
        )

        if col_orden_addi and "Payment Reference" in ventas_shopi.columns:
            addi[col_orden_addi] = addi[col_orden_addi].astype(str).str.strip()
            addi = addi.merge(
                ventas_shopi[["Payment Reference", "Total", "Shipping"]].dropna(
                    subset=["Payment Reference"]
                ),
                left_on=col_orden_addi,
                right_on="Payment Reference",
                how="left",
            )

        # Columna de monto ADDI
        col_monto_addi = next(
            (c for c in addi.columns if "monto" in c.lower() or "total después" in c.lower()),
            None,
        )
        col_estado_addi = next(
            (c for c in addi.columns if "estado" in c.lower()),
            None,
        )

        if col_monto_addi and "Total" in addi.columns:
            addi["Validacion"] = np.where(
                addi["Total"] == addi[col_monto_addi], "Conciliado",
                np.where(
                    addi.get(col_estado_addi, pd.Series(dtype=str)).isin(
                        ["Cancelada", "Abandono", "Rechazada"]
                    ),
                    "Rechazada/Cancelada",
                    np.where(addi["Total"].isna(), "No está en Shopify", "No Conciliado"),
                ),
            )
        else:
            addi["Validacion"] = "No Conciliado (columnas faltantes)"

        ruta_addi = salida / f"conciliado_addi_{timestamp}.xlsx"
        addi.to_excel(ruta_addi, index=False)
        resultados["ADDI"] = {"archivo": ruta_addi, "registros": len(addi)}
        conciliados = (addi["Validacion"] == "Conciliado").sum()
        log_callback(
            f"   ✔ ADDI: {len(addi):,} registros | "
            f"{conciliados:,} conciliados → {ruta_addi.name}"
        )

    # ── 5. Conciliación PayU ──────────────────────────────────────────────
    if archivos_payu:
        log_callback("💳 Procesando PayU...")
        payu = consolidar_archivos(archivos_payu, extension="csv")

        if "Referencia" in payu.columns and "Payment Reference" in ventas_shopi.columns:
            payu = payu.merge(
                ventas_shopi[["Payment Reference", "Total", "Shipping"]].dropna(
                    subset=["Payment Reference"]
                ),
                left_on="Referencia",
                right_on="Payment Reference",
                how="left",
            )

        col_monto_payu  = "Valor procesado"
        col_estado_payu = "Estado de transacción"

        if col_monto_payu in payu.columns and "Total" in payu.columns:
            payu["Validacion"] = np.where(
                payu["Total"] == payu[col_monto_payu], "Conciliado",
                np.where(
                    ~payu.get(col_estado_payu, pd.Series(dtype=str)).isin(["approved"]),
                    "Rechazada/Cancelada",
                    np.where(payu["Total"].isna(), "No está en Shopify", "No Conciliado"),
                ),
            )
        else:
            payu["Validacion"] = "No Conciliado (columnas faltantes)"

        ruta_payu = salida / f"conciliado_payu_{timestamp}.xlsx"
        payu.to_excel(ruta_payu, index=False)
        resultados["PayU"] = {"archivo": ruta_payu, "registros": len(payu)}
        conciliados = (payu["Validacion"] == "Conciliado").sum()
        log_callback(
            f"   ✔ PayU: {len(payu):,} registros | "
            f"{conciliados:,} conciliados → {ruta_payu.name}"
        )

    if not resultados:
        raise ValueError(
            "No se seleccionó ningún archivo de pasarela (MercadoPago, ADDI o PayU)."
        )

    log_callback("✅ Proceso completado exitosamente.")
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
#  COMPONENTES DE UI PERSONALIZADOS
# ─────────────────────────────────────────────────────────────────────────────

class FileSelector(tk.Frame):
    """
    Widget reutilizable para seleccionar uno o múltiples archivos.
    Muestra una etiqueta descriptiva, un botón y la lista de archivos elegidos.
    """

    def __init__(
        self,
        parent,
        label: str,
        filetypes: list,
        multiple: bool = True,
        required: bool = False,
        accent_color: str = COLORS["accent"],
        **kwargs,
    ):
        super().__init__(parent, bg=COLORS["surface"], **kwargs)
        self.filetypes  = filetypes
        self.multiple   = multiple
        self.required   = required
        self.accent     = accent_color
        self._archivos  = []

        # ── Encabezado ────────────────────────────────────────────────────
        header = tk.Frame(self, bg=COLORS["surface"])
        header.pack(fill="x", padx=12, pady=(10, 4))

        badge_text = "  OBLIGATORIO  " if required else "  OPCIONAL  "
        badge_color = COLORS["danger"] if required else COLORS["optional"]
        tk.Label(
            header,
            text=badge_text,
            bg=badge_color,
            fg="#FFFFFF",
            font=("Segoe UI", 7, "bold"),
            padx=4, pady=2,
        ).pack(side="left")

        tk.Label(
            header,
            text=f"  {label}",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=FONT_SUB,
        ).pack(side="left")

        btn = tk.Button(
            header,
            text="＋ Seleccionar",
            command=self._seleccionar,
            bg=self.accent,
            fg="#FFFFFF",
            font=FONT_SMALL,
            relief="flat",
            cursor="hand2",
            padx=10, pady=3,
            activebackground=COLORS["surface2"],
            activeforeground=COLORS["text"],
        )
        btn.pack(side="right")

        # ── Lista de archivos seleccionados ───────────────────────────────
        self.lista_frame = tk.Frame(self, bg=COLORS["surface2"])
        self.lista_frame.pack(fill="x", padx=12, pady=(0, 10))

        self._placeholder = tk.Label(
            self.lista_frame,
            text="Ningún archivo seleccionado",
            bg=COLORS["surface2"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
            pady=6,
        )
        self._placeholder.pack()

    def _seleccionar(self):
        if self.multiple:
            rutas = filedialog.askopenfilenames(filetypes=self.filetypes)
            if rutas:
                self._archivos = list(rutas)
        else:
            ruta = filedialog.askopenfilename(filetypes=self.filetypes)
            if ruta:
                self._archivos = [ruta]
        self._actualizar_lista()

    def _actualizar_lista(self):
        for widget in self.lista_frame.winfo_children():
            widget.destroy()

        if not self._archivos:
            tk.Label(
                self.lista_frame,
                text="Ningún archivo seleccionado",
                bg=COLORS["surface2"],
                fg=COLORS["text_dim"],
                font=FONT_SMALL,
                pady=6,
            ).pack()
            return

        for ruta in self._archivos:
            nombre = Path(ruta).name
            fila = tk.Frame(self.lista_frame, bg=COLORS["surface2"])
            fila.pack(fill="x", padx=4, pady=1)
            tk.Label(
                fila,
                text="📄",
                bg=COLORS["surface2"],
                fg=self.accent,
                font=FONT_SMALL,
            ).pack(side="left")
            tk.Label(
                fila,
                text=nombre,
                bg=COLORS["surface2"],
                fg=COLORS["text"],
                font=FONT_MONO,
                anchor="w",
            ).pack(side="left", padx=4)
            tk.Button(
                fila,
                text="✕",
                command=lambda r=ruta: self._quitar(r),
                bg=COLORS["surface2"],
                fg=COLORS["danger"],
                font=("Segoe UI", 8),
                relief="flat",
                cursor="hand2",
            ).pack(side="right")

    def _quitar(self, ruta: str):
        self._archivos = [a for a in self._archivos if a != ruta]
        self._actualizar_lista()

    @property
    def archivos(self) -> list[str]:
        return self._archivos

    def limpiar(self):
        self._archivos = []
        self._actualizar_lista()


class LogPanel(tk.Frame):
    """Panel de log con texto desplazable."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=COLORS["bg"], **kwargs)

        tk.Label(
            self,
            text="📋  Registro de actividad",
            bg=COLORS["bg"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
        ).pack(anchor="w", padx=4, pady=(6, 2))

        self.text = tk.Text(
            self,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=FONT_MONO,
            relief="flat",
            bd=0,
            wrap="word",
            state="disabled",
            height=10,
        )
        scrollbar = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True, padx=(4, 0))

        # Tags de color para distintos tipos de mensaje
        self.text.tag_config("info",    foreground=COLORS["text"])
        self.text.tag_config("success", foreground=COLORS["accent2"])
        self.text.tag_config("warning", foreground=COLORS["warning"])
        self.text.tag_config("error",   foreground=COLORS["danger"])

    def log(self, mensaje: str, tipo: str = "info"):
        self.text.configure(state="normal")
        hora = datetime.now().strftime("%H:%M:%S")
        self.text.insert("end", f"[{hora}]  {mensaje}\n", tipo)
        self.text.see("end")
        self.text.configure(state="disabled")

    def limpiar(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────────────
#  VENTANA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

class AppConciliacion(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Conciliación Ecommerce · La Poción")
        self.configure(bg=COLORS["bg"])
        self.geometry("860x900")
        self.minsize(700, 700)
        self.resizable(True, True)

        # Centrar ventana
        self.after(10, self._centrar)

        # Variables de estado
        self._carpeta_salida = tk.StringVar(value="")
        self._procesando     = False

        self._construir_ui()

    def _centrar(self):
        w, h = 860, 900
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Construcción de la interfaz ────────────────────────────────────────

    def _construir_ui(self):
        # ── Encabezado ────────────────────────────────────────────────────
        header = tk.Frame(self, bg=COLORS["accent"], height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="  🛒  Conciliación Ecommerce",
            bg=COLORS["accent"],
            fg="#FFFFFF",
            font=FONT_TITLE,
        ).pack(side="left", padx=16)

        tk.Label(
            header,
            text="Shopify  ×  ADDI  ×  MercadoPago  ×  PayU",
            bg=COLORS["accent"],
            fg="rgba(255,255,255,0.7)" if False else "#C8DEFF",
            font=FONT_SMALL,
        ).pack(side="right", padx=16)

        # ── Contenedor scrollable ─────────────────────────────────────────
        canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._contenedor = tk.Frame(canvas, bg=COLORS["bg"])
        self._win_id = canvas.create_window((0, 0), window=self._contenedor, anchor="nw")

        self._contenedor.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        ))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(
            self._win_id, width=e.width
        ))

        # Scroll con rueda del mouse
        self.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-1 * (e.delta / 120)), "units"
        ))

        body = self._contenedor

        # ── Sección: Archivos de entrada ──────────────────────────────────
        self._seccion(body, "1  —  Archivos de entrada")

        CSV_TYPES  = [("CSV files", "*.csv"), ("All files", "*.*")]
        XLSX_TYPES = [("Excel files", "*.xlsx *.xls"), ("All files", "*.*")]
        ZIP_TYPES  = [("ZIP/CSV/Excel", "*.zip *.csv *.xlsx"), ("All files", "*.*")]

        self.sel_shopify = FileSelector(
            body,
            label="Archivos Shopify",
            filetypes=CSV_TYPES,
            multiple=True,
            required=True,
            accent_color=COLORS["accent"],
        )
        self.sel_shopify.pack(fill="x", padx=16, pady=4)
        self._separador(body)

        self.sel_mercadopago = FileSelector(
            body,
            label="Archivos MercadoPago (.xlsx)",
            filetypes=XLSX_TYPES,
            multiple=True,
            required=False,
            accent_color=COLORS["optional"],
        )
        self.sel_mercadopago.pack(fill="x", padx=16, pady=4)
        self._separador(body)

        self.sel_addi = FileSelector(
            body,
            label="Archivos ADDI (.zip o .csv)",
            filetypes=ZIP_TYPES,
            multiple=True,
            required=False,
            accent_color=COLORS["optional"],
        )
        self.sel_addi.pack(fill="x", padx=16, pady=4)
        self._separador(body)

        self.sel_payu = FileSelector(
            body,
            label="Archivos PayU (.csv)",
            filetypes=CSV_TYPES,
            multiple=True,
            required=False,
            accent_color=COLORS["optional"],
        )
        self.sel_payu.pack(fill="x", padx=16, pady=4)
        self._separador(body)

        self.sel_odoo = FileSelector(
            body,
            label="Archivos Odoo / Ventas limpias (.csv separado por ;)",
            filetypes=CSV_TYPES,
            multiple=True,
            required=False,
            accent_color=COLORS["optional"],
        )
        self.sel_odoo.pack(fill="x", padx=16, pady=4)

        # ── Sección: Carpeta de salida ────────────────────────────────────
        self._seccion(body, "2  —  Carpeta de resultados")

        salida_frame = tk.Frame(body, bg=COLORS["surface"])
        salida_frame.pack(fill="x", padx=16, pady=4)

        inner = tk.Frame(salida_frame, bg=COLORS["surface"])
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(
            inner,
            text="📁  Guardar resultados en:",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=FONT_SUB,
        ).pack(side="left")

        tk.Button(
            inner,
            text="Elegir carpeta",
            command=self._seleccionar_carpeta,
            bg=COLORS["accent"],
            fg="#FFFFFF",
            font=FONT_SMALL,
            relief="flat",
            cursor="hand2",
            padx=10, pady=3,
        ).pack(side="right")

        self.lbl_carpeta = tk.Label(
            salida_frame,
            textvariable=self._carpeta_salida,
            bg=COLORS["surface2"],
            fg=COLORS["accent2"],
            font=FONT_MONO,
            anchor="w",
            pady=6,
            padx=8,
        )
        self.lbl_carpeta.pack(fill="x", padx=12, pady=(0, 10))

        # ── Sección: Ejecución ────────────────────────────────────────────
        self._seccion(body, "3  —  Ejecutar")

        btn_frame = tk.Frame(body, bg=COLORS["bg"])
        btn_frame.pack(fill="x", padx=16, pady=8)

        self.btn_ejecutar = tk.Button(
            btn_frame,
            text="▶  Ejecutar conciliación",
            command=self._ejecutar,
            bg=COLORS["accent2"],
            fg="#0F1117",
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            cursor="hand2",
            padx=24, pady=10,
        )
        self.btn_ejecutar.pack(side="left")

        tk.Button(
            btn_frame,
            text="🗑  Limpiar todo",
            command=self._limpiar_todo,
            bg=COLORS["surface"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
            relief="flat",
            cursor="hand2",
            padx=10, pady=4,
        ).pack(side="left", padx=12)

        # Barra de progreso
        self.progreso = ttk.Progressbar(
            body, mode="indeterminate", length=400
        )
        self.progreso.pack(padx=16, pady=(4, 0), fill="x")

        # ── Log ────────────────────────────────────────────────────────────
        self.log_panel = LogPanel(body)
        self.log_panel.pack(fill="both", expand=True, padx=16, pady=(8, 16))

    # ── Helpers de construcción ────────────────────────────────────────────

    def _seccion(self, parent, titulo: str):
        frame = tk.Frame(parent, bg=COLORS["bg"])
        frame.pack(fill="x", padx=16, pady=(16, 4))
        tk.Label(
            frame,
            text=titulo.upper(),
            bg=COLORS["bg"],
            fg=COLORS["accent"],
            font=("Segoe UI", 8, "bold"),
            pady=2,
        ).pack(side="left")
        tk.Frame(frame, bg=COLORS["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=8
        )

    def _separador(self, parent):
        tk.Frame(parent, bg=COLORS["border"], height=1).pack(
            fill="x", padx=32, pady=2
        )

    # ── Acciones ──────────────────────────────────────────────────────────

    def _seleccionar_carpeta(self):
        carpeta = filedialog.askdirectory(title="Seleccionar carpeta de resultados")
        if carpeta:
            self._carpeta_salida.set(carpeta)

    def _limpiar_todo(self):
        for sel in [
            self.sel_shopify, self.sel_mercadopago,
            self.sel_addi, self.sel_payu, self.sel_odoo,
        ]:
            sel.limpiar()
        self._carpeta_salida.set("")
        self.log_panel.limpiar()

    def _ejecutar(self):
        # ── Validaciones ──────────────────────────────────────────────────
        if not self.sel_shopify.archivos:
            messagebox.showerror(
                "Archivo requerido",
                "⚠  Debes seleccionar al menos un archivo de Shopify para continuar.",
            )
            return

        if not self._carpeta_salida.get():
            messagebox.showerror(
                "Carpeta requerida",
                "⚠  Debes elegir una carpeta donde guardar los resultados.",
            )
            return

        # Verificar que al menos una pasarela fue seleccionada
        pasarelas = (
            self.sel_mercadopago.archivos
            + self.sel_addi.archivos
            + self.sel_payu.archivos
        )
        if not pasarelas:
            messagebox.showwarning(
                "Sin pasarelas",
                "No seleccionaste archivos de ninguna pasarela de pago.\n"
                "Selecciona al menos MercadoPago, ADDI o PayU.",
            )
            return

        if self._procesando:
            return

        # ── Iniciar procesamiento en hilo separado ─────────────────────────
        self._procesando = True
        self.btn_ejecutar.configure(state="disabled", text="⏳  Procesando...")
        self.progreso.start(12)
        self.log_panel.limpiar()
        self.log_panel.log("Iniciando proceso de conciliación...", "info")

        hilo = threading.Thread(target=self._hilo_proceso, daemon=True)
        hilo.start()

    def _hilo_proceso(self):
        """Ejecuta el procesamiento en un hilo para no bloquear la UI."""
        try:
            resultados = procesar_conciliacion(
                archivos_shopify     = self.sel_shopify.archivos,
                archivos_mercadopago = self.sel_mercadopago.archivos,
                archivos_addi        = self.sel_addi.archivos,
                archivos_payu        = self.sel_payu.archivos,
                archivos_odoo        = self.sel_odoo.archivos,
                carpeta_salida       = self._carpeta_salida.get(),
                log_callback         = self._log_thread_safe,
            )
            self.after(0, self._finalizar_exito, resultados)

        except Exception as exc:
            error_detalle = traceback.format_exc()
            self.after(0, self._finalizar_error, str(exc), error_detalle)

    def _log_thread_safe(self, mensaje: str):
        """Envía mensajes al log desde un hilo secundario."""
        tipo = (
            "success" if mensaje.startswith("✅") else
            "warning" if mensaje.startswith("⚠") else
            "error"   if mensaje.startswith("❌") else
            "info"
        )
        self.after(0, lambda: self.log_panel.log(mensaje, tipo))

    def _finalizar_exito(self, resultados: dict):
        self.progreso.stop()
        self._procesando = False
        self.btn_ejecutar.configure(state="normal", text="▶  Ejecutar conciliación")

        # Construir resumen
        resumen = "Archivos generados:\n\n"
        for pasarela, datos in resultados.items():
            resumen += f"  ✔  {pasarela}: {datos['registros']:,} registros\n"
            resumen += f"      → {Path(datos['archivo']).name}\n\n"
        resumen += f"Carpeta: {self._carpeta_salida.get()}"

        messagebox.showinfo("✅  Proceso completado", resumen)

        # Ofrecer abrir carpeta
        if messagebox.askyesno(
            "Abrir carpeta",
            "¿Deseas abrir la carpeta con los resultados?",
        ):
            os.startfile(self._carpeta_salida.get())

    def _finalizar_error(self, mensaje: str, detalle: str):
        self.progreso.stop()
        self._procesando = False
        self.btn_ejecutar.configure(state="normal", text="▶  Ejecutar conciliación")
        self.log_panel.log(f"❌ ERROR: {mensaje}", "error")

        messagebox.showerror(
            "❌  Error en el proceso",
            f"Ocurrió un error durante la conciliación:\n\n{mensaje}\n\n"
            "Revisa el registro de actividad para más detalles.",
        )
        # Log del traceback completo
        for linea in detalle.splitlines():
            self.log_panel.log(linea, "error")


# ─────────────────────────────────────────────────────────────────────────────
#  PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Necesario para PyInstaller --noconsole en Windows
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    app = AppConciliacion()
    app.mainloop()


if __name__ == "__main__":
    main()