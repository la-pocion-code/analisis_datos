"""
Extractor de nómina electrónica — desprendibles de pago en PDF.

Recorre una carpeta raíz con subcarpetas por mes, parsea cada PDF de desprendible
y genera tres CSVs:

  1. nomina_largo.csv     — un registro por (empleado, mes, concepto)
  2. nomina_ancho.csv     — un registro por (empleado, mes), con una columna por concepto
  3. nomina_auditoria.csv — sumas parseadas vs totales del PDF, con flag de discrepancia

Uso:
    python extraer_nomina.py "D:\\Downloads\\SOPORTES NE-20260519T163657Z-3-001\\SOPORTES NE" --salida "D:\\Downloads\\salida_nomina"

Requisitos:
    pip install pdfplumber pandas
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict

import pdfplumber
import pandas as pd


# ---------- Patrones ----------
RE_PERIODO       = re.compile(r"Periodo Pago:\s*(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})")
RE_EMPLEADO      = re.compile(r"Empleado:\s*(.+?)\s*$", re.MULTILINE)
RE_CEDULA        = re.compile(r"C[eé]dula de ciudadan[ií]a:\s*(\d+)")
RE_CUNE          = re.compile(r"CUNE:\s*([0-9a-fA-F]+)")
RE_SALARIO_BASE  = re.compile(r"Salario Base:\s*([\d,\.]+)")
RE_TOTALES       = re.compile(r"Totales COP\s+([\d,\.]+)\s+([\d,\.]+)")
RE_NETO          = re.compile(r"Total Neto COP\s+([\d,\.]+)")
RE_FECHA_INGRESO = re.compile(r"Fecha Ingreso:\s*(\d{4}-\d{2}-\d{2})")
RE_CONSECUTIVO   = re.compile(r"Consecutivo:\s*(\S+)")
RE_AMOUNT        = re.compile(r"^[\d,]+(?:\.\d+)?$")

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def parse_amount(s: str | None) -> float:
    if not s:
        return 0.0
    return float(s.replace(",", ""))


def normalize_concept(desc: str) -> str:
    """Normaliza un concepto para que casos con fechas embebidas
    (ej. 'Incapacidad desde 2025-06-19 hasta 2025-06-20') se mapeen al mismo
    concepto en el CSV ancho."""
    d = desc.strip()
    d = re.sub(r"\s*desde\s+\d{4}-\d{2}-\d{2}\s+hasta\s+\d{4}-\d{2}-\d{2}", "", d, flags=re.IGNORECASE)
    d = re.sub(r"\s*desde\s+\d{4}-\d{2}-\d{2}", "", d, flags=re.IGNORECASE)
    d = re.sub(r"\d{4}-\d{2}-\d{2}", "", d)
    d = re.sub(r"\s+", " ", d).strip()
    return d


def _column_centers(page) -> tuple[float | None, float | None]:
    """Localiza el centro X de las columnas Devengados y Deducciones."""
    x_dev = x_ded = None
    for w in page.extract_words():
        if w["text"] == "Devengados":
            x_dev = (w["x0"] + w["x1"]) / 2
        elif w["text"] == "Deducciones":
            x_ded = (w["x0"] + w["x1"]) / 2
    return x_dev, x_ded


def _group_words_by_line(words, y_tol: float = 2.0):
    """Agrupa palabras por línea usando tolerancia en Y."""
    lines: dict[float, list] = {}
    for w in words:
        top = w["top"]
        bucket = None
        for k in lines:
            if abs(k - top) <= y_tol:
                bucket = k
                break
        if bucket is None:
            lines[top] = []
            bucket = top
        lines[bucket].append(w)
    sorted_lines = sorted(lines.items(), key=lambda kv: kv[0])
    return [(top, sorted(ws, key=lambda w: w["x0"])) for top, ws in sorted_lines]


def parse_pdf(path: Path) -> dict | None:
    """Devuelve un dict con metadata + lista de conceptos. None si falla."""
    try:
        with pdfplumber.open(str(path)) as pdf:
            full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            page = pdf.pages[0]
            words = page.extract_words(use_text_flow=True)
            x_dev, x_ded = _column_centers(page)
    except Exception as e:
        return {"_error": f"No se pudo abrir el PDF: {e}", "_archivo": str(path)}

    if x_dev is None or x_ded is None:
        return {"_error": "No se detectaron columnas Devengados/Deducciones", "_archivo": str(path)}

    meta = {"_archivo": str(path)}
    m = RE_PERIODO.search(full_text)
    meta["periodo_inicio"] = m.group(1) if m else None
    meta["periodo_fin"]    = m.group(2) if m else None
    m = RE_EMPLEADO.search(full_text);      meta["empleado"]      = m.group(1).strip() if m else None
    m = RE_CEDULA.search(full_text);        meta["cedula"]        = m.group(1) if m else None
    m = RE_CUNE.search(full_text);          meta["cune"]          = m.group(1) if m else None
    m = RE_SALARIO_BASE.search(full_text);  meta["salario_base"]  = parse_amount(m.group(1)) if m else None
    m = RE_FECHA_INGRESO.search(full_text); meta["fecha_ingreso"] = m.group(1) if m else None
    m = RE_CONSECUTIVO.search(full_text);   meta["consecutivo"]   = m.group(1) if m else None
    m = RE_TOTALES.search(full_text)
    meta["total_devengados_pdf"]  = parse_amount(m.group(1)) if m else None
    meta["total_deducciones_pdf"] = parse_amount(m.group(2)) if m else None
    m = RE_NETO.search(full_text)
    meta["total_neto_pdf"] = parse_amount(m.group(1)) if m else None

    # Conceptos
    line_groups = _group_words_by_line(words)
    start_idx = end_idx = None
    for i, (top, ws) in enumerate(line_groups):
        text = " ".join(w["text"] for w in ws)
        if "Descripción" in text and "Devengados" in text:
            start_idx = i + 1
        elif start_idx is not None and ("Totales COP" in text or "Total Neto" in text):
            end_idx = i
            break
    if start_idx is None:
        meta["_error"] = "No se encontró cabecera de tabla"
        meta["conceptos"] = []
        return meta
    if end_idx is None:
        end_idx = len(line_groups)

    conceptos = []
    for top, ws in line_groups[start_idx:end_idx]:
        montos, descripcion_words = [], []
        for w in ws:
            t = w["text"]
            if RE_AMOUNT.match(t) and any(c.isdigit() for c in t):
                montos.append(w)
            else:
                descripcion_words.append(w)
        if not descripcion_words:
            continue
        descripcion = " ".join(w["text"] for w in descripcion_words).strip()
        if not descripcion:
            continue

        devengado = deduccion = 0.0
        unidades = porcentaje = ""
        for w in montos:
            cx = (w["x0"] + w["x1"]) / 2
            val = float(w["text"].replace(",", ""))
            d_dev = abs(cx - x_dev)
            d_ded = abs(cx - x_ded)
            if min(d_dev, d_ded) > 60:  # ni devengado ni deducción → unidades / porcentaje
                if not unidades:
                    unidades = w["text"]
                else:
                    porcentaje = w["text"]
            elif d_dev < d_ded:
                devengado = val
            else:
                deduccion = val

        conceptos.append({
            "descripcion": descripcion,
            "descripcion_normalizada": normalize_concept(descripcion),
            "unidades": unidades,
            "porcentaje": porcentaje,
            "devengado": devengado,
            "deduccion": deduccion,
        })

    meta["conceptos"] = conceptos
    return meta


# ---------- Walk de la carpeta ----------
def discover_pdfs(root: Path) -> list[tuple[str, Path]]:
    """Devuelve una lista de (nombre_carpeta_mes, ruta_pdf)."""
    results = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            for pdf in sorted(child.glob("*.pdf")):
                results.append((child.name, pdf))
    # Si no hay subcarpetas, también recorre la raíz
    if not results:
        for pdf in sorted(root.glob("*.pdf")):
            results.append((root.name, pdf))
    return results


def main():
    parser = argparse.ArgumentParser(description="Extrae nómina de desprendibles PDF a CSV.")
    parser.add_argument("ruta", help="Carpeta raíz que contiene subcarpetas por mes")
    parser.add_argument("--salida", default="./salida_nomina", help="Carpeta donde se guardarán los CSVs")
    args = parser.parse_args()

    root = Path(args.ruta).expanduser().resolve()
    if not root.exists():
        print(f"ERROR: no existe la ruta {root}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.salida).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Raíz:   {root}")
    print(f"Salida: {out_dir}\n")

    pdfs = discover_pdfs(root)
    print(f"PDFs encontrados: {len(pdfs)}")

    registros_largo = []
    registros_auditoria = []
    cunes_vistos: set[str] = set()
    duplicados = 0
    sin_cune = 0
    errores = []

    for carpeta_mes, pdf_path in pdfs:
        rel = pdf_path.relative_to(root)
        result = parse_pdf(pdf_path)
        if result is None or "_error" in result:
            errores.append((str(rel), result.get("_error", "?") if result else "?"))
            continue

        # Deduplicación por CUNE
        cune = result.get("cune")
        if cune:
            if cune in cunes_vistos:
                duplicados += 1
                continue
            cunes_vistos.add(cune)
        else:
            sin_cune += 1  # Sin CUNE no podemos deduplicar; lo procesamos igual

        # Período del PDF (fuente de verdad), no el nombre de la carpeta
        per_ini = result.get("periodo_inicio")
        if per_ini:
            anio_mes = per_ini[:7]                            # 'YYYY-MM'
            mes_num = int(per_ini[5:7])
            mes_nombre = MESES_ES[mes_num]
            anio = int(per_ini[:4])
        else:
            anio_mes = mes_nombre = None
            anio = mes_num = None

        meta_comun = {
            "anio": anio,
            "mes_num": mes_num,
            "anio_mes": anio_mes,
            "mes_nombre": mes_nombre,
            "carpeta_origen": carpeta_mes,
            "archivo": str(rel),
            "empleado": result.get("empleado"),
            "cedula": result.get("cedula"),
            "cune": cune,
            "consecutivo": result.get("consecutivo"),
            "periodo_inicio": result.get("periodo_inicio"),
            "periodo_fin": result.get("periodo_fin"),
            "fecha_ingreso": result.get("fecha_ingreso"),
            "salario_base": result.get("salario_base"),
        }

        # Largo: una fila por concepto
        for c in result["conceptos"]:
            registros_largo.append({
                **meta_comun,
                "descripcion": c["descripcion"],
                "concepto": c["descripcion_normalizada"],
                "unidades": c["unidades"],
                "porcentaje": c["porcentaje"],
                "devengado": c["devengado"],
                "deduccion": c["deduccion"],
            })

        # Auditoría: comparar suma parseada vs total del PDF
        sum_dev = sum(c["devengado"] for c in result["conceptos"])
        sum_ded = sum(c["deduccion"] for c in result["conceptos"])
        tot_dev = result.get("total_devengados_pdf") or 0.0
        tot_ded = result.get("total_deducciones_pdf") or 0.0
        tot_net = result.get("total_neto_pdf") or 0.0
        diff_dev = sum_dev - tot_dev
        diff_ded = sum_ded - tot_ded
        # Tolerancia: 2 pesos por redondeos del PDF
        flag = "OK"
        if abs(diff_dev) > 2 or abs(diff_ded) > 2:
            flag = "REVISAR"
        registros_auditoria.append({
            **meta_comun,
            "suma_devengados_parseado": sum_dev,
            "total_devengados_pdf": tot_dev,
            "diff_devengados": round(diff_dev, 2),
            "suma_deducciones_parseado": sum_ded,
            "total_deducciones_pdf": tot_ded,
            "diff_deducciones": round(diff_ded, 2),
            "total_neto_pdf": tot_net,
            "neto_calculado": round(sum_dev - sum_ded, 2),
            "flag": flag,
        })

    df_largo = pd.DataFrame(registros_largo)
    df_audit = pd.DataFrame(registros_auditoria)

    # CSV ancho: pivot por concepto
    if not df_largo.empty:
        # Devengados (positivo) y deducciones (negativo) en columnas separadas
        df_dev = (df_largo[df_largo["devengado"] > 0]
                  .pivot_table(index=["anio_mes", "mes_num", "mes_nombre", "cedula", "empleado"],
                               columns="concepto", values="devengado", aggfunc="sum", fill_value=0))
        df_dev.columns = [f"DEV_{c}" for c in df_dev.columns]
        df_ded = (df_largo[df_largo["deduccion"] > 0]
                  .pivot_table(index=["anio_mes", "mes_num", "mes_nombre", "cedula", "empleado"],
                               columns="concepto", values="deduccion", aggfunc="sum", fill_value=0))
        df_ded.columns = [f"DED_{c}" for c in df_ded.columns]
        df_ancho = df_dev.join(df_ded, how="outer").fillna(0).reset_index()
        df_ancho["TOTAL_DEVENGADO"] = df_ancho.filter(like="DEV_").sum(axis=1)
        df_ancho["TOTAL_DEDUCCION"] = df_ancho.filter(like="DED_").sum(axis=1)
        df_ancho["TOTAL_NETO"] = df_ancho["TOTAL_DEVENGADO"] - df_ancho["TOTAL_DEDUCCION"]
        df_ancho = df_ancho.sort_values(["cedula", "anio_mes"])
    else:
        df_ancho = pd.DataFrame()

    # Escritura
    df_largo.to_csv(out_dir / "nomina_largo.csv",     index=False, encoding="utf-8-sig")
    df_ancho.to_csv(out_dir / "nomina_ancho.csv",     index=False, encoding="utf-8-sig")
    df_audit.to_csv(out_dir / "nomina_auditoria.csv", index=False, encoding="utf-8-sig")

    # Resumen
    print(f"\n--- Resumen ---")
    print(f"  PDFs procesados:        {len(pdfs) - len(errores) - duplicados}")
    print(f"  Duplicados (mismo CUNE):{duplicados}")
    print(f"  Sin CUNE detectado:     {sin_cune}")
    print(f"  Errores:                {len(errores)}")
    if not df_audit.empty:
        n_revisar = (df_audit["flag"] == "REVISAR").sum()
        print(f"  Discrepancias suma vs total del PDF: {n_revisar} (ver nomina_auditoria.csv)")
    if errores:
        print("\n  Detalle de errores:")
        for nombre, err in errores[:20]:
            print(f"    - {nombre}: {err}")

    print(f"\nArchivos generados en {out_dir}:")
    print(f"  - nomina_largo.csv     ({len(df_largo)} filas)")
    print(f"  - nomina_ancho.csv     ({len(df_ancho)} filas)")
    print(f"  - nomina_auditoria.csv ({len(df_audit)} filas)")


if __name__ == "__main__":
    main()