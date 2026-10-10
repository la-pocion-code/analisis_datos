"""
diagnosticar_direcciones_terceros.py — Mide lo que hace falta para GEORREFERENCIAR a los clientes
(mapas de calor por país, departamento, ciudad y comuna) ANTES de construir nada. SOLO LECTURA:
no toca el ETL ni la BD, y no llama a ningún geocodificador.

Contexto (2026-10-10): se quiere un mapa de calor de los clientes que compran. País, departamento
y ciudad ya salen de Odoo sin coste; la COMUNA y un mapa de puntos obligan a geocodificar, y eso se
paga POR DIRECCIÓN. Este script da la cifra que decide el coste y el criterio de qué cliente se
puede listar con su dirección (empresa) y cuál solo agregado (persona natural, Ley 1581).

Qué reporta:
  1. Qué campos de dirección, coordenada e identidad existen DE VERDAD en este Odoo (fields_get).
  2. Si `res.city` trae un CÓDIGO DANE aparte del display name, y el código ISO de `res.country`.
  3. Cobertura de cada campo sobre los clientes CON VENTA.
  4. Empresa vs persona: `is_company` × tipo de identificación × `tipo_cliente`, con su venta.
  5. Reparto por país (los 5 países donde se vende).
  6. Top de ciudades colombianas por clientes con venta, con el acumulado.
  7. DIRECCIONES DISTINTAS a geocodificar (lo que se paga), en total y por grupos de ciudades.
  8. Clientes nuevos por mes (lo que costaría mantenerlo al día).
  9. La tabla de coste por proveedor.

Uso:  python diagnosticar_direcciones_terceros.py
      python diagnosticar_direcciones_terceros.py --muestra 3000   # aproximación rápida
"""
import re
import sys
import logging
import warnings
import argparse
import unicodedata
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import pandas as pd
from classes.db_loader import DBLoader

logging.disable(logging.INFO)

ANIO_PESO = 2025          # año CERRADO para ponderar por venta
CHUNK_READ = 1000
MM = 1e6
CTX_INACTIVOS = {"active_test": False}

# Patrones para DESCUBRIR campos (bloque 1). Amplio a propósito: es exploratorio.
PATRON = ("street", "direcc", "city", "ciudad", "municip", "zip", "postal", "state", "depart",
          "country", "pais", "dane", "barrio", "comuna", "localidad", "latitud", "longitud",
          "latitude", "longitude", "geo", "company", "ident", "vat", "type")

# Ciudades donde la COMUNA/LOCALIDAD tiene polígonos oficiales en datos abiertos. Es el grupo que
# decide el coste de la fase 3 si solo se geocodifica donde la comuna importa. Se comparan por
# nombre NORMALIZADO contra `dim_tercero.ciudad`, que desde el 2026-09-07 sale del catálogo
# res.city ('BOGOTÁ, D.C.', 'MEDELLÍN', 'CARTAGENA DE INDIAS'…).
CIUDADES_COMUNA = ("bogota d c", "medellin", "cali", "santiago de cali", "barranquilla",
                   "cartagena de indias", "bucaramanga", "cucuta", "san jose de cucuta",
                   "pereira", "manizales", "ibague")

# Precios públicos por 1.000 llamadas (consultados el 2026-10-10; se re-verifican antes de pagar).
#   Google: 10.000 gratis/mes; US$5 hasta 100k; US$4 hasta 500k.  ⛔ solo deja GUARDAR 30 días.
#   Esri stored: ~US$4.  Mapbox permanente: US$5, sin cupo gratis.
def coste_google(n):
    restante, total = max(0, n - 10_000), 0.0
    for tope, precio in ((90_000, 5.0), (400_000, 4.0), (10**12, 3.0)):
        tramo = min(restante, tope)
        total += tramo * precio / 1000
        restante -= tramo
        if restante <= 0:
            break
    return total


def coste_esri(n):
    return n * 4.0 / 1000


def coste_mapbox(n):
    return n * 5.0 / 1000


