"""
╔══════════════════════════════════════════════════════════════════╗
║   RUES CIIU por NIT — vía Datos Abiertos de Colombia (Socrata)     ║
║   Reemplaza el scraping con Playwright por consultas API directas  ║
║   Dataset oficial RUES: c82u-588k (datos.gov.co)                   ║
╚══════════════════════════════════════════════════════════════════╝

INSTALACIÓN PREVIA:
    pip install pandas openpyxl requests

EJECUCIÓN:
    python rues_ciiu_api.py

QUÉ HACE:
    - Lee 'Base_Correos_Telefonos.xlsx' (hoja F205, columna 'Número identificación')
    - Consulta el CIIU principal de cada NIT en la API abierta del RUES
    - Elige la fila correcta cuando el NIT tiene varios registros
    - Escribe el resultado en 'proveedores_ciiu_actualizado.xlsx'
    - Guarda checkpoints cada 50 filas y registra todo en un log

VENTAJAS vs. Playwright:
    - Sin navegador, sin CAPTCHA, sin token  →  estable y ~50x más rápido
    - Trae además razón social y estado de matrícula para control de calidad
"""

import re
import time
import logging
from pathlib import Path

import pandas as pd
import requests

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
INPUT_FILE  = "Base_Correos_Telefonos.xlsx"
OUTPUT_FILE = "proveedores_ciiu_actualizado.xlsx"
SHEET_NAME  = "F205"

COL_NIT    = "Número identificación"     # columna de entrada con los NITs
COL_CIIU   = "Codigo_CIUU_Principal"     # salida: código CIIU
COL_RAZON  = "Razon_Social_RUES"         # salida extra (control de calidad)
COL_ESTADO = "Estado_Matricula_RUES"     # salida extra (control de calidad)

# API oficial de Datos Abiertos (RUES publicado por Confecámaras)
SOCRATA_URL = "https://www.datos.gov.co/resource/c82u-588k.json"

# Opcional: token de app de Socrata para subir el límite de peticiones.
# No es obligatorio. Si lo tienes, pégalo aquí; si no, déjalo vacío.
APP_TOKEN = ""

DELAY_ENTRE_CONSULTAS = 0.15   # segundos; la API aguanta bien este ritmo
MAX_REINTENTOS        = 3
TIMEOUT_S             = 20
CHECKPOINT_CADA       = 50

# CIIU que se consideran "no informativos" y se descartan si hay opción mejor
CIIU_BASURA = {"9999", "0000", "0", "", None}

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("rues_ciiu_api.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# LIMPIEZA DEL NIT
# ─────────────────────────────────────────────
def limpiar_nit(valor) -> str:
    """
    Normaliza el NIT a solo dígitos, SIN dígito de verificación.
    Maneja casos comunes:
        '890.903.939-5'  → '890903939'
        '890903939.0'    → '890903939'   (artefacto float de Excel)
        '890903939'      → '890903939'
    """
    s = str(valor).strip()
    if s.endswith(".0"):            # Excel guardó el número como float
        s = s[:-2]
    if "-" in s:                    # dígito de verificación separado por guion
        s = s.split("-")[0]
    s = re.sub(r"\D", "", s)        # deja solo dígitos
    return s


# ─────────────────────────────────────────────
# SELECCIÓN DE LA FILA CORRECTA
# ─────────────────────────────────────────────
def elegir_mejor_registro(registros: list) -> dict | None:
    """
    Un NIT puede devolver varias filas (sociedad principal + establecimientos
    o registros viejos NO MATRICULADO con CIIU 9999). Elegimos la mejor con
    este orden de prioridad:
        1. estado_matricula == 'ACTIVA'
        2. CIIU informativo (distinto de 9999/0000)
        3. año de última renovación más reciente
    """
    if not registros:
        return None

    def puntaje(r: dict):
        activa = 1 if str(r.get("estado_matricula", "")).upper() == "ACTIVA" else 0
        ciiu = str(r.get("cod_ciiu_act_econ_pri", "")).strip()
        ciiu_valido = 1 if ciiu not in CIIU_BASURA else 0
        try:
            ano = int(r.get("ultimo_ano_renovado", "0") or 0)
        except (ValueError, TypeError):
            ano = 0
        return (activa, ciiu_valido, ano)

    return max(registros, key=puntaje)


# ─────────────────────────────────────────────
# CONSULTA A LA API
# ─────────────────────────────────────────────
def consultar_ciiu(nit_limpio: str, session: requests.Session) -> dict:
    """
    Devuelve un dict: {'ciiu': ..., 'razon': ..., 'estado': ...}
    con valores de control si algo falla:
        'No encontrado'     → el NIT no está en el dataset
        'Requiere revisión' → error de red tras los reintentos
    """
    if not nit_limpio:
        return {"ciiu": "Requiere revisión", "razon": "", "estado": "NIT vacío"}

    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}

    def _pedir(nit: str):
        resp = session.get(
            SOCRATA_URL, params={"nit": nit}, headers=headers, timeout=TIMEOUT_S
        )
        resp.raise_for_status()
        return resp.json()

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            datos = _pedir(nit_limpio)

            # Fallback: si vino vacío y el NIT tiene 10 dígitos, puede que
            # incluyera el dígito de verificación pegado → reintentar sin él.
            if not datos and len(nit_limpio) == 10:
                datos = _pedir(nit_limpio[:-1])

            if not datos:
                return {"ciiu": "No encontrado", "razon": "", "estado": "No encontrado"}

            mejor = elegir_mejor_registro(datos)
            return {
                "ciiu": str(mejor.get("cod_ciiu_act_econ_pri", "") or "Sin CIIU"),
                "razon": str(mejor.get("razon_social", "") or ""),
                "estado": str(mejor.get("estado_matricula", "") or ""),
            }

        except requests.RequestException as e:
            log.warning(f"  [NIT {nit_limpio}] Error red (intento {intento}/{MAX_REINTENTOS}): {e}")
            if intento < MAX_REINTENTOS:
                time.sleep(2 * intento)

    return {"ciiu": "Requiere revisión", "razon": "", "estado": "Error de red"}


