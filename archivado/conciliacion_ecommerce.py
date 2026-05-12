"""
Conciliación E-commerce – La Poción
Cruza ventas de Shopify contra ADDI, MercadoPago y PayU.

Para compilar como ejecutable:
    pyinstaller --onefile --noconsole \
        --add-data "la_pocion_logo.jfif;." \
        conciliacion_ecommerce.py
"""

import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import traceback
import zipfile
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

try:
    from PIL import Image, ImageTk
    _PIL = True
except ImportError:
    _PIL = False


def resource_path(nombre: str) -> Path:
    """Ruta al recurso, compatible con PyInstaller --onefile."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / nombre
    return Path(__file__).parent / nombre


# ══════════════════════════════════════════════════════════
#  UTILIDADES DE CARGA
# ══════════════════════════════════════════════════════════

def _leer_archivo(ruta: Path, extension: str, sep: str = ",") -> pd.DataFrame | None:
    """Lee un único archivo CSV o Excel con fallback de encoding."""
    try:
        if extension == "csv":
            try:
                return pd.read_csv(ruta, sep=sep, encoding="utf-8", low_memory=False)
            except UnicodeDecodeError:
                return pd.read_csv(ruta, sep=sep, encoding="latin-1", low_memory=False)
        elif extension in ("xlsx", "xls"):
            return pd.read_excel(ruta, engine="openpyxl")
    except Exception:
        return None


def consolidar_carpeta(ruta_carpeta: str, extension: str = "csv", sep: str = ",") -> pd.DataFrame | None:
    """Concatena todos los archivos de una carpeta con la extensión indicada."""
    ruta = Path(ruta_carpeta)
    archivos = list(ruta.glob(f"*.{extension}"))
    if not archivos:
        return None

    dfs = [df for a in archivos if (df := _leer_archivo(a, extension, sep)) is not None]
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]


# ══════════════════════════════════════════════════════════
#  ETAPAS DE PROCESAMIENTO
# ══════════════════════════════════════════════════════════

def cargar_shopify(ruta: str, log: callable) -> pd.DataFrame:
    """Carga y limpia las órdenes de Shopify."""
    log("Cargando ventas Shopify...")
    df = consolidar_carpeta(ruta, extension="csv")
    if df is None:
        raise FileNotFoundError("No se encontraron archivos CSV en la carpeta de Shopify.")

    # Eliminar filas de detalle de producto (sin Payment ID)
    if "Payment ID" in df.columns:
        df = df[df["Payment ID"].notna()].copy()

    # Normalizar columna de referencia de pago
    # Shopify puede exportarla como 'Payment Reference' o 'Payment References'
    if "Payment Reference" not in df.columns and "Payment References" in df.columns:
        df.rename(columns={"Payment References": "Payment Reference"}, inplace=True)
    elif "Payment Reference" not in df.columns and "Payment ID" in df.columns:
        df["Payment Reference"] = df["Payment ID"]

    # Parsear fecha
    if "Created at" in df.columns:
        df["Created at"] = pd.to_datetime(df["Created at"], errors="coerce")
        df["Period"] = df["Created at"].dt.to_period("M").astype(str)
        df["Created at"] = df["Created at"].dt.date

    if "NUMERO_FACTURA" not in df.columns:
        df["NUMERO_FACTURA"] = np.nan

    log(f"  → {len(df):,} órdenes Shopify cargadas.")
    return df


def _shopi_referencia(ventas_shopi: pd.DataFrame) -> pd.DataFrame:
    """Extrae y normaliza las columnas de referencia de pago de Shopify."""
    cols = ["Payment Reference", "Total", "Shipping", "NUMERO_FACTURA"]
    cols_existentes = [c for c in cols if c in ventas_shopi.columns]
    ref = ventas_shopi[cols_existentes].dropna(subset=["Payment Reference"]).copy()
    ref["Payment Reference"] = ref["Payment Reference"].astype(str).str.strip()
    return ref


def conciliar_odoo(ruta: str, ventas_shopi: pd.DataFrame, ruta_salida: str, log: callable):
    """Concilia los pagos de Odoo contra Shopify."""
    log("Procesando Odoo...")
    df_odoo = consolidar_carpeta(ruta, extension="xlsx")
    if df_odoo is None:
        log("  ⚠ No se encontraron archivos XLSX de Odoo.")
        return

    if "Referencia" not in df_odoo.columns:
        log("  ⚠ Columna 'Referencia' no encontrada en Odoo.")
        return

    df_odoo = df_odoo[['Número','Referencia','Total firmado']]
    df_odoo["Referencia"] = df_odoo["Referencia"].astype(str).str.strip()
    df_result = ventas_shopi.merge(df_odoo[['Referencia','Total firmado']], left_on='Name', right_on='Referencia', how='left')

    df_result['Validacion'] = np.where(
        df_result['Total firmado'] == df_result['Subtotal'], 'Conciliado',
        np.where(~df_result['Financial Status'].isin(['paid']), 'Rechazada/Cancelada',
                 np.where(df_result['Total firmado'].isna(), 'No esta en Odoo',
                          'No Conciliado'))
    )
    salida = Path(ruta_salida) / "conciliado_odoo.xlsx"
    df_result.to_excel(salida, index=False)
    log(f"  ✓ Odoo → {len(df_result):,} filas guardadas en '{salida.name}'")


def conciliar_addi(ruta: str, ventas_shopi: pd.DataFrame, ruta_salida: str, log: callable):
    """Concilia los pagos de ADDI contra Shopify."""
    log("Procesando ADDI...")
    ruta_addi = Path(ruta)
    dfs = []

    archivos_zip = list(ruta_addi.glob("*.zip"))

    if archivos_zip:
        log(f"  → {len(archivos_zip)} archivos ZIP encontrados.")
        for archivo_zip in archivos_zip:
            if not zipfile.is_zipfile(archivo_zip):
                continue
            with zipfile.ZipFile(archivo_zip, "r") as z:
                internos = [
                    f for f in z.namelist()
                    if "pagos/" in f.lower() and "resumen general.xlsx" in f.lower()
                ]
                if not internos:
                    log(f"  ⚠ No se encontró 'resumen general.xlsx' en {archivo_zip.name}")
                    continue
                with z.open(internos[0]) as excel:
                    df = pd.read_excel(excel, sheet_name="Transacciones + cancelaciones", engine="openpyxl")
                    df = df[~df[df.columns[1]].isna()]
                    df.columns = df.iloc[0].values
                    df = df.iloc[1:].copy()
                    dfs.append(df)

        if not dfs:
            log("  ⚠ No se procesaron ZIPs válidos de ADDI.")
            return

        df_addi = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
        clave_addi = "Id pedido"
        col_monto = "Total Después de\ndescuentos (2)"

        shopi_ref = _shopi_referencia(ventas_shopi)
        df_addi[clave_addi] = df_addi[clave_addi].astype(str).str.strip()
        df_result = df_addi.merge(shopi_ref, left_on=clave_addi, right_on="Payment Reference", how="left")

        df_result["Validacion"] = np.where(
            df_result["Total"] == df_result[col_monto],
            "Conciliado", "No Conciliado"
        )

    else:
        # Archivos CSV directos
        df_addi = consolidar_carpeta(ruta, extension="csv")
        if df_addi is None:
            log("  ⚠ No se encontraron archivos CSV ni ZIP de ADDI.")
            return

        if "ID Orden" not in df_addi.columns:
            log("  ⚠ Columna 'ID Orden' no encontrada en los CSV de ADDI.")
            return

        shopi_ref = _shopi_referencia(ventas_shopi)
        df_addi["ID Orden"] = df_addi["ID Orden"].astype(str).str.strip()
        df_result = df_addi.merge(shopi_ref, left_on="ID Orden", right_on="Payment Reference", how="left")

        estado_col = "Estado" if "Estado" in df_addi.columns else None
        monto_col  = "Monto"  if "Monto"  in df_addi.columns else None

        def _validar(row):
            if monto_col and row.get("Total") == row.get(monto_col):
                return "Conciliado"
            if estado_col and row.get(estado_col) in ("Cancelada", "Abandono", "Rechazada"):
                return "Rechazada/Cancelada"
            if pd.isna(row.get("Total")):
                return "No esta en shopify"
            return "No Conciliado"

        df_result["Validacion"] = df_result.apply(_validar, axis=1)

    salida = Path(ruta_salida) / "conciliado_addi.xlsx"
    df_result.to_excel(salida, index=False)
    log(f"  ✓ ADDI → {len(df_result):,} filas guardadas en '{salida.name}'")


def conciliar_mercadopago(ruta: str, ventas_shopi: pd.DataFrame, ruta_salida: str, log: callable):
    """Concilia los pagos de MercadoPago contra Shopify."""
    log("Procesando MercadoPago...")
    df_mp = consolidar_carpeta(ruta, extension="xlsx")
    if df_mp is None:
        log("  ⚠ No se encontraron archivos XLSX de MercadoPago.")
        return


    df_mercado = None
    if 'Código de referencia (external_reference)' in df_mp.columns:
        df_mp = df_mp.rename(columns={'Código de referencia (external_reference)': 'external_reference'})
    else: 
        df_mp = df_mp.rename(columns={'NÚMERO DE IDENTIFICACIÓN': 'external_reference'})
        df_mercado = True



    shopi_ref = _shopi_referencia(ventas_shopi)
    df_result = df_mp.merge(shopi_ref, left_on='external_reference', right_on="Payment Reference", how="left")

    if   df_mercado:
        df_result['Validacion'] = np.where(
            df_result['Total'] == df_result['VALOR DE LA COMPRA'],'Conciliado',
                            np.where(df_result['Total'].isna(), 'No esta en shopify',
            'No Conciliado'))
        

    else:
        df_result['Validacion'] = np.where(
            df_result['Total'] == df_result['Valor del producto (transaction_amount)'],'Conciliado',
                np.where(~df_result['Estado de la operación (status)'].isin(['approved']), 'Rechazada/Cancelada',
                            np.where(df_result['Total'].isna(), 'No esta en shopify',
            'No Conciliado'))
        )

    salida = Path(ruta_salida) / "conciliado_mercadopago.xlsx"
    df_result.to_excel(salida, index=False)
    log(f"  ✓ MercadoPago → {len(df_result):,} filas guardadas en '{salida.name}'")


def conciliar_payu(ruta: str, ventas_shopi: pd.DataFrame, ruta_salida: str, log: callable):
    """Concilia los pagos de PayU contra Shopify."""
    log("Procesando PayU...")
    df_payu = consolidar_carpeta(ruta, extension="csv")
    if df_payu is None:
        log("  ⚠ No se encontraron archivos CSV de PayU.")
        return

    if "Referencia" not in df_payu.columns:
        log("  ⚠ Columna 'Referencia' no encontrada en PayU.")
        return

    shopi_ref = _shopi_referencia(ventas_shopi)
    df_result = df_payu.merge(shopi_ref, left_on="Referencia", right_on="Payment Reference", how="left")

    col_estado = "Estado de transacción" if "Estado de transacción" in df_payu.columns else None
    col_monto  = "Valor procesado"       if "Valor procesado"       in df_payu.columns else None

    df_result["Validacion"] = np.where(
        df_result["Total"] == df_result.get(col_monto, pd.Series(dtype=float)), "Conciliado",
        np.where(
            ~df_result[col_estado].isin(["approved"]) if col_estado else False,
            "Rechazada/Cancelada",
            np.where(df_result["Total"].isna(), "No esta en shopify", "No Conciliado")
        )
    )

    salida = Path(ruta_salida) / "conciliado_payu.xlsx"
    df_result.to_excel(salida, index=False)
    log(f"  ✓ PayU → {len(df_result):,} filas guardadas en '{salida.name}'")


def ejecutar_proceso(rutas: dict, log: callable, on_finish: callable):
    """
    Orquesta todo el proceso de conciliación.
    Se ejecuta en un hilo separado para no bloquear la interfaz.
    """
    try:
        ventas_shopi = cargar_shopify(rutas["shopify"], log)

        if rutas.get("odoo"):
            conciliar_odoo(rutas["odoo"], ventas_shopi, rutas["salida"], log)

        if rutas.get("addi"):
            conciliar_addi(rutas["addi"], ventas_shopi, rutas["salida"], log)

        if rutas.get("mercadopago"):
            conciliar_mercadopago(rutas["mercadopago"], ventas_shopi, rutas["salida"], log)

        if rutas.get("payu"):
            conciliar_payu(rutas["payu"], ventas_shopi, rutas["salida"], log)

        log("\n✅ Proceso completado exitosamente.")
        on_finish(success=True)

    except Exception as exc:
        log(f"\n❌ Error: {exc}")
        log(traceback.format_exc())
        on_finish(success=False, error=str(exc))


# ══════════════════════════════════════════════════════════
#  INTERFAZ GRÁFICA
# ══════════════════════════════════════════════════════════

# Paleta de colores
_HDR    = "#1E3A5F"   # header
_HDR2   = "#162d4a"   # header oscuro (acento inferior)
_AZUL   = "#2563EB"   # botones Explorar
_AZUL_H = "#1D4ED8"   # hover
_VERDE  = "#16A34A"   # botón ejecutar
_VERDE_H= "#15803D"   # hover
_ROJO   = "#DC2626"   # campos obligatorios
_FONDO  = "#F1F5F9"   # fondo ventana
_CARD   = "#FFFFFF"   # tarjetas
_BORDE  = "#CBD5E1"   # bordes
_TEXTO  = "#1E293B"   # texto principal
_MUTED  = "#64748B"   # texto secundario


def _hover(widget, normal: str, over: str):
    widget.bind("<Enter>", lambda _: widget.configure(bg=over))
    widget.bind("<Leave>", lambda _: widget.configure(bg=normal))


class FilaRuta(tk.Frame):
    """Fila de selección de carpeta: etiqueta + entry + botón."""

    def __init__(self, parent, label: str, variable: tk.StringVar, obligatorio: bool = False):
        super().__init__(parent, bg=_CARD)

        indicador = " ★" if obligatorio else "   "
        color_lbl = _ROJO if obligatorio else _TEXTO

        tk.Label(self, text=f"{label}{indicador}", anchor="w",
                 font=("Segoe UI", 9), bg=_CARD, fg=color_lbl,
                 width=24).pack(side="left")

        tk.Entry(self, textvariable=variable,
                 font=("Segoe UI", 9), width=33,
                 relief="solid", bd=1,
                 bg="#F8FAFC", fg=_TEXTO,
                 insertbackground=_TEXTO).pack(side="left", ipady=4, padx=(4, 6))

        btn = tk.Button(self, text="Explorar", font=("Segoe UI", 8, "bold"),
                        bg=_AZUL, fg="white",
                        activebackground=_AZUL_H, activeforeground="white",
                        relief="flat", cursor="hand2", bd=0, padx=12, pady=4,
                        command=lambda: self._seleccionar(variable))
        btn.pack(side="left")
        _hover(btn, _AZUL, _AZUL_H)

    @staticmethod
    def _seleccionar(variable: tk.StringVar):
        ruta = filedialog.askdirectory(title="Seleccionar carpeta")
        if ruta:
            variable.set(ruta)


class ConciliacionApp:
    """Ventana principal de la aplicación."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Conciliación E-commerce – La Poción")
        self.root.geometry("730x670")
        self.root.resizable(False, False)
        self.root.configure(bg=_FONDO)

        self.var_shopify = tk.StringVar()
        self.var_odoo    = tk.StringVar()
        self.var_addi    = tk.StringVar()
        self.var_mp      = tk.StringVar()
        self.var_payu    = tk.StringVar()
        self.var_salida  = tk.StringVar()

        self._logo_header = None
        self._cargar_logo()
        self._estilo_ttk()
        self._construir_ui()

    # ── Logo ──────────────────────────────────────────────

    def _cargar_logo(self):
        if not _PIL:
            return
        try:
            img = Image.open(resource_path("la_pocion_logo.jfif"))

            # Ícono de la ventana (32 × 32)
            icon = img.copy().resize((32, 32), Image.LANCZOS)
            self._icon_photo = ImageTk.PhotoImage(icon)
            self.root.iconphoto(True, self._icon_photo)

            # Logo para el header (altura máx. 54 px)
            logo = img.copy()
            logo.thumbnail((54, 54), Image.LANCZOS)
            self._logo_header = ImageTk.PhotoImage(logo)
        except Exception:
            pass

    # ── Estilos ttk ───────────────────────────────────────

    def _estilo_ttk(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Bar.Horizontal.TProgressbar",
                    troughcolor=_BORDE,
                    background=_VERDE,
                    thickness=5)

    # ── Construcción de la UI ──────────────────────────────

    def _construir_ui(self):
        self._header()
        self._seccion_entradas()
        self._seccion_salida()
        self._boton_ejecutar()
        self._seccion_log()

    def _header(self):
        hdr = tk.Frame(self.root, bg=_HDR)
        hdr.pack(fill="x")

        # Franja de acento inferior
        tk.Frame(self.root, bg=_HDR2, height=3).pack(fill="x")

        inner = tk.Frame(hdr, bg=_HDR)
        inner.pack(padx=18, pady=12, anchor="w")

        if self._logo_header:
            tk.Label(inner, image=self._logo_header,
                     bg=_HDR).pack(side="left", padx=(0, 14))

        txt = tk.Frame(inner, bg=_HDR)
        txt.pack(side="left")
        tk.Label(txt, text="La Poción",
                 font=("Segoe UI", 18, "bold"),
                 fg="white", bg=_HDR, anchor="w").pack(anchor="w")
        tk.Label(txt, text="Conciliación E-commerce",
                 font=("Segoe UI", 9),
                 fg="#94A3B8", bg=_HDR, anchor="w").pack(anchor="w")

    def _card(self, titulo: str) -> tk.Frame:
        """Retorna el frame interior de una tarjeta con título y separador."""
        wrap = tk.Frame(self.root, bg=_BORDE)
        wrap.pack(fill="x", padx=16, pady=(10, 0))

        card = tk.Frame(wrap, bg=_CARD)
        card.pack(fill="x", padx=1, pady=1)

        tk.Label(card, text=titulo,
                 font=("Segoe UI", 7, "bold"),
                 fg=_MUTED, bg=_CARD).pack(anchor="w", padx=14, pady=(8, 0))

        tk.Frame(card, bg=_BORDE, height=1).pack(fill="x", padx=14, pady=(4, 0))

        content = tk.Frame(card, bg=_CARD)
        content.pack(fill="x", padx=14, pady=(6, 10))
        return content

    def _seccion_entradas(self):
        card = self._card("ARCHIVOS DE ENTRADA")
        FilaRuta(card, "Shopify (CSV)", self.var_shopify, obligatorio=True).pack(fill="x", pady=3)
        FilaRuta(card, "ODOO (XLSX)",       self.var_odoo).pack(fill="x", pady=3)
        FilaRuta(card, "ADDI (CSV o ZIP)",  self.var_addi).pack(fill="x", pady=3)
        FilaRuta(card, "MercadoPago (XLSX)", self.var_mp).pack(fill="x", pady=3)
        FilaRuta(card, "PayU (CSV)",         self.var_payu).pack(fill="x", pady=3)

    def _seccion_salida(self):
        card = self._card("CARPETA DE RESULTADOS")
        FilaRuta(card, "Guardar resultados en", self.var_salida, obligatorio=True).pack(fill="x", pady=3)

    def _boton_ejecutar(self):
        frame = tk.Frame(self.root, bg=_FONDO)
        frame.pack(fill="x", padx=16, pady=(12, 0))

        self.btn_ejecutar = tk.Button(
            frame,
            text="▶   Ejecutar conciliación",
            font=("Segoe UI", 11, "bold"),
            bg=_VERDE, fg="white",
            activebackground=_VERDE_H, activeforeground="white",
            relief="flat", cursor="hand2", bd=0, pady=11,
            command=self._on_ejecutar,
        )
        self.btn_ejecutar.pack(fill="x")
        _hover(self.btn_ejecutar, _VERDE, _VERDE_H)

        self.progress = ttk.Progressbar(
            frame, mode="indeterminate",
            style="Bar.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(5, 0))

    def _seccion_log(self):
        wrap = tk.Frame(self.root, bg=_BORDE)
        wrap.pack(fill="both", expand=True, padx=16, pady=(10, 14))

        terminal = tk.Frame(wrap, bg="#0D1117")
        terminal.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(terminal, text="  PROGRESO",
                 font=("Segoe UI", 7, "bold"),
                 fg="#4B5563", bg="#0D1117",
                 anchor="w").pack(fill="x", pady=(6, 2))

        tk.Frame(terminal, bg="#21262D", height=1).pack(fill="x")

        self.txt_log = tk.Text(
            terminal, height=8,
            font=("Consolas", 9),
            bg="#0D1117", fg="#C9D1D9",
            insertbackground="white",
            selectbackground="#264F78",
            relief="flat", state="disabled", wrap="word", bd=0,
        )
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        # Colorear timestamps de forma diferente al texto normal
        self.txt_log.tag_configure("ts", foreground="#58A6FF")
        self.txt_log.tag_configure("ok", foreground="#3FB950")
        self.txt_log.tag_configure("err", foreground="#F85149")
        self.txt_log.tag_configure("warn", foreground="#D29922")

    # ── Acciones ──────────────────────────────────────────

    def _log(self, mensaje: str):
        def _escribir():
            ts = datetime.now().strftime("%H:%M:%S")
            self.txt_log.configure(state="normal")

            start = self.txt_log.index("end-1c")
            self.txt_log.insert("end", f"[{ts}] ")
            self.txt_log.tag_add("ts", start, self.txt_log.index("end-1c"))

            # Elegir color según contenido
            if "✓" in mensaje or "✅" in mensaje:
                tag = "ok"
            elif "❌" in mensaje or "Error" in mensaje:
                tag = "err"
            elif "⚠" in mensaje:
                tag = "warn"
            else:
                tag = None

            msg_start = self.txt_log.index("end-1c")
            self.txt_log.insert("end", f"{mensaje}\n")
            if tag:
                self.txt_log.tag_add(tag, msg_start, self.txt_log.index("end-1c"))

            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.root.after(0, _escribir)

    def _on_ejecutar(self):
        if not self.var_shopify.get().strip():
            messagebox.showwarning("Campo requerido",
                                   "Debe seleccionar la carpeta de archivos de Shopify.")
            return

        if not self.var_salida.get().strip():
            messagebox.showwarning("Campo requerido",
                                   "Debe seleccionar la carpeta donde guardar los resultados.")
            return

        pasarelas = [self.var_odoo.get(), self.var_addi.get(),
                     self.var_mp.get(), self.var_payu.get()]
        if not any(p.strip() for p in pasarelas):
            messagebox.showwarning(
                "Sin pasarelas seleccionadas",
                "Seleccione al menos una pasarela de pago:\nODOO, ADDI, MercadoPago o PayU."
            )
            return

        rutas = {
            "shopify":     self.var_shopify.get().strip(),
            "odoo":        self.var_odoo.get().strip()   or None,
            "addi":        self.var_addi.get().strip()   or None,
            "mercadopago": self.var_mp.get().strip()     or None,
            "payu":        self.var_payu.get().strip()   or None,
            "salida":      self.var_salida.get().strip(),
        }

        self.btn_ejecutar.configure(state="disabled", bg="#475569",
                                    text="⏳   Procesando...")
        self.progress.start(10)
        self._log("Iniciando proceso de conciliación...")

        threading.Thread(
            target=ejecutar_proceso,
            args=(rutas, self._log, self._on_finish),
            daemon=True
        ).start()

    def _on_finish(self, success: bool = True, error: str = None):
        def _actualizar():
            self.progress.stop()
            self.btn_ejecutar.configure(state="normal", bg=_VERDE,
                                        text="▶   Ejecutar conciliación")
            if success:
                messagebox.showinfo(
                    "Proceso completado",
                    f"Conciliación finalizada.\n\nArchivos guardados en:\n{self.var_salida.get()}"
                )
            else:
                messagebox.showerror(
                    "Error en el proceso",
                    f"Ocurrió un error:\n\n{error}\n\nRevisa el log para más detalles."
                )
        self.root.after(0, _actualizar)


# ══════════════════════════════════════════════════════════
#  PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    ConciliacionApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