def _p(df, vacio="(sin filas)"):
    if df is None:
        return "ERROR EN LA CONSULTA (ver log)"
    if isinstance(df, pd.DataFrame) and df.empty:
        return vacio
    return df.to_string(index=False)


def _titulo(n, txt):
    print()
    print("=" * 110)
    print(f"{n}) {txt}")
    print("=" * 110)


def _norm(s):
    """minúsculas sin tildes, sin puntuación y sin espacios de sobra. SOLO para comparar."""
    s = ("" if s is None else str(s)).strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = "".join(c if c.isalnum() else " " for c in s.lower())
    return " ".join(s.split())


def _vacio(v):
    if v is None or v is False:
        return True
    if isinstance(v, (list, tuple)):
        return len(v) == 0
    s = str(v).strip()
    return not s or not any(c.isalnum() for c in s)


def _m2o_id(v):
    return int(v[0]) if isinstance(v, (list, tuple)) and v else None


def _m2o_txt(v):
    return v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else None


def _lectura_masiva(meta):
    """Solo campos ALMACENADOS y escalares en una lectura masiva (la lección de `valid_ean`: un
    computado devuelve un valor distinto según el tamaño del lote, sin error)."""
    return bool(meta.get("store")) and meta.get("type") in (
        "char", "text", "many2one", "selection", "boolean", "float", "integer")


def _llave_direccion(r):
    """La dirección tal como se mandaría al geocodificador: calle + ciudad (catálogo) + país.
    Dos terceros con la misma llave se geocodifican UNA vez (caché). Sin calle no hay llave."""
    calle = _norm(r.get("street"))
    if not calle or not any(c.isdigit() for c in calle):
        return None
    ciudad = _m2o_id(r.get("city_id")) or _norm(r.get("city"))
    pais = _m2o_id(r.get("country_id"))
    return (calle, ciudad, pais)


# ══════════════════════════════════════════════════════════════════════════════════════════════
def bloque1_campos(od):
    _titulo(1, "CAMPOS DE DIRECCION, COORDENADA E IDENTIDAD QUE EXISTEN (res.partner.fields_get)")
    fg = od._exec("res.partner", "fields_get", [],
                  {"attributes": ["type", "string", "store", "relation", "selection"]})
    filas = []
    for k, v in sorted(fg.items()):
        etiqueta = _norm(v.get("string"))
        if any(p in k.lower() for p in PATRON) or any(p in etiqueta for p in PATRON):
            filas.append({"campo": k, "type": v.get("type"), "relation": v.get("relation") or "",
                          "store": bool(v.get("store")),
                          "lectura": "MASIVO" if _lectura_masiva(v) else "solo ficha",
                          "etiqueta": (v.get("string") or "")[:40]})
    print(_p(pd.DataFrame(filas)))
    return fg


def bloque2_catalogos(od):
    _titulo(2, "CATALOGOS: ¿res.city trae CODIGO DANE? ¿res.country trae el ISO?")
    out = {"city_code": None, "ciudades": {}, "paises": {}}
    fgc = od._exec("res.city", "fields_get", [], {"attributes": ["type", "string", "store"]})
    cand = [k for k, v in fgc.items()
            if v.get("store") and v.get("type") in ("char", "integer")
            and k not in ("name", "display_name", "__last_update")]
    print(f"  res.city: campos char/integer almacenados -> {sorted(cand)}")
    pedir = ["id", "name", "display_name"] + sorted(cand)
    for r in od.search_read("res.city", [], pedir, limit=5, context=CTX_INACTIVOS):
        print("     " + "  ".join(f"{c}={r.get(c)!r}" for c in pedir))
    # El primer campo cuyo valor tenga forma de código DIVIPOLA (5 dígitos) en la muestra.
    todas = od.search_read("res.city", [], pedir, context=CTX_INACTIVOS)
    for c in sorted(cand):
        con = sum(1 for r in todas if re.fullmatch(r"\d{5}", str(r.get(c) or "").strip()))
        print(f"     campo {c!r}: {con} de {len(todas)} con forma de código DANE (5 dígitos)")
        if con and out["city_code"] is None and con >= 0.9 * len(todas):
            out["city_code"] = c
    con_dn = sum(1 for r in todas if re.search(r"\((\d{5})\)\s*$", str(r.get("display_name") or "")))
    print(f"     display_name con '(NNNNN)' al final: {con_dn} de {len(todas)}")
    out["ciudades"] = {r["id"]: r for r in todas}
    paises = od.search_read("res.country", [], ["id", "name", "code"], context=CTX_INACTIVOS)
    out["paises"] = {r["id"]: r for r in paises}
    print(f"\n  res.country: {len(paises)} países con `code` ISO-2 (p.ej. "
          f"{[(p['name'], p['code']) for p in paises if p.get('code') in ('CO', 'EC', 'PE')]})")
    print(f"\n  => campo DANE elegido en res.city: {out['city_code']!r}")
    return out


def bloque3_cobertura(datos, campos):
    _titulo(3, f"COBERTURA SOBRE LOS {len(datos)} CLIENTES CON VENTA")
    filas = []
    for c in campos:
        con = sum(1 for r in datos if not _vacio(r.get(c)))
        filas.append({"campo": c, "con_dato": con,
                      "pct": round(100 * con / len(datos), 2) if datos else None})
    print(_p(pd.DataFrame(filas).sort_values("pct", ascending=False)))
    con_num = sum(1 for r in datos if _llave_direccion(r))
    print(f"\n  street con al menos un dígito (geocodificable en principio): {con_num} "
          f"({100*con_num/len(datos):.2f} %)")
    if "partner_latitude" in campos:
        coords = sum(1 for r in datos if (r.get("partner_latitude") or 0) != 0)
        print(f"  partner_latitude distinta de 0 (Odoo YA trae coordenada): {coords}")


def bloque4_empresa_persona(datos, lo, venta, campo_ident):
    _titulo(4, "EMPRESA vs PERSONA — is_company x tipo de identificacion x tipo_cliente")
    dtc = lo.consultar("SELECT tercero_id, tipo_cliente FROM marts.dim_tercero")
    tipo = {} if dtc is None else {int(r.tercero_id): r.tipo_cliente for r in dtc.itertuples()}
    agg = defaultdict(lambda: [0, 0.0])
    for r in datos:
        ident = _m2o_txt(r.get(campo_ident)) if campo_ident else None
        k = (bool(r.get("is_company")), ident or "(sin tipo)", tipo.get(r["id"]) or "(sin tipo_cliente)")
        agg[k][0] += 1
        agg[k][1] += venta.get(r["id"], 0.0)
    filas = [{"is_company": k[0], "identificacion": k[1][:28], "tipo_cliente": str(k[2])[:28],
              "clientes": n, f"venta_{ANIO_PESO}_mm": round(v / MM, 1)}
             for k, (n, v) in agg.items()]
    df = pd.DataFrame(filas).sort_values("clientes", ascending=False)
    print(_p(df.head(40)))
    emp = sum(1 for r in datos if r.get("is_company"))
    print(f"\n  is_company=True: {emp} ({100*emp/len(datos):.2f} %) · False: {len(datos)-emp}")

    # ⚠ `is_company` NO separa B2B de consumidor en este Odoo (medido 2026-10-10: el 83 % de los
    # MAYORISTA NV está en False). La localización colombiana trae `person_type` (Tipo de
    # persona) y `vat_type` (Tipo de documento), almacenados: se miden aquí contra el canal.
    if any("person_type" in r for r in datos):
        _titulo("4b", "person_type x vat_type x tipo_cliente (la localizacion colombiana)")
        agg2 = defaultdict(lambda: [0, 0.0])
        for r in datos:
            k = (r.get("person_type") or "(vacio)", r.get("vat_type") or "(vacio)",
                 tipo.get(r["id"]) or "(sin tipo_cliente)")
            agg2[k][0] += 1
            agg2[k][1] += venta.get(r["id"], 0.0)
        filas2 = [{"person_type": k[0], "vat_type": str(k[1])[:14], "tipo_cliente": str(k[2])[:26],
                   "clientes": n, f"venta_{ANIO_PESO}_mm": round(v / MM, 1)}
                  for k, (n, v) in agg2.items()]
        print(_p(pd.DataFrame(filas2).sort_values("clientes", ascending=False).head(40)))
        print("\n  -- partner_type_id de la FICHA (el tipo_cliente del DW sale del asiento) --")
        c = Counter(_m2o_txt(r.get("partner_type_id")) or "(vacio)" for r in datos)
        for nombre, n in c.most_common(20):
            print(f"     {n:7d}  {nombre}")


def bloque5_paises(datos, venta, cat):
    _titulo(5, "REPARTO POR PAIS (clientes con venta)")
    agg = defaultdict(lambda: [0, 0, 0.0])
    for r in datos:
        pid = _m2o_id(r.get("country_id"))
        code = (cat["paises"].get(pid) or {}).get("code") if pid else None
        k = (code or "(sin pais)", _m2o_txt(r.get("country_id")) or "")
        agg[k][0] += 1
        agg[k][1] += 1 if _llave_direccion(r) else 0
        agg[k][2] += venta.get(r["id"], 0.0)
    filas = [{"iso": k[0], "pais": k[1][:24], "clientes": a[0], "con_calle": a[1],
              f"venta_{ANIO_PESO}_mm": round(a[2] / MM, 1)} for k, a in agg.items()]
    print(_p(pd.DataFrame(filas).sort_values("clientes", ascending=False)))


def bloque6_ciudades(lo):
    _titulo(6, "TOP CIUDADES (dim_tercero.ciudad) POR CLIENTES CON VENTA, CON ACUMULADO")
    df = lo.consultar("""
        WITH c AS (SELECT DISTINCT tercero_id FROM marts.mv_ventas_mes WHERE tercero_id > 0)
        SELECT COALESCE(t.ciudad, '(sin ciudad)') AS ciudad, COUNT(*) AS clientes
        FROM c JOIN marts.dim_tercero t USING (tercero_id)
        GROUP BY 1 ORDER BY 2 DESC
    """)
    if df is None or df.empty:
        print("(sin filas)")
        return
    tot = df.clientes.sum()
    df["pct"] = (100 * df.clientes / tot).round(2)
    df["pct_acum"] = df.pct.cumsum().round(2)
    print(_p(df.head(30)))
    print(f"\n  {len(df)} ciudades distintas para {tot} clientes con venta.")


def bloque7_direcciones(datos, cat):
    _titulo(7, "DIRECCIONES DISTINTAS A GEOCODIFICAR (lo que se paga: una llamada por dirección)")
    llaves = [(r, _llave_direccion(r)) for r in datos]
    validas = [(r, k) for r, k in llaves if k]
    distintas = {k for _, k in validas}

    def _ciudad_norm(r):
        cid = _m2o_id(r.get("city_id"))
        nombre = (cat["ciudades"].get(cid) or {}).get("name") if cid else r.get("city")
        return _norm(nombre)

    grupos = {
        "TODOS los clientes con calle": validas,
        "solo EMPRESAS (is_company)": [(r, k) for r, k in validas if r.get("is_company")],
        "solo ciudades con COMUNA (10 grandes)": [(r, k) for r, k in validas
                                                   if _ciudad_norm(r) in CIUDADES_COMUNA],
        "solo COLOMBIA": [(r, k) for r, k in validas
                          if (cat["paises"].get(_m2o_id(r.get("country_id"))) or {}).get("code") == "CO"],
    }
    filas = []
    for nombre, filas_g in grupos.items():
        n = len({k for _, k in filas_g})
        filas.append({"grupo": nombre, "clientes": len(filas_g), "direcciones_distintas": n,
                      "google_usd_1_corrida": round(coste_google(n)),
                      "esri_usd": round(coste_esri(n)), "mapbox_usd": round(coste_mapbox(n))})
    print(_p(pd.DataFrame(filas)))
    print(f"\n  Sin calle utilizable (no se geocodifican, van por ciudad): {len(datos) - len(validas)}")
    print(f"  Llaves compartidas: {len(validas) - len(distintas)} clientes repiten la dirección de otro.")
    print("\n  ⛔ Google: el coste es de UNA corrida, pero sus términos obligan a BORRAR la coordenada")
    print("     a los 30 días. Para guardarla en el almacén hace falta Esri (stored) o Mapbox (permanente).")
    print("\n  -- muestra de 15 calles tal como vienen (para ver el formato) --")
    for r, k in validas[:15]:
        print(f"     {str(r.get('street'))[:60]!r:64s} | {str(_m2o_txt(r.get('city_id')) or r.get('city'))[:28]}")


