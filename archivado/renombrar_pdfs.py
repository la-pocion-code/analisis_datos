import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from pathlib import Path


def generar_nombre_unico(carpeta, nombre_base):
    """
    Si el archivo ya existe:
    123.pdf
    genera:
    123_renombrado.pdf

    Si también existe:
    123_renombrado2.pdf
    """
    destino = carpeta / f"{nombre_base}.pdf"

    if not destino.exists():
        return destino

    contador = 1

    while True:
        sufijo = "_renombrado" if contador == 1 else f"_renombrado{contador}"
        destino = carpeta / f"{nombre_base}{sufijo}.pdf"

        if not destino.exists():
            return destino

        contador += 1


def renombrar_pdfs():

    carpeta = filedialog.askdirectory(
        title="Seleccione la carpeta que contiene los PDFs"
    )

    if not carpeta:
        return

    carpeta = Path(carpeta)

    texto_adicional = entrada_texto.get().strip()

    total = 0
    renombrados = 0
    errores = 0

    log.delete(1.0, tk.END)

    archivos = list(carpeta.glob("*.pdf"))

    if not archivos:
        messagebox.showwarning(
            "Sin archivos",
            "No se encontraron archivos PDF en la carpeta seleccionada."
        )
        return

    for archivo in archivos:

        total += 1

        try:

            nombre_original = archivo.stem.strip()

            # Toma todo lo que está antes del primer espacio
            identificacion = nombre_original.split(" ")[0].strip()

            if not identificacion:
                errores += 1
                log.insert(
                    tk.END,
                    f"⚠ No se pudo obtener identificación de: {archivo.name}\n"
                )
                continue

            nuevo_nombre = identificacion

            if texto_adicional:
                nuevo_nombre = f"{identificacion} {texto_adicional}"

            nuevo_archivo = generar_nombre_unico(
                carpeta,
                nuevo_nombre
            )

            if archivo.resolve() == nuevo_archivo.resolve():
                continue

            archivo.rename(nuevo_archivo)

            renombrados += 1

            log.insert(
                tk.END,
                f"✔ {archivo.name}\n"
                f"   → {nuevo_archivo.name}\n\n"
            )

        except Exception as e:

            errores += 1

            log.insert(
                tk.END,
                f"✖ Error en {archivo.name}\n"
                f"   {str(e)}\n\n"
            )

    messagebox.showinfo(
        "Proceso finalizado",
        f"""PDF encontrados: {total}

Renombrados: {renombrados}

Errores: {errores}
"""
    )


# ==========================
# INTERFAZ
# ==========================

root = tk.Tk()
root.title("Renombrador de PDFs")
root.geometry("900x600")

titulo = tk.Label(
    root,
    text="Renombrador de PDFs por Identificación",
    font=("Segoe UI", 14, "bold")
)
titulo.pack(pady=10)

lbl = tk.Label(
    root,
    text="Texto que se agregará después de la identificación:"
)
lbl.pack()

entrada_texto = tk.Entry(
    root,
    width=70,
    font=("Segoe UI", 10)
)
entrada_texto.pack(pady=5)

# Texto por defecto
entrada_texto.insert(
    0,
    "Certificado de ingresos 2025"
)

btn = tk.Button(
    root,
    text="Seleccionar carpeta y ejecutar",
    font=("Segoe UI", 11, "bold"),
    height=2,
    command=renombrar_pdfs
)
btn.pack(pady=10)

log = scrolledtext.ScrolledText(
    root,
    width=110,
    height=25,
    font=("Consolas", 9)
)
log.pack(
    padx=10,
    pady=10,
    fill="both",
    expand=True
)

root.mainloop()