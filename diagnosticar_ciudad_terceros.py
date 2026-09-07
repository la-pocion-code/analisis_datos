"""
diagnosticar_ciudad_terceros.py — Audita de dónde sale `marts.dim_tercero.ciudad` y mide cuánto
mejora si el dato ESTRUCTURADO de Odoo (el que se elige de un catálogo) pasa por delante del texto
libre digitado a mano. SOLO LECTURA (no toca el ETL ni la BD). Reusable.

Contexto: hoy `dim_tercero.ciudad` = `res.partner.city` VERBATIM, y nada más
(etl_dw_marts.py::cargar_terceros y ::refrescar_dimensiones, duplicado en las dos rutas).
`city` es un char de TEXTO LIBRE: no está validado contra ningún catálogo.

Caso que motivó el script:
  ROSSEMARKET SAS (NIT 901877868) sale SIN ciudad en el DW, pero su ficha de Odoo muestra Cartagena
  en un campo con forma de many2one ("CARTAGENA (1300...") y un municipio de la localización
  colombiana ("130001-Urbano, CARTAGENA DE INDIAS, Bolívar"). El char `city` está vacío: es el caso
  típico de datos cargados sobre el many2one sin disparar el onchange que copia el nombre.

Y la razón de fondo para no fiarse del texto libre: quien vende por SHOPIFY digita la ciudad a mano,
así que ese campo produce variantes del mismo municipio ('bogota', 'Bogotá D.C.', 'BOGOTA', typos).

Qué reporta:
  1. Qué campos de ubicación existen DE VERDAD en este Odoo (fields_get; no se adivinan nombres).
  2. Los catálogos destino de los many2one: su `name` frente a su display_name.
  3. La ficha CRUDA del caso (ROSSEMARKET) al lado de su fila actual en dim_tercero.
  4. Cobertura de cada campo en Odoo: catálogo completo y clientes CON VENTA.
  5. Cobertura en Postgres de ciudad/departamento/pais: bruta y PONDERADA POR VENTA.
  6. Validación de `departamento` (ya viene de un many2one, pero se comprueba, no se supone).
  7. El "antes -> despues": cuántos terceros CAMBIARÍAN de ciudad y con cuánta venta detrás,
     con la lista de pares texto_libre -> catálogo para revisar a ojo ANTES de escribir nada.
  8. Previsualización de la cascada real, en cuanto exista en el ETL (se importa, no se duplica).

Uso:  python diagnosticar_ciudad_terceros.py
      python diagnosticar_ciudad_terceros.py --muestra 2000   # aproximación rápida
      python diagnosticar_ciudad_terceros.py --sin-odoo        # solo los bloques de Postgres
"""
import sys
import logging
import warnings
import argparse
import unicodedata
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import pandas as pd
from classes.db_loader import DBLoader

logging.disable(logging.INFO)   # el informe se lee mejor sin el log de conexión de DBLoader

NIT_CASO = "901877868"          # ROSSEMARKET SAS, el caso que motivó el script
NOMBRE_CASO = "ROSSEMARKET"
ANIO_PESO = 2025                # año CERRADO para ponderar por venta (2026 se mueve cada 15 min)
CHUNK_READ = 1000               # ids por lote en el read masivo de res.partner
TOPE_PARES = 40                 # pares texto_libre -> catálogo que se listan en el bloque 7
MM = 1e6

# Nombres/etiquetas que delatan un campo de ubicación. Amplio a propósito: el bloque 1 es
# EXPLORATORIO y es mejor que sobre un campo que perderse el que trae el dato.
PATRON_UBIC = ("city", "ciudad", "municip", "mpio", "zip", "postal", "state", "depart",
               "country", "pais", "dane", "localidad", "barrio", "street", "direccion")
# De esos, los que son candidatos a ser LA CIUDAD (no el depto, ni el país, ni la calle).
PATRON_CIUDAD = ("city", "ciudad", "municip", "mpio", "localidad")

CTX_INACTIVOS = {"active_test": False}


