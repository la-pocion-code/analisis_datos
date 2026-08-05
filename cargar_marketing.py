"""
Carga los datos de la hoja de MARKETING de la intranet.

    python cargar_marketing.py                      # ventana movil de 7 dias
    python cargar_marketing.py --desde 2026-01-01   # backfill
    python cargar_marketing.py --solo-trm           # solo la tasa de cambio
    python cargar_marketing.py --seco               # no escribe, solo informa

Escribe en las tablas de aterrizaje de `sql/marts/31_marketing_dashboards.sql`
(`bi_trm_dia`, `bi_marketing_gasto_dia`, `bi_marketing_web_dia`,
`bi_marketing_atribucion_dia`). Las MV que lee la intranet se refrescan aparte,
con `refrescar_mv_dashboards.py`.

⚠⚠ ESTADO (2026-08-05): FUNCIONAN LA TRM Y EL GASTO PUBLICITARIO
`gasto_publicidad` esta **implementado y probado contra la API real**: carga Meta,
Google Ads y TikTok por la Query API de Supermetrics. Medido ese dia, 1.296 filas
desde 2026-01-01.

Las otras TRES fuentes —Shopify, GA4 y Search Console— **siguen siendo esqueletos
sin implementar**: tienen su firma, comprueban su credencial y devuelven vacio,
pero **no hay codigo que llame a esas APIs**. No confundir «escrito» con
«implementado»: esa frase en el contrato es la que hizo creer que bastaba con
poner la credencial. Lo que falta esta en `marketing-contrato.md` §0 Fase A.

Consecuencia directa mientras sigan asi: la hoja muestra INVERSION pero no venta,
y el ROAS del Resumen sale `null` **con su razon** (nunca 0, que seria mentira).

⚠ Dos huecos que NO son de codigo y hay que resolver fuera (medidos el 2026-08-05):
  · **RD/Meta responde HTTP 500**: esa cuenta no esta como «prioritised account»
    en la suscripcion de Supermetrics. Se arregla en hub.supermetrics.com, no aqui.
  · **EC/Meta carga 0 filas**, y es correcto: su `filtro` limita a campanas
    `OUTCOME_SALES` y en 2026 Ecuador solo ha corrido `OUTCOME_AWARENESS`. Hay
    gasto real que el filtro excluye a proposito — el artefacto hace lo mismo.

⚠ EL DIA EN CURSO NO SE CARGA. Las cuatro fuentes lo entregan incompleto y un dia
a medias hunde el promedio. Es tambien lo que hacia el artefacto de Cowork, y la
intranet cuenta con ello: su calculo del ritmo divide entre DIAS CON DATO.

⚠ VENTANA MOVIL DE 7 DIAS. Las plataformas de anuncios corrigen el gasto de dias
ya cerrados; el UPSERT por clave compuesta lo absorbe sin duplicar.

⚠ AQUI NO SE CONVIERTE MONEDA. El gasto se guarda en la moneda de la CUENTA y la
conversion la hace `mv_marketing_gasto_dia` con la TRM vigente de cada dia. El
artefacto tenia la tasa cableada a 4.000 en una casilla de texto; la real del
2026-08-03 es 3.144,14, o sea que **subestimaba la inversion de Ecuador un 21 % e
inflaba su ROAS un 27 %**. Guardando la moneda nativa, corregir la TRM corrige el
historico entero en el siguiente refresco.
"""
import argparse
import logging
import os
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, ".")
from classes.db_loader import DBLoader
from etl_dw_marts import upsert

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

SCHEMA = "marts"
DIAS_VENTANA = 7

# Serie oficial de la TRM (Banco de la Republica via datos.gov.co). Publica
# VIGENCIAS, no dias: la tasa del viernes rige hasta el domingo.
URL_TRM = "https://www.datos.gov.co/resource/32sa-8pi3.csv"


# ── Utilidades ────────────────────────────────────────────────────────────────

def _ventana(desde: str | None) -> tuple[date, date]:
    """
    Rango a cargar. Termina AYER: el dia en curso llega incompleto de las cuatro
    fuentes y contamina el promedio.
    """
    hasta = date.today() - timedelta(days=1)
    if desde:
        return date.fromisoformat(desde), hasta
    return hasta - timedelta(days=DIAS_VENTANA - 1), hasta


def _config(loader) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Los paises activos y sus cuentas de publicidad, desde la propia base."""
    paises = loader.consultar(
        "SELECT pais, nombre, moneda_reporte, timezone, shopify_shop, "
        "       ga4_property_id, gsc_site_url "
        "FROM marts.bi_marketing_pais WHERE activo ORDER BY orden")
    cuentas = loader.consultar(
        "SELECT pais, plataforma, cuenta_id, moneda_nativa, ds_id, filtro, ajustes "
        "FROM marts.bi_marketing_cuenta WHERE activo ORDER BY pais, plataforma")
    return (paises if paises is not None else pd.DataFrame(),
            cuentas if cuentas is not None else pd.DataFrame())


def _falta(*variables) -> list:
    """Cuales de estas variables de entorno no estan puestas."""
    return [v for v in variables if not os.getenv(v)]


# ── TRM — la unica fuente que funciona hoy ────────────────────────────────────

def trm(desde: date, hasta: date) -> pd.DataFrame:
    """
    La TRM diaria, con las vigencias ya expandidas a un registro por dia.

    ⚠ La expansion no es cosmetica: la serie publica «del viernes al domingo» en
    una sola fila, y la MV hace un join por fecha. Sin expandir, el gasto del
    sabado se quedaria sin tasa.

    Se pide con margen hacia atras porque la vigencia que cubre `desde` puede
    haber empezado dias antes.
    """
    import requests

    margen = (desde - timedelta(days=10)).isoformat()
    url = (f"{URL_TRM}?$limit=50000&$where=vigenciahasta%20%3E=%20'{margen}'"
           f"&$order=vigenciadesde")
    r = requests.get(url, timeout=60)
    r.raise_for_status()

    import io
    df = pd.read_csv(io.StringIO(r.text))
    if df.empty:
        return pd.DataFrame()

    df["vigenciadesde"] = pd.to_datetime(df["vigenciadesde"]).dt.date
    df["vigenciahasta"] = pd.to_datetime(df["vigenciahasta"]).dt.date

    filas = []
    for _, v in df.iterrows():
        d = max(v["vigenciadesde"], desde - timedelta(days=10))
        fin = min(v["vigenciahasta"], hasta)
        while d <= fin:
            filas.append({
                "fecha": d,
                # ⚠ Sentido: cuantos COP vale UN USD. La MV divide o multiplica
                # segun de que moneda a cual convierta.
                "moneda_origen": "USD",
                "moneda_destino": "COP",
                "tasa": float(v["valor"]),
                "fuente": "datos.gov.co/32sa-8pi3",
            })
            d += timedelta(days=1)
    return pd.DataFrame(filas)


# ── Gasto publicitario, via la Query API de Supermetrics ──────────────────────

#: La API de Supermetrics. ⚠ Es la de plan **Enterprise**: con una clave de otro
#: nivel responde 401/403, y eso se propaga como error en vez de como «no hubo
#: gasto» (ver `ErrorSupermetrics`).
SM_BASE = "https://api.supermetrics.com/enterprise/v2/query"
SM_ESPERA_S = 5          # entre sondeos
SM_MAX_SONDEOS = 60      # 5 min por cuenta; una consulta de 7 dias tarda segundos

#: Los campos que pide cada plataforma, EN ORDEN: fecha, gasto, compras, valor,
#: roas. Salen del artefacto de Cowork, que es lo que produce las cifras que
#: marketing usa hoy.
#:
#: ⚠⚠ LA RESPUESTA NO VIENE CON ESTAS CLAVES, VIENE CON ETIQUETAS HUMANAS. Se pide
#: `cost` y vuelve `Cost`; se pide `date` y vuelve `Date`; se pide
#: `ConversionValue` y vuelve `Total conversion value`. Por eso el mapeo es
#: **POSICIONAL** y no por nombre — ver `_filas_a_gasto`. Mapear por nombre es lo
#: que tuvo esta carga en cero: con `date` en minuscula ninguna fila de Google ni
#: de TikTok casaba y se descartaban todas, y en Meta la fecha coincidia de
#: casualidad pero `cost` no, asi que el gasto entraba en NULL. Medido el
#: 2026-08-05.
#:
#: Etiquetas que devolvio la API ese dia, por si hay que depurar:
#:   FA  -> Date · Cost · Website purchases · Website purchases conversion value ·
#:          Website purchase ROAS (return on advertising spend)
#:   AW  -> Date · Cost · Conversions · Total conversion value ·
#:          Return on ad spend (ROAS)
#:   TIK -> Date · Cost · Complete payment events · Total complete payment value ·
#:          Complete payment ROAS
#:
#: ⚠ `total_complete_payment_rate` de TikTok **no es una tasa** pese al nombre:
#: devuelve «Total complete payment value», el importe. Comprobado, no suponer.
CAMPOS_GASTO = {
    "FA": ["Date", "cost", "offsite_conversions_fb_pixel_purchase",
           "offsite_conversion_value_fb_pixel_purchase", "website_purchase_roas"],
    "AW": ["date", "cost", "Conversions", "ConversionValue", "ROAS"],
    "TIK": ["date", "cost", "complete_payment", "total_complete_payment_rate",
            "complete_payment_roas"],
}


class ErrorSupermetrics(RuntimeError):
    """
    Fallo de la API, distinto de «esa cuenta no gasto nada esos dias».

    ⚠⚠ Existe porque confundir las dos cosas es justo lo que dejo esta carga
    muerta y en verde: el esqueleto devolvia un DataFrame vacio ante cualquier
    problema, asi que un 401 por plan equivocado se veia igual que un dia sin
    inversion. Un vacio legitimo se informa; un error se levanta.
    """


def _sm_pedir(sesion, ruta: str, params: dict) -> dict:
    """Una llamada a la API. Traduce cualquier problema a `ErrorSupermetrics`."""
    r = sesion.get(f"{SM_BASE}/{ruta}", params=params, timeout=120)
    if r.status_code >= 400:
        # El cuerpo lleva el motivo real (clave invalida, cuenta sin permiso,
        # campo inexistente). Sin el, depurar esto a ciegas es imposible.
        raise ErrorSupermetrics(
            f"{ruta}: HTTP {r.status_code} - {r.text[:400]}")
    try:
        return r.json()
    except ValueError as exc:
        raise ErrorSupermetrics(f"{ruta}: respuesta no es JSON - {r.text[:200]}") from exc


def _sm_filas(payload: dict) -> list[dict]:
    """
    Las filas de una respuesta, venga como lista de listas o de diccionarios.

    Supermetrics devuelve `data` como matriz con la PRIMERA FILA de encabezados.
    Se admiten las dos formas porque el formato depende de ajustes de la cuenta y
    equivocarse aqui daria cero filas sin ningun error.
    """
    datos = (payload or {}).get("data")
    if not datos:
        return []
    if isinstance(datos, dict):                       # {"data": {"rows": [...]}}
        datos = datos.get("rows") or []
    if not datos:
        return []
    if isinstance(datos[0], dict):
        return datos
    cabecera = [str(c) for c in datos[0]]
    return [dict(zip(cabecera, fila)) for fila in datos[1:]]


def _sm_consultar(sesion, api_key: str, params: dict) -> list[dict]:
    """
    Envia una consulta y devuelve sus filas, sondeando si va en asincrono.

    Se pide en **sincrono** primero (`sync_timeout` alto): una ventana de 7 dias
    de una cuenta tarda segundos, y el ida y vuelta asincrono solo anade puntos
    de fallo. Si la API decide encolarla igualmente (202 + `schedule_id`), se
    sondea `status` hasta que termine.
    """
    import time

    payload = _sm_pedir(sesion, "data/json", {**params, "api_key": api_key})

    meta = payload.get("meta") or {}
    schedule_id = meta.get("schedule_id") or payload.get("schedule_id")
    estado = str(meta.get("status_code") or payload.get("status_code") or "").upper()

    # Ya vinieron los datos: nada que sondear.
    if not schedule_id or _sm_filas(payload):
        return _sm_filas(payload)

    for _ in range(SM_MAX_SONDEOS):
        if estado in ("SUCCESS", "COMPLETED", "DONE"):
            break
        if estado in ("FAILURE", "FAILED", "ERROR", "CANCELLED"):
            raise ErrorSupermetrics(f"la consulta termino en {estado}: "
                                    f"{str(payload)[:400]}")
        time.sleep(SM_ESPERA_S)
        payload = _sm_pedir(sesion, "status",
                            {"api_key": api_key, "schedule_id": schedule_id})
        meta = payload.get("meta") or {}
        estado = str(meta.get("status_code") or payload.get("status_code") or "").upper()
        if _sm_filas(payload):
            return _sm_filas(payload)
    else:
        raise ErrorSupermetrics(
            f"la consulta sigue en {estado or 'estado desconocido'} tras "
            f"{SM_MAX_SONDEOS * SM_ESPERA_S}s (schedule_id={schedule_id})")

    return _sm_filas(_sm_pedir(sesion, "results",
                               {"api_key": api_key, "schedule_id": schedule_id}))


def _num(valor):
    """Un numero, o None. Nunca 0 por defecto: un cero inventado es un dato falso."""
    if valor in (None, "", "-"):
        return None
    try:
        return float(str(valor).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _filas_a_gasto(crudas: list[dict], cuenta, etiqueta: str) -> list[dict]:
    """
    Traduce las filas de la API a las columnas de `bi_marketing_gasto_dia`.

    ⚠⚠ POR POSICION, no por nombre: la API responde con etiquetas humanas
    (`Cost`, `Total conversion value`…) y no con los codigos que se le piden.
    Se pide UNA dimension (la fecha) y CUATRO metricas, y Supermetrics devuelve
    las dimensiones primero y luego las metricas en el orden pedido, asi que la
    posicion es estable.

    ⚠ Y por eso hay guardian: si la respuesta no trae exactamente 5 columnas o la
    primera no es una fecha, se levanta en vez de rellenar con None. Un mapeo
    silenciosamente desalineado produciria gasto en la columna de compras — un
    numero creible y falso, que es peor que no cargar nada.
    """
    filas = []
    for r in crudas:
        valores = list(r.values())
        if len(valores) != 5:
            raise ErrorSupermetrics(
                f"{etiqueta}: se pidieron 5 campos y la respuesta trae "
                f"{len(valores)} ({list(r.keys())}). El mapeo posicional no es "
                f"fiable asi: revisa `CAMPOS_GASTO`.")
        try:
            fecha = pd.to_datetime(valores[0]).date()
        except (TypeError, ValueError) as exc:
            raise ErrorSupermetrics(
                f"{etiqueta}: la primera columna deberia ser la fecha y vino "
                f"{valores[0]!r} (claves: {list(r.keys())}).") from exc

        filas.append({
            "fecha": fecha,
            "pais": cuenta["pais"],
            "plataforma": cuenta["plataforma"],
            "gasto_nativo": _num(valores[1]),
            "moneda_nativa": cuenta["moneda_nativa"],
            "compras_auto": _num(valores[2]),
            "valor_compras_auto": _num(valores[3]),
            "roas_auto": _num(valores[4]),
        })
    return filas


def gasto_publicidad(cuentas: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Gasto, compras y ROAS auto-reportado de Meta, Google Ads y TikTok, via la
    Query API de Supermetrics.

    Una consulta por cuenta (8 hoy: 3 Meta, 3 Google, 2 TikTok). Se hacen por
    separado y no en una sola porque cada `ds_id` tiene su propio juego de campos
    y su propio filtro.

    ⚠ `filtro` y `ajustes` de `bi_marketing_cuenta` se pasan TAL CUAL. Sin ellos
    las cifras no cuadran con las que marketing usa hoy: Colombia limita Google a
    Search y Performance Max, y Ecuador y Rep. Dominicana limitan Meta a las
    campanas de conversion.

    ⚠⚠ El gasto se guarda en `moneda_nativa`, SIN convertir (ver la cabecera del
    modulo). La conversion la hace `mv_marketing_gasto_dia` con la TRM del dia, y
    es lo que evita repetir el error de la tasa cableada a 4.000.

    ⚠ Un fallo de UNA cuenta no tumba las demas: se registra y se sigue. Pero si
    fallan TODAS se levanta, porque eso no es «no hubo gasto», es que la API o la
    credencial no sirven — y devolver vacio ahi es como nacio este problema.
    """
    faltan = _falta("SUPERMETRICS_API_KEY")
    if faltan:
        logging.warning(
            "  [aviso] gasto publicitario: falta %s. Se omiten las %d cuentas; "
            "el resto de la carga sigue.", ", ".join(faltan), len(cuentas))
        return pd.DataFrame()
    if cuentas.empty:
        logging.warning("  [aviso] gasto publicitario: no hay cuentas activas en "
                        "marts.bi_marketing_cuenta.")
        return pd.DataFrame()

    import json

    import requests

    api_key = os.getenv("SUPERMETRICS_API_KEY")
    team_id = os.getenv("SUPERMETRICS_TEAM_ID")
    sesion = requests.Session()

    filas, errores = [], []
    for _, c in cuentas.iterrows():
        etiqueta = f"{c['pais']}/{c['plataforma']}"
        campos = CAMPOS_GASTO.get(c["ds_id"])
        if not campos:
            errores.append(f"{etiqueta}: ds_id '{c['ds_id']}' sin mapa de campos")
            continue

        params = {
            "ds_id": c["ds_id"],
            "ds_accounts": c["cuenta_id"],
            "start_date": desde.isoformat(),
            "end_date": hasta.isoformat(),
            # ⚠ EL ORDEN ES EL CONTRATO: la fecha primero y las cuatro metricas
            # despues, porque el mapeo de la respuesta es posicional.
            "fields": ",".join(campos),
            "max_rows": 100000,
            # Sincrono: una ventana de 7 dias tarda segundos.
            "sync_timeout": 300,
        }
        if team_id:
            params["team_id"] = team_id
        if c.get("filtro"):
            params["filter"] = c["filtro"]
        if c.get("ajustes"):
            # `ajustes` es JSON en la semilla (`asset_level`, `report_type`) y sus
            # claves son parametros de la API: se expanden, no se mandan como blob.
            try:
                extra = c["ajustes"] if isinstance(c["ajustes"], dict) \
                    else json.loads(c["ajustes"])
                params.update(extra)
            except (TypeError, ValueError):
                errores.append(f"{etiqueta}: `ajustes` no es JSON valido")
                continue

        try:
            nuevas = _filas_a_gasto(_sm_consultar(sesion, api_key, params),
                                    c, etiqueta)
        except ErrorSupermetrics as exc:
            logging.error("  [ERROR] %s: %s", etiqueta, exc)
            errores.append(f"{etiqueta}: {exc}")
            continue

        filas.extend(nuevas)
        # El conteo y el gasto por cuenta son lo unico que delata un `filtro` que
        # dejo una cuenta en cero sin que la API se queje — le paso justo a
        # EC/Meta, cuyo filtro de campanas de conversion excluye TODO su gasto.
        total = sum(f["gasto_nativo"] or 0 for f in nuevas)
        logging.info("  %-12s %4d filas  %15.2f %s", etiqueta, len(nuevas),
                     total, c["moneda_nativa"])
        if nuevas and not total:
            logging.warning("      [aviso] %s trae filas pero gasto 0. Si tiene "
                            "`filtro`, puede estar excluyendo todo.", etiqueta)

    if errores and not filas:
        raise ErrorSupermetrics(
            f"fallaron las {len(errores)} cuentas y no se trajo ni una fila. "
            f"Primer motivo -> {errores[0]}")
    if errores:
        logging.warning("  [aviso] %d de %d cuentas fallaron: %s",
                        len(errores), len(cuentas), "; ".join(errores[:3]))

    if not filas:
        logging.warning("  [aviso] gasto publicitario: la API respondio bien pero "
                        "no hay ni una fila en la ventana. Es un vacio legitimo.")
        return pd.DataFrame()

    df = pd.DataFrame(filas)
    # Una cuenta puede devolver el mismo dia partido en varias filas (moneda o
    # campana); la clave de la tabla es (fecha, pais, plataforma), asi que se
    # agrega aqui. Sin esto el UPSERT se quedaria con una fila arbitraria.
    df = (df.groupby(["fecha", "pais", "plataforma", "moneda_nativa"], as_index=False)
            .agg({"gasto_nativo": "sum", "compras_auto": "sum",
                  "valor_compras_auto": "sum", "roas_auto": "mean"}))
    return df


def web_shopify(paises: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Venta neta, impuestos y pedidos por dia y tienda, via la Admin API de Shopify.

    ⚠ SIN PROBAR: requiere `SHOPIFY_TOKEN_{CO,EC,RD}`.

    ⚠ Leer pedidos de mas de 60 dias atras exige que Shopify apruebe el scope
    `read_all_orders`; sin el, un backfill largo devuelve vacio SIN error.
    """
    faltan = _falta("SHOPIFY_TOKEN_CO", "SHOPIFY_TOKEN_EC", "SHOPIFY_TOKEN_RD")
    if faltan:
        logging.warning("  [aviso] Shopify: faltan %d credenciales (%s). "
                        "Sin venta ni pedidos.", len(faltan), ", ".join(faltan))
        return pd.DataFrame()
    logging.warning("  [aviso] Shopify: conector sin implementar.")
    return pd.DataFrame()


def web_ga4(paises: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Sesiones y usuarios por dia, via la Data API de GA4.

    ⚠ SIN PROBAR: requiere `GA4_CREDENTIALS_JSON` y `ga4_property_id` en
    `bi_marketing_pais` (hoy NULL en los tres paises).

    ⚠ Lo que no haya recogido GA4 antes de que se concediera el acceso al service
    account NO EXISTE y no se puede reconstruir. Esos dias tienen que quedar en
    NULL, jamas en 0: la hoja distingue «no hubo visitas» de «no tenemos el dato»,
    y el artefacto de Cowork mostraba «0 sesiones sobre una meta de 18.000» —
    semaforo rojo permanente sobre un dato inexistente.
    """
    faltan = _falta("GA4_CREDENTIALS_JSON")
    sin_propiedad = paises[paises["ga4_property_id"].isna()]["pais"].tolist() \
        if not paises.empty else []
    if faltan or len(sin_propiedad) == len(paises):
        logging.warning(
            "  [aviso] GA4: %s. Las sesiones quedaran en NULL (que NO es cero).",
            f"falta {', '.join(faltan)}" if faltan
            else f"sin ga4_property_id en {', '.join(sin_propiedad)}")
        return pd.DataFrame()
    logging.warning("  [aviso] GA4: conector sin implementar.")
    return pd.DataFrame()


def web_search_console(paises: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Impresiones, clics y posicion media, via la API de Search Console.

    ⚠ SIN PROBAR: requiere credenciales y `gsc_site_url` en `bi_marketing_pais`.
    ⚠ Search Console entrega con 2-3 dias de retraso: los dias mas recientes de
    la ventana pueden venir vacios, y eso es NULL, no cero.
    """
    faltan = _falta("GSC_CREDENTIALS_JSON", "GOOGLE_CREDENTIALS_PATH")
    sin_sitio = paises[paises["gsc_site_url"].isna()]["pais"].tolist() \
        if not paises.empty else []
    if len(faltan) == 2 or len(sin_sitio) == len(paises):
        logging.warning(
            "  [aviso] Search Console: %s. Impresiones y clics en NULL.",
            "sin credenciales" if len(faltan) == 2
            else f"sin gsc_site_url en {', '.join(sin_sitio)}")
        return pd.DataFrame()
    logging.warning("  [aviso] Search Console: conector sin implementar.")
    return pd.DataFrame()


def atribucion(paises: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Venta atribuida por canal (GA4, y Shopify `order_referrer_name` de respaldo).

    ⚠ SIN PROBAR.
    ⚠ Para los canales de pago, `canal` tiene que salir EXACTAMENTE como
    `bi_marketing_cuenta.plataforma` (`Meta`, `Google`, `TikTok`): la intranet
    cruza por igualdad de cadena, y un `facebook` aqui contra un `Meta` alli deja
    el ROAS last-click en null sin que nada lo delate.
    """
    if _falta("GA4_CREDENTIALS_JSON") and _falta("SHOPIFY_TOKEN_CO"):
        logging.warning("  [aviso] atribucion por canal: sin credenciales de GA4 "
                        "ni de Shopify. El ROAS last-click no se podra calcular.")
        return pd.DataFrame()
    logging.warning("  [aviso] atribucion: conector sin implementar.")
    return pd.DataFrame()


# ── Orquestacion ──────────────────────────────────────────────────────────────

def _escribir(loader, df, tabla, pk, resumen, seco):
    """Escribe una tabla de aterrizaje. Un fallo no aborta las demas."""
    if df is None or df.empty:
        resumen.append((tabla, 0, "sin datos"))
        return
    if seco:
        resumen.append((tabla, len(df), "(seco) no escrito"))
        return
    try:
        n = upsert(loader, df, tabla, pk=pk, schema=SCHEMA)
        resumen.append((tabla, n, "OK"))
    except Exception as exc:                                # noqa: BLE001
        logging.error("%s: %s", tabla, exc)
        resumen.append((tabla, 0, f"ERROR {exc}"))


def cargar(desde: str | None = None, solo_trm: bool = False,
           seco: bool = False, solo_gasto: bool = False) -> list:
    """Carga todo lo que se pueda. Devuelve el resumen para imprimir."""
    d0, d1 = _ventana(desde)
    logging.info("Ventana: %s -> %s (el dia en curso NO se carga)", d0, d1)

    loader = DBLoader()
    resumen = []

    # 1) TRM. Va PRIMERO porque la MV del gasto la necesita para convertir: sin
    #    tasa del dia, el gasto convertido saldria NULL.
    #    ⚠ Con `--solo-gasto` se salta, y por eso ese modo es para DEPURAR el
    #    conector, no para dejar el almacen al dia.
    if not solo_gasto:
        try:
            _escribir(loader, trm(d0, d1), "bi_trm_dia",
                      pk=["fecha", "moneda_origen", "moneda_destino"], resumen=resumen,
                      seco=seco)
        except Exception as exc:                            # noqa: BLE001
            logging.error("TRM: %s", exc)
            resumen.append(("bi_trm_dia", 0, f"ERROR {exc}"))

    if solo_trm:
        return resumen

    paises, cuentas = _config(loader)
    if paises.empty:
        logging.error("marts.bi_marketing_pais esta vacia: aplicar antes "
                      "sql/marts/31_marketing_dashboards.sql.")
        return resumen
    logging.info("Config: %d paises activos, %d cuentas de publicidad",
                 len(paises), len(cuentas))

    # 2) Gasto publicitario.
    try:
        _escribir(loader, gasto_publicidad(cuentas, d0, d1),
                  "bi_marketing_gasto_dia",
                  pk=["fecha", "pais", "plataforma"], resumen=resumen, seco=seco)
    except Exception as exc:                                # noqa: BLE001
        logging.error("gasto: %s", exc)
        resumen.append(("bi_marketing_gasto_dia", 0, f"ERROR {exc}"))

    if solo_gasto:
        return resumen

    # 3) Web: las tres fuentes se combinan en una fila por (fecha, pais). Van
    #    juntas a proposito — comparten clave, y escribirlas por separado con
    #    UPSERT haria que la segunda pisara con NULL lo que puso la primera.
    partes = []
    for nombre, fn in (("Shopify", web_shopify), ("GA4", web_ga4),
                       ("Search Console", web_search_console)):
        try:
            p = fn(paises, d0, d1)
            if p is not None and not p.empty:
                partes.append(p.set_index(["fecha", "pais"]))
        except Exception as exc:                            # noqa: BLE001
            logging.error("%s: %s", nombre, exc)
    web = pd.concat(partes, axis=1).reset_index() if partes else pd.DataFrame()
    _escribir(loader, web, "bi_marketing_web_dia",
              pk=["fecha", "pais"], resumen=resumen, seco=seco)

    # 4) Atribucion por canal.
    try:
        _escribir(loader, atribucion(paises, d0, d1),
                  "bi_marketing_atribucion_dia",
                  pk=["fecha", "pais", "canal", "fuente"], resumen=resumen, seco=seco)
    except Exception as exc:                                # noqa: BLE001
        logging.error("atribucion: %s", exc)
        resumen.append(("bi_marketing_atribucion_dia", 0, f"ERROR {exc}"))

    return resumen


def main():
    ap = argparse.ArgumentParser(description="Carga los datos de la hoja de Marketing.")
    ap.add_argument("--desde", help="Fecha inicial AAAA-MM-DD (backfill).")
    ap.add_argument("--solo-trm", action="store_true",
                    help="Solo la tasa de cambio (no necesita credenciales).")
    ap.add_argument("--solo-gasto", action="store_true",
                    help="Solo el gasto publicitario, para depurar el conector de "
                         "Supermetrics. NO carga la TRM: no deja el almacen al dia.")
    ap.add_argument("--seco", action="store_true", help="No escribe, solo informa.")
    args = ap.parse_args()

    resumen = cargar(desde=args.desde, solo_trm=args.solo_trm, seco=args.seco,
                     solo_gasto=args.solo_gasto)

    print("\n" + "=" * 70)
    print(f"RESUMEN - marketing{'  (SECO)' if args.seco else ''}")
    print("=" * 70)
    print(f"{'tabla':<32}{'filas':>10}   estado")
    for tabla, filas, estado in resumen:
        print(f"{tabla:<32}{filas:>10}   {estado}")

    vacias = [t for t, n, _ in resumen if n == 0 and t != "bi_trm_dia"]
    if vacias:
        print("\nLas fuentes sin datos esperan credenciales. Lo que hace falta esta "
              "en marketing-contrato.md, seccion 0 (Fase A).")
    if not args.seco:
        print("\nDespues hay que refrescar las MV:")
        print("  python refrescar_mv_dashboards.py --mv mv_marketing_gasto_dia "
              "--mv mv_marketing_web_dia --mv mv_marketing_atribucion_dia")


if __name__ == "__main__":
    main()