def bloque8_nuevos(lo):
    _titulo(8, "CLIENTES NUEVOS POR MES (lo que costaría mantenerlo al dia)")
    print(_p(lo.consultar("""
        SELECT primer_periodo AS periodo, COUNT(*) AS clientes_nuevos
        FROM marts.mv_ventas_cliente_primera
        WHERE tercero_id > 0 AND primer_periodo >= 202504
        GROUP BY 1 ORDER BY 1
    """)))
    print("\n  (una corrida mensual geocodifica solo estos, y solo si su dirección no está ya en caché)")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def main(muestra=0):
    lo = DBLoader()
    dfv = lo.consultar(f"""
        SELECT tercero_id,
               COALESCE(SUM(venta) FILTER (WHERE anio = {ANIO_PESO}), 0) AS venta_peso
        FROM marts.mv_ventas_mes
        WHERE tercero_id > 0
        GROUP BY 1
    """)
    venta = {int(r.tercero_id): float(r.venta_peso or 0.0) for r in dfv.itertuples()}
    print(f"Clientes con venta en marts.mv_ventas_mes: {len(venta)}")

    from etl_dw_marts import conectar_odoo, Odoo
    db, uid, pw, models = conectar_odoo()
    od = Odoo(db, uid, pw, models)

    fg = bloque1_campos(od)
    cat = bloque2_catalogos(od)

    # Lo que se lee en masa: solo los campos que EXISTEN y son almacenados.
    deseados = ["street", "street2", "city", "city_id", "state_id", "country_id", "zip", "zip_id",
                "partner_latitude", "partner_longitude", "is_company", "type", "parent_id",
                "l10n_latam_identification_type_id", "vat", "person_type", "vat_type",
                "partner_type_id"]
    campos = [c for c in deseados if c in fg and _lectura_masiva(fg[c])]
    faltan = [c for c in deseados if c not in campos]
    print(f"\n  Se leen en masa: {campos}")
    print(f"  NO existen o no son masivos: {faltan}")
    campo_ident = "l10n_latam_identification_type_id" if "l10n_latam_identification_type_id" in campos else None

    ids = sorted(venta)
    if muestra:
        ids = ids[:muestra]
    print(f"\n  ... leyendo {len(ids)} clientes de Odoo en lotes de {CHUNK_READ} ...")
    datos = od.read("res.partner", ids, ["id"] + campos, chunk=CHUNK_READ, context=CTX_INACTIVOS)
    print(f"  ... {len(datos)} fichas leidas.")

    bloque3_cobertura(datos, campos)
    bloque4_empresa_persona(datos, lo, venta, campo_ident)
    bloque5_paises(datos, venta, cat)
    bloque6_ciudades(lo)
    bloque7_direcciones(datos, cat)
    bloque8_nuevos(lo)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=0,
                    help="limita el read de Odoo a los N primeros clientes con venta (0 = todos)")
    a = ap.parse_args()
    main(muestra=a.muestra)