def _p(df, vacio="(sin filas)"):
    if df is None:
        return "ERROR EN LA CONSULTA (ver log)"
    if isinstance(df, pd.DataFrame) and df.empty:
        return vacio
    return df.to_string(index=False)


def _titulo(n, txt):
    print()
    print("=" * 115)
    print(f"{n}) {txt}")
    print("=" * 115)


def _norm(s):
    """minúsculas sin tildes ni espacios de sobra. SOLO para COMPARAR, nunca para escribir:
    el valor que va a la base es el del catálogo de Odoo tal cual."""
    s = ("" if s is None else str(s)).strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def _vacio(v):
    """Este valor de Odoo cuenta como 'sin dato'? Cubre None (que ya dejó Odoo._limpiar), la
    cadena vacía, y los rellenos que no son una ciudad ('.', '0', '-')."""
    if v is None or v is False:
        return True
    if isinstance(v, (list, tuple)):
        return len(v) == 0
    s = str(v).strip()
    return not s or not any(c.isalpha() for c in s)


def _tokens(s):
    """Palabras normalizadas, sin puntuacion. 'Bogotá, D.C.' -> {'bogota','d','c'}."""
    n = _norm(s)
    return {t for t in "".join(c if c.isalnum() else " " for c in n).split() if t}


def _misma_ciudad(a, b):
    """Los dos nombres son el MISMO municipio escrito distinto?

    Se decide por SUBCONJUNTO de palabras en cualquiera de los dos sentidos, que es lo que separa
    una variante de grafia de un conflicto de verdad:
      'Cartagena'    vs 'CARTAGENA DE INDIAS'         -> si (nombre corto vs oficial)
      'TUMACO'       vs 'SAN ANDRES DE TUMACO'        -> si
      'Bogotá D.C'   vs 'BOGOTÁ, D.C.'                -> si (misma puntuacion distinta)
      'CALI'         vs 'JAMUNDÍ'                     -> NO: son dos municipios distintos
      'BOGOTÁ, D.C.' vs 'SOGAMOSO'                    -> NO
    Es una prueba de CLASIFICACION para el informe: no decide lo que se escribe en la base.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def _m2o_id(v):
    return int(v[0]) if isinstance(v, (list, tuple)) and v else None


def _m2o_txt(v):
    return v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else None


def _lectura_masiva(meta):
    """Solo los campos ALMACENADOS y escalares se piden en una lectura masiva."""
    return bool(meta.get("store")) and meta.get("type") in ("char", "many2one", "selection", "text")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def bloque1_campos(od):
    """fields_get de res.partner: qué campos de ubicación existen en ESTE Odoo."""
    _titulo(1, "CAMPOS DE UBICACION QUE EXISTEN EN ESTE ODOO (res.partner.fields_get)")
    fg = od._exec("res.partner", "fields_get", [],
                  {"attributes": ["type", "string", "store", "relation"]})
    cands = {}
    for k, v in fg.items():
        etiqueta = _norm(v.get("string"))
        if any(p in k.lower() for p in PATRON_UBIC) or any(p in etiqueta for p in PATRON_UBIC):
            cands[k] = v
    filas = [{"campo": k,
              "type": v.get("type"),
              "relation": v.get("relation") or "",
              "store": bool(v.get("store")),
              "lectura": "MASIVO" if _lectura_masiva(v) else "solo ficha",
              "etiqueta": (v.get("string") or "")[:42]}
             for k, v in sorted(cands.items())]
    print(_p(pd.DataFrame(filas)))
    print()
    print("  AVISO 'solo ficha' = NO se pide en lectura masiva. Es la leccion ya medida con")
    print("  `valid_ean` (ver PRODUCTO_FIELDS en etl_dw_marts.py): un campo COMPUTADO devuelve")
    print("  False para la mayoria en un read grande y True en un lote pequeno, o sea que el")
    print("  resultado dependeria del tamano del lote y no habria ni un error a la vista.")
    return cands


def _roles(cands):
    """Reparte los campos descubiertos: candidatos a CIUDAD (m2o y texto) + el resto de contexto."""
    ciudad_m2o, ciudad_txt, contexto = [], [], []
    for k, v in sorted(cands.items()):
        if not _lectura_masiva(v):
            continue
        es_ciudad = any(p in k.lower() for p in PATRON_CIUDAD)
        if es_ciudad and v.get("type") == "many2one":
            ciudad_m2o.append(k)
        elif es_ciudad and v.get("type") in ("char", "text"):
            ciudad_txt.append(k)
        else:
            contexto.append(k)
    # Preferencia de previsualizacion: el catalogo de ciudades antes que el municipio fiscal
    # (su `name` suele ser el nombre corto: 'CARTAGENA' contra 'CARTAGENA DE INDIAS').
    ciudad_m2o.sort(key=lambda k: (0 if "city" in k.lower() else 1, k))
    return ciudad_m2o, ciudad_txt, contexto


def bloque2_catalogos(od, cands, ciudad_m2o):
    """Los catálogos destino: su `name` es el nombre limpio, o hay que parsear el display_name?"""
    _titulo(2, "CATALOGOS DESTINO DE LOS MANY2ONE — `name` frente a display_name")
    mapas = {}
    for campo in ciudad_m2o:
        rel = cands[campo].get("relation")
        if not rel:
            continue
        try:
            n = od._exec(rel, "search_count", [[]], {"context": CTX_INACTIVOS})
            fgr = od._exec(rel, "fields_get", [], {"attributes": ["type"]})
        except Exception as e:
            print(f"  {campo} -> {rel}: NO accesible ({type(e).__name__})")
            continue
        pedir = ["id"] + [c for c in ("name", "display_name", "code") if c in fgr]
        muestra = od.search_read(rel, [], pedir, limit=6, context=CTX_INACTIVOS)
        print(f"\n  -- {campo} -> {rel}  ({n} registros) --")
        for r in muestra:
            trozos = [f"id={r.get('id')}"] + [f"{c}={r.get(c)!r}" for c in pedir if c != "id"]
            print("     " + "  ".join(trozos))
        campo_nombre = "name" if "name" in fgr else "display_name"
        mapas[campo] = _mapa_catalogo(od, rel, campo_nombre)
        print(f"     (mapa id->{campo_nombre} cargado: {len(mapas[campo])} entradas)")
    print()
    print("  LO QUE SE ESTA MIRANDO: si `name` es el nombre limpio ('CARTAGENA') mientras el")
    print("  display_name lleva el codigo pegado ('CARTAGENA (130001)'), la cascada resuelve el id")
    print("  contra el `name` del CATALOGO y no parsea display names, que es atarse al name_get de")
    print("  Odoo. Son pocas filas y se cachean una vez por proceso.")
    return mapas


def _mapa_catalogo(od, modelo, campo_nombre):
    filas = od.search_read(modelo, [], ["id", campo_nombre], context=CTX_INACTIVOS)
    return {f["id"]: f.get(campo_nombre) for f in filas}


def bloque3_caso(od, lo, cands):
    """La ficha cruda del caso, al lado de su fila en el DW."""
    _titulo(3, f"EL CASO — {NOMBRE_CASO} (NIT {NIT_CASO}): ficha CRUDA de Odoo vs fila del DW")
    dom = ["|", ["vat", "ilike", NIT_CASO], ["name", "ilike", NOMBRE_CASO]]
    ids = od._exec("res.partner", "search", [dom], {"context": CTX_INACTIVOS, "limit": 10})
    if not ids:
        print(f"  (Odoo no devuelve ningun res.partner con NIT {NIT_CASO} ni nombre ~{NOMBRE_CASO})")
        return
    # Aqui SI entran los computados: son 1-2 registros, no una lectura masiva.
    pedir = ["id", "name", "vat", "active", "type", "parent_id", "commercial_partner_id",
             "write_date"] + [k for k in sorted(cands) if k not in ("id", "name")]
    fichas = od.read("res.partner", ids, pedir, context=CTX_INACTIVOS)
    for f in fichas:
        print(f"\n  -- res.partner id={f.get('id')} · {f.get('name')!r} --")
        for k in pedir:
            if k in f:
                print(f"     {k:38s} = {f[k]!r}")
    print("\n  -- su fila HOY en marts.dim_tercero --")
    print(_p(lo.consultar("""
        SELECT tercero_id, nombre, identificacion, ciudad, departamento, pais, cliente_padre
        FROM marts.dim_tercero
        WHERE identificacion ILIKE %s OR nombre ILIKE %s
        ORDER BY tercero_id
    """, [f"%{NIT_CASO}%", f"%{NOMBRE_CASO}%"]),
        vacio="(no esta en dim_tercero: no tiene movimiento contable cargado)"))


def _dom_con_dato(campo, tipo):
    """Dominio 'este campo tiene dato', segun el TIPO del campo.

    OJO: en un many2one hay que preguntar por `!= False`. Un `not in [False, '']` compara el ID
    contra la cadena vacia y Odoo devuelve 0 SIN ERROR, o sea que el campo parece vacio al 100 %
    cuando esta poblado al 99 %. Mordio la primera corrida de este script.
    """
    if tipo == "many2one":
        return [[campo, "!=", False]]
    return [[campo, "not in", [False, ""]]]


def bloque4_cobertura_odoo(od, cands, medidos, datos):
    """Cobertura de cada campo: catálogo completo (search_count) y clientes CON VENTA (del read)."""
    _titulo(4, "COBERTURA EN ODOO POR CAMPO — catalogo completo vs clientes CON VENTA")
    total_cat = od._exec("res.partner", "search_count", [[]], {"context": CTX_INACTIVOS})
    filas = []
    for campo in medidos:
        tipo = (cands.get(campo) or {}).get("type")
        n_cat = od._exec("res.partner", "search_count", [_dom_con_dato(campo, tipo)],
                         {"context": CTX_INACTIVOS})
        con = sum(1 for r in datos if not _vacio(r.get(campo)))
        filas.append({"campo": campo,
                      "tipo": tipo,
                      "catalogo_con_dato": n_cat,
                      "pct_catalogo": round(100 * n_cat / total_cat, 2) if total_cat else None,
                      "clientes_con_dato": con,
                      "pct_clientes": round(100 * con / len(datos), 2) if datos else None})
    print(f"  Catalogo completo: {total_cat} res.partner   ·   leidos con venta: {len(datos)}")
    print()
    df = pd.DataFrame(filas)
    print(_p(df.sort_values("pct_clientes", ascending=False) if not df.empty else df))
    print()
    print("  ESTO CONFIRMA O MATA LA HIPOTESIS: si el char de texto libre tiene cobertura BAJA y")
    print("  el/los many2one la tienen ALTA, el hueco del DW es exactamente eso.")


def bloque5_cobertura_pg(lo):
    _titulo(5, "COBERTURA EN POSTGRES — dim_tercero: bruta y PONDERADA POR VENTA")
    print("  -- 5a) bruta, sobre todo el catalogo cargado --")
    print(_p(lo.consultar("""
        SELECT COUNT(*)                                                                    AS terceros,
               COUNT(*) FILTER (WHERE NULLIF(btrim(COALESCE(ciudad,'')),'')       IS NULL) AS sin_ciudad,
               COUNT(*) FILTER (WHERE NULLIF(btrim(COALESCE(departamento,'')),'') IS NULL) AS sin_departamento,
               COUNT(*) FILTER (WHERE NULLIF(btrim(COALESCE(pais,'')),'')         IS NULL) AS sin_pais,
               COUNT(*) FILTER (WHERE ciudad LIKE '%(%')                                   AS ciudad_con_parentesis,
               COUNT(*) FILTER (WHERE ciudad = '')                                         AS ciudad_cadena_vacia,
               COUNT(DISTINCT ciudad)                                                      AS ciudades_distintas
        FROM marts.dim_tercero
    """)))
    print()
    print(f"  -- 5b) PONDERADA por VENTA {ANIO_PESO} (el juez: quien no factura no importa) --")
    print(_p(lo.consultar(f"""
        WITH v AS (SELECT tercero_id, SUM(venta) AS venta
                     FROM marts.mv_ventas_mes
                    WHERE tercero_id > 0 AND anio = {ANIO_PESO}
                    GROUP BY 1)
        SELECT COUNT(*)                                                                AS clientes,
               COUNT(*) FILTER (WHERE NULLIF(btrim(COALESCE(t.ciudad,'')),'') IS NULL) AS clientes_sin_ciudad,
               ROUND(SUM(v.venta)/{MM}, 1)                                             AS venta_mm,
               ROUND(SUM(v.venta) FILTER (WHERE NULLIF(btrim(COALESCE(t.ciudad,'')),'') IS NULL)/{MM}, 1)
                                                                                       AS venta_sin_ciudad_mm,
               ROUND(100*COALESCE(SUM(v.venta) FILTER (WHERE NULLIF(btrim(COALESCE(t.ciudad,'')),'') IS NULL), 0)
                     / NULLIF(SUM(v.venta),0), 2)                                      AS pct_venta_sin_ciudad
        FROM v JOIN marts.dim_tercero t USING (tercero_id)
    """)))
    print()
    print(f"  -- 5c) el hueco por CANAL (venta {ANIO_PESO}) --")
    print(_p(lo.consultar(f"""
        WITH v AS (SELECT tercero_id, categoria, SUM(venta) AS venta
                     FROM marts.mv_ventas_mes
                    WHERE tercero_id > 0 AND anio = {ANIO_PESO}
                    GROUP BY 1,2)
        SELECT v.categoria,
               ROUND(SUM(v.venta)/{MM}, 1)                                             AS venta_mm,
               ROUND(100*COALESCE(SUM(v.venta) FILTER (WHERE NULLIF(btrim(COALESCE(t.ciudad,'')),'') IS NULL), 0)
                     / NULLIF(SUM(v.venta),0), 2)                                      AS pct_venta_sin_ciudad
        FROM v JOIN marts.dim_tercero t USING (tercero_id)
        GROUP BY 1 ORDER BY 2 DESC
    """)))
    print()
    print("  -- 5d) los valores de `ciudad` mas frecuentes HOY (aqui se ven las variantes) --")
    print(_p(lo.consultar("""
        SELECT ciudad, COUNT(*) AS terceros
        FROM marts.dim_tercero
        WHERE ciudad IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 25
    """)))


def bloque6_departamento(lo, datos, campos_depto):
    """`departamento` ya sale de un many2one (state_id), pero se comprueba, no se supone."""
    _titulo(6, "VALIDACION DE `departamento` — ya viene de un many2one, pero se comprueba")
    print("  -- 6a) integridad del formato (el sufijo del pais) --")
    print(_p(lo.consultar("""
        SELECT COUNT(*)                                                            AS terceros,
               COUNT(*) FILTER (WHERE departamento IS NOT NULL)                    AS con_departamento,
               COUNT(*) FILTER (WHERE departamento LIKE '% (%)')                   AS con_sufijo_pais,
               COUNT(*) FILTER (WHERE departamento IS NOT NULL
                                  AND departamento NOT LIKE '% (%)')               AS sin_sufijo_pais,
               COUNT(DISTINCT departamento)                                        AS departamentos_distintos,
               COUNT(*) FILTER (WHERE ciudad IS NOT NULL AND departamento IS NULL) AS ciudad_sin_departamento
        FROM marts.dim_tercero
    """)))
    print()
    print("  AVISO: `con_sufijo_pais` es el formato que EXIGE el LEFT JOIN de map_zona en")
    print("  sql/marts/24_rol_intranet.sql:62. Si ese conteo cambia, la `zona` de la intranet se")
    print("  queda en NULL para ~97,8 % de los terceros. Por eso NO se toca `departamento`.")
    print()
    print(f"  -- 6b) cobertura ponderada por venta {ANIO_PESO}, y la `zona` que sale de ella --")
    print(_p(lo.consultar(f"""
        WITH v AS (SELECT tercero_id, SUM(venta) AS venta
                     FROM marts.mv_ventas_mes
                    WHERE tercero_id > 0 AND anio = {ANIO_PESO}
                    GROUP BY 1)
        SELECT COUNT(*)                                               AS clientes,
               COUNT(*) FILTER (WHERE t.departamento IS NULL)         AS sin_departamento,
               ROUND(100*COALESCE(SUM(v.venta) FILTER (WHERE t.departamento IS NULL), 0)
                     / NULLIF(SUM(v.venta),0), 2)                     AS pct_venta_sin_depto,
               COUNT(*) FILTER (WHERE lk.zona IS NULL)                AS sin_zona,
               ROUND(100*COALESCE(SUM(v.venta) FILTER (WHERE lk.zona IS NULL), 0)
                     / NULLIF(SUM(v.venta),0), 2)                     AS pct_venta_sin_zona
        FROM v
        JOIN marts.dim_tercero t USING (tercero_id)
        LEFT JOIN marts.v_lk_tercero lk USING (tercero_id)
    """)))
    if datos and campos_depto:
        print()
        print("  -- 6c) en Odoo: cobertura de los campos de departamento/pais --")
        filas = [{"campo": c,
                  "clientes_con_dato": sum(1 for r in datos if not _vacio(r.get(c))),
                  "pct": round(100 * sum(1 for r in datos if not _vacio(r.get(c))) / len(datos), 2)}
                 for c in campos_depto]
        print(_p(pd.DataFrame(filas)))


def bloque7_impacto(datos, ciudad_m2o, ciudad_txt, mapas, venta):
    """El antes -> despues. Lo que autoriza (o no) a escribir."""
    _titulo(7, "IMPACTO DE PONER EL CATALOGO POR DELANTE DEL TEXTO LIBRE")
    if not datos:
        print("  (sin datos de Odoo: correr sin --sin-odoo)")
        return
    orden = ciudad_m2o + ciudad_txt
    print(f"  Orden de la cascada previsualizada:  {' -> '.join(orden)}  -> None")
    print(f"  Poblacion: {len(datos)} clientes con venta.  Peso: venta {ANIO_PESO}.")

    def _valor_catalogo(r):
        """Primer campo de CATALOGO con dato, resuelto contra el `name` del catalogo."""
        for campo in ciudad_m2o:
            cid = _m2o_id(r.get(campo))
            if cid:
                v = (mapas.get(campo) or {}).get(cid) or _m2o_txt(r.get(campo))
                if not _vacio(v):
                    return campo, str(v).strip()
        return None, None

    def _valor_texto(r):
        for campo in ciudad_txt:
            v = r.get(campo)
            if not _vacio(v):
                return campo, str(v).strip()
        return None, None

    ESC_A1 = "A1- misma ciudad, otra grafia (NORMALIZA)"
    ESC_A2 = "A2- ciudades DISTINTAS (conflicto: revisar)"
    ESC_B = "B- los dos y coinciden exacto (sin cambio)"
    ESC_C = "C- solo catalogo (RELLENA un hueco)"
    ESC_D = "D- solo texto libre (se conserva)"
    ESC_E = "E- ninguno (sigue sin ciudad)"

    esc, peso, cambian, conflictos = Counter(), Counter(), [], []
    for r in datos:
        w = venta.get(r.get("id"), 0.0)
        _, v_cat = _valor_catalogo(r)
        _, v_txt = _valor_texto(r)
        if v_cat and v_txt:
            if _norm(v_cat) == _norm(v_txt):
                k = ESC_B
            elif _misma_ciudad(v_cat, v_txt):
                k = ESC_A1
                cambian.append((v_txt, v_cat, w, r.get("id")))
            else:
                k = ESC_A2
                cambian.append((v_txt, v_cat, w, r.get("id")))
                conflictos.append((v_txt, v_cat, w, r.get("id")))
        elif v_cat:
            k = ESC_C
        elif v_txt:
            k = ESC_D
        else:
            k = ESC_E
        esc[k] += 1
        peso[k] += w
    tot_peso = sum(peso.values()) or 1.0
    filas = [{"escalon": k, "clientes": esc[k],
              "pct_clientes": round(100 * esc[k] / len(datos), 2),
              "venta_mm": round(peso[k] / MM, 1),
              "pct_venta": round(100 * peso[k] / tot_peso, 2)}
             for k in sorted(esc)]
    print()
    print(_p(pd.DataFrame(filas)))
    print()
    print(f"  A1 + A2 son las REESCRITURAS ({esc[ESC_A1] + esc[ESC_A2]} clientes). La diferencia")
    print("  entre las dos es la que importa:")
    print("   · A1 es el objetivo del cambio: consolida las variantes en el nombre del catalogo.")
    print(f"   · A2 son CONFLICTOS DE DATO en Odoo ({esc[ESC_A2]} clientes): el catalogo y el texto")
    print("     libre apuntan a municipios distintos y uno de los dos esta mal. La cascada se")
    print("     queda con el del catalogo; NO se puede saber aqui cual es el correcto.")
    print("  Los escalones C (rellena) y D (conserva) no pueden empeorar nada.")
    print()
    print("  -- A2: LOS CONFLICTOS REALES, por venta (esto es lo que hay que revisar en Odoo) --")
    conflictos.sort(key=lambda t: -t[2])
    for v_txt, v_cat, w, pid in conflictos[:TOPE_PARES]:
        print(f"     {w/MM:9.1f} MM  id={pid:<8} texto={v_txt!r:32s} catalogo={v_cat!r}")
    if not conflictos:
        print("     (ninguno: donde los dos campos tienen dato, siempre es el mismo municipio)")
    print()
    print("  -- A2 agrupado: que pares se contradicen y cuantas veces --")
    for (v_txt, v_cat), n in Counter((a, b) for a, b, _, _ in conflictos).most_common(20):
        print(f"     {n:5d}x  texto={v_txt!r:32s} catalogo={v_cat!r}")
    print()
    print("  -- A1: las variantes que se CONSOLIDAN, mas repetidas primero --")
    solo_a1 = [(a, b) for a, b, _, _ in cambian if _misma_ciudad(a, b)]
    for (v_txt, v_cat), n in Counter(solo_a1).most_common(20):
        print(f"     {n:5d}x  {v_txt!r:38s} -> {v_cat!r}")


def bloque8_preview(datos, mapas, ciudad_m2o):
    """Previsualiza la cascada REAL del ETL en cuanto exista. No se duplica la logica."""
    _titulo(8, "PREVISUALIZACION DE LA CASCADA REAL DEL ETL")
    try:
        from etl_dw_marts import _ciudad_tercero
    except ImportError:
        print("  La cascada aun no esta en etl_dw_marts.py: este bloque se activa solo cuando exista.")
        print("  Hasta entonces, el bloque 7 ya mide el impacto con los campos crudos.")
        return
    if not datos:
        print("  (sin datos de Odoo)")
        return
    # El catalogo de ciudades: el mapa del primer many2one descubierto (city_id -> res.city).
    m_ciudades = mapas.get(ciudad_m2o[0], {}) if ciudad_m2o else {}
    res = [_ciudad_tercero(r, m_ciudades) for r in datos]
    sin = sum(1 for v in res if not v)
    print(f"  {len(res) - sin} de {len(res)} clientes con venta quedarian CON ciudad "
          f"({sin} sin ella, {100*sin/len(res):.2f} %).")
    print()
    print("  -- las 30 ciudades resultantes mas frecuentes --")
    for nombre, n in Counter(v for v in res if v).most_common(30):
        print(f"     {n:6d}  {nombre!r}")
    malos = [v for v in res if v and ("(" in v or not any(c.isalpha() for c in v))]
    print()
    print(f"  -- control de basura (parentesis pegados, valores sin letras): {len(malos)} --")
    for v in malos[:15]:
        print(f"     {v!r}")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def main(usar_odoo=True, muestra=0):
    lo = DBLoader()

    # Clientes con venta + su peso. De las MV, que ya estan agregadas e indexadas: un
    # SELECT DISTINCT del hecho tarda >15 min y bloquea el DDL (ver diagnosticar_fecha_venta.py).
    # OJO tercero_id > 0: el -1 es el centinela que exige el indice unico de la MV.
    dfv = lo.consultar(f"""
        SELECT tercero_id,
               COALESCE(SUM(venta) FILTER (WHERE anio = {ANIO_PESO}), 0) AS venta_peso
        FROM marts.mv_ventas_mes
        WHERE tercero_id > 0
        GROUP BY 1
    """)
    venta = {}
    if dfv is not None and not dfv.empty:
        venta = {int(r.tercero_id): float(r.venta_peso or 0.0) for r in dfv.itertuples()}
    print(f"Clientes con venta en marts.mv_ventas_mes: {len(venta)}")

    datos, cands, mapas, ciudad_m2o, ciudad_txt = [], {}, {}, [], []
    if usar_odoo:
        from etl_dw_marts import conectar_odoo, Odoo
        db, uid, pw, models = conectar_odoo()
        od = Odoo(db, uid, pw, models)

        cands = bloque1_campos(od)
        ciudad_m2o, ciudad_txt, contexto = _roles(cands)
        mapas = bloque2_catalogos(od, cands, ciudad_m2o)
        bloque3_caso(od, lo, cands)

        # Lectura masiva de los clientes CON VENTA: una sola pasada da la cobertura exacta, el
        # impacto y la previsualizacion. Solo campos almacenados (ver el aviso del bloque 1).
        extra = [c for c in ("state_id", "country_id", "zip") if c in contexto]
        medidos = ciudad_m2o + ciudad_txt + extra
        ids = sorted(venta)
        if muestra:
            ids = ids[:muestra]
        print(f"\n  ... leyendo {len(ids)} clientes de Odoo ({len(medidos)} campos, "
              f"lotes de {CHUNK_READ}) ...")
        datos = od.read("res.partner", ids, ["id"] + medidos, chunk=CHUNK_READ,
                        context=CTX_INACTIVOS)
        print(f"  ... {len(datos)} fichas leidas.")

        bloque4_cobertura_odoo(od, cands, medidos, datos)

    bloque5_cobertura_pg(lo)
    bloque6_departamento(lo, datos, [c for c in ("state_id", "country_id") if c in cands])
    bloque7_impacto(datos, ciudad_m2o, ciudad_txt, mapas, venta)
    bloque8_preview(datos, mapas, ciudad_m2o)

    print()
    print("=" * 115)
    print("COMO LEER ESTE INFORME")
    print("=" * 115)
    print("· El juez de la mejora es el % de VENTA sin ciudad (bloque 5b), no el conteo bruto sobre")
    print("  el catalogo: de los ~209k terceros, la mayoria no factura.")
    print("· `ciudad` es hoy PURAMENTE DESCRIPTIVA: no entra en ningun join del DW.")
    print("  map_zona_cundinamarca (depto+ciudad->zona) esta cargada pero nadie la consulta.")
    print("· `departamento` conserva a proposito su sufijo ' (CO)': lo exige el LEFT JOIN de")
    print("  map_zona en sql/marts/24_rol_intranet.sql:62.")
    print("· El escalon A del bloque 7 son REESCRITURAS. Revisar esos pares antes de correr")
    print("  `python etl_dw_marts.py --backfill-terceros`.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-odoo", action="store_true",
                    help="solo los bloques de Postgres (no se conecta a Odoo)")
    ap.add_argument("--muestra", type=int, default=0,
                    help="limita el read de Odoo a los N primeros clientes con venta "
                         "(0 = todos; usar p.ej. 2000 para una aproximacion rapida)")
    a = ap.parse_args()
    main(usar_odoo=not a.sin_odoo, muestra=a.muestra)