# ─────────────────────────────────────────────
# PROCESO PRINCIPAL
# ─────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  RUES CIIU (API Datos Abiertos) — Iniciando")
    log.info("=" * 60)

    if not Path(INPUT_FILE).exists():
        log.error(f"No se encontró el archivo de entrada: {INPUT_FILE}")
        return

    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_NAME, dtype={COL_NIT: str})
    total = len(df)
    log.info(f"  Archivo cargado: {total} proveedores.")

    for col in (COL_CIIU, COL_RAZON, COL_ESTADO):
        if col not in df.columns:
            df[col] = ""

    # Filas pendientes: CIIU vacío, 0, o marcado para revisión en una corrida previa
    pendientes_mask = (
        df[COL_CIIU].isna()
        | (df[COL_CIIU].astype(str).str.strip().isin(["", "0", "Requiere revisión"]))
    )
    indices = df[pendientes_mask].index.tolist()
    log.info(f"  Pendientes de consulta: {len(indices)} de {total}")

    if not indices:
        log.info("  Nada pendiente. Guardando y saliendo.")
        df.to_excel(OUTPUT_FILE, sheet_name=SHEET_NAME, index=False)
        return

    session = requests.Session()
    session.headers.update({"User-Agent": "consulta-ciiu/1.0"})

    encontrados = no_encontrados = errores = 0

    for n, idx in enumerate(indices, start=1):
        nit_raw = df.at[idx, COL_NIT]
        nit = limpiar_nit(nit_raw)
        log.info(f"  [{n}/{len(indices)}] NIT {nit_raw} → {nit}")

        r = consultar_ciiu(nit, session)
        df.at[idx, COL_CIIU]   = r["ciiu"]
        df.at[idx, COL_RAZON]  = r["razon"]
        df.at[idx, COL_ESTADO] = r["estado"]

        if r["ciiu"] in ("No encontrado",):
            no_encontrados += 1
        elif r["ciiu"] in ("Requiere revisión",):
            errores += 1
        else:
            encontrados += 1
            log.info(f"        → CIIU {r['ciiu']}  ({r['razon'][:40]})")

        if n % CHECKPOINT_CADA == 0:
            df.to_excel(OUTPUT_FILE, sheet_name=SHEET_NAME, index=False)
            log.info(f"  [Checkpoint] {n} procesados, progreso guardado.")

        time.sleep(DELAY_ENTRE_CONSULTAS)

    df.to_excel(OUTPUT_FILE, sheet_name=SHEET_NAME, index=False)

    log.info("=" * 60)
    log.info("  PROCESO COMPLETADO")
    log.info(f"  Encontrados     : {encontrados}")
    log.info(f"  No encontrados  : {no_encontrados}")
    log.info(f"  Requieren rev.  : {errores}")
    log.info(f"  Salida          : {OUTPUT_FILE}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
