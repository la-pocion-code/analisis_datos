"""
Carga los datos de la hoja de MARKETING de la intranet.

    python cargar_marketing.py                       # ventana movil de 7 dias
    python cargar_marketing.py --desde 2026-01-01    # backfill
    python cargar_marketing.py --solo-trm            # solo la tasa de cambio
    python cargar_marketing.py --solo-gasto          # depurar Supermetrics
    python cargar_marketing.py --solo-shopify --seco # depurar Shopify sin escribir
    python cargar_marketing.py --seco                # no escribe, solo informa

Escribe en las tablas de aterrizaje de `sql/marts/31_marketing_dashboards.sql`
(`bi_trm_dia`, `bi_marketing_gasto_dia`, `bi_marketing_web_dia`,
`bi_marketing_atribucion_dia`). Las MV que lee la intranet se refrescan aparte,
con `refrescar_mv_dashboards.py`.

⚠⚠ ESTADO (2026-08-12): FUNCIONAN LA TRM, EL GASTO PUBLICITARIO Y SHOPIFY
`gasto_publicidad` esta **implementado y probado contra la API real** (2026-08-05):
Meta, Google Ads y TikTok por la Query API de Supermetrics. Medido ese dia, 1.296
filas desde 2026-01-01.

`web_shopify` y la mitad de Shopify de `atribucion` estan **implementados contra la
Admin API GraphQL, y SIN PROBAR contra la API real**: no habia tokens el dia que se
escribieron. ⚠⚠ No confundir «implementado» con «probado»: la distincion es
exactamente la que costo una sesion cuando «escrito» se leyo como «implementado».
Lo que falta para probarlo es un token por tienda — el runbook esta en
`marketing-contrato.md` §0 Fase A, paso A5.

GA4 y Search Console **siguen siendo esqueletos**: tienen su firma, comprueban su
credencial y devuelven vacio, pero **no hay codigo que llame a esas APIs**.

Consecuencia mientras GA4 siga asi: la hoja tendra inversion y venta, pero no
sesiones ni usuarios, y el embudo de trafico saldra `null` **con su razon**.

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


# ── Venta web, via la Admin API de Shopify ────────────────────────────────────

#: Version de la Admin API. ⚠ Va PINEADA a proposito: `latest` cambia solo cada
#: trimestre y un campo retirado rompe la carga sin que nadie haya tocado nada.
#: Shopify sostiene cada version 12 meses, asi que hay que subirla una vez al ano
#: — y la fecha limite de esta esta en el contrato, no aqui.
SHOPIFY_API_VERSION = "2026-07"

#: Tope de la conexion `orders`. 250 es el maximo que admite Shopify.
SHOPIFY_PAGINA = 250

#: Un pedido son pocos campos, pero un backfill de un ano son muchas paginas.
SHOPIFY_TIMEOUT = 120

#: Shopify limita por coste con un cubo que se rellena solo, asi que un
#: `THROTTLED` es lo NORMAL en un backfill y no un fallo: se espera y se repite.
SHOPIFY_MAX_ESPERAS = 8
SHOPIFY_ESPERA_S = 4


class ErrorShopify(RuntimeError):
    """
    Fallo de la Admin API, distinto de «esa tienda no vendio nada esos dias».

    La distincion es el motivo de que exista esta clase: devolver un DataFrame
    vacio ante un token invalido es como nacio el problema de esta hoja — un cron
    en verde, `ok=true` y cero filas.
    """


def _shopify_url(dominio: str) -> str:
    """El endpoint GraphQL de una tienda. Acepta el dominio con o sin esquema."""
    d = dominio.strip().replace("https://", "").replace("http://", "").strip("/")
    return f"https://{d}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"


def _shopify_pedir(sesion, dominio: str, token: str, consulta: str,
                   variables: dict, etiqueta: str) -> dict:
    """
    Una llamada GraphQL, con espera y reintento SOLO ante `THROTTLED`.

    ⚠⚠ GraphQL devuelve **200 con `errors` dentro**: un token sin el scope que
    hace falta no es un HTTP 403, es un 200 con el campo en null. Sin mirar
    `errors` la carga seguiria como si todo hubiera ido bien y escribiria ceros.
    """
    import time as _time

    for intento in range(SHOPIFY_MAX_ESPERAS):
        r = sesion.post(_shopify_url(dominio),
                        json={"query": consulta, "variables": variables},
                        headers={"X-Shopify-Access-Token": token,
                                 "Content-Type": "application/json"},
                        timeout=SHOPIFY_TIMEOUT)
        if r.status_code == 401:
            raise ErrorShopify(f"{etiqueta}: 401. El token no vale para {dominio}.")
        if r.status_code >= 400:
            raise ErrorShopify(
                f"{etiqueta}: HTTP {r.status_code} - {r.text[:400]}")

        payload = r.json()
        errores = payload.get("errors") or []
        if errores:
            codigos = {(e.get("extensions") or {}).get("code") for e in errores}
            if "THROTTLED" in codigos and intento < SHOPIFY_MAX_ESPERAS - 1:
                _time.sleep(SHOPIFY_ESPERA_S * (intento + 1))
                continue
            raise ErrorShopify(
                f"{etiqueta}: {'; '.join(str(e.get('message')) for e in errores)[:400]}")

        datos = payload.get("data")
        if datos is None:
            raise ErrorShopify(f"{etiqueta}: respuesta sin `data`: {r.text[:300]}")
        return datos

    raise ErrorShopify(f"{etiqueta}: sigue limitado tras {SHOPIFY_MAX_ESPERAS} esperas.")


#: Identidad de la tienda. Se pide ANTES de los pedidos para poder cotejar que el
#: token es de la tienda que dice el catalogo (ver `_shopify_cotejar`).
Q_SHOPIFY_TIENDA = """
query { shop { id myshopifyDomain currencyCode ianaTimezone } }
"""

#: Los pedidos de la ventana. `sortKey: CREATED_AT` para que el cursor sea estable.
#: ⚠ `customerJourneySummary` va en la MISMA consulta porque recorrer los pedidos
#: dos veces cuesta el doble de cuota; si el token no lo permite se repite sin el.
Q_SHOPIFY_PEDIDOS = """
query($cursor: String, $filtro: String!) {
  orders(first: %d, after: $cursor, query: $filtro, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      createdAt
      test
      cancelledAt
      currentSubtotalPriceSet { shopMoney { amount currencyCode } }
      currentTotalTaxSet      { shopMoney { amount } }
      %s
    }
  }
}
""".strip()

_BLOQUE_JOURNEY = """
      customerJourneySummary {
        lastVisit {
          sourceType
          source
          referrerUrl
          landingPage
          utmParameters { source medium campaign }
        }
      }
"""


def _shopify_cotejar(datos_tienda: dict, pais: dict, etiqueta: str) -> None:
    """
    Que el token sea de la tienda que el catalogo dice, no de otra.

    ⚠⚠ Es el guardian que hace imposible el fallo mas caro de esta integracion:
    seis tiendas y seis tokens copiados a mano, y un par cruzado atribuye la venta
    de un pais a otro **sin ningun error y con una cifra creible**. El catalogo ya
    guarda el GID verificado de cada tienda (`bi_marketing_pais.shopify_shop`), asi
    que la comprobacion es gratis: se compara con el `shop.id` que responde la API.
    """
    esperado = (pais.get("shopify_shop") or "").strip()
    real = ((datos_tienda.get("shop") or {}).get("id") or "").strip()
    if esperado and real and esperado != real:
        raise ErrorShopify(
            f"{etiqueta}: el token responde por {real} y el catalogo dice "
            f"{esperado}. Es un token pegado en el pais equivocado: se aborta "
            f"antes de atribuir su venta a {pais['pais']}.")
    if esperado and not real:
        raise ErrorShopify(
            f"{etiqueta}: la API no devolvio `shop.id`; sin eso no se puede "
            f"cotejar que el token sea de la tienda del catalogo.")


def _shopify_credenciales(paises: pd.DataFrame) -> tuple[list, list]:
    """
    Reparte los paises del catalogo en (los que tienen credencial, los que no).

    ⚠ No devuelve solo los configurados: quien llama TIENE que ver los que faltan,
    porque cargar 5 de 6 tiendas y presentarlo como el total es el fallo silencioso
    que esta integracion tiene que hacer imposible.
    """
    con, sin = [], []
    for _, p in paises.iterrows():
        dominio = os.getenv(f"SHOPIFY_SHOP_{p['pais']}")
        token = os.getenv(f"SHOPIFY_TOKEN_{p['pais']}")
        if dominio and token:
            con.append((p, dominio, token))
        else:
            sin.append(p["pais"])
    return con, sin


def _shopify_filtro(pais, desde: date, hasta: date) -> str:
    """
    El filtro de busqueda, en INSTANTES con el desplazamiento de la tienda.

    ⚠⚠ La ventana la da `_ventana()` en FECHAS, y la Admin API filtra por
    instantes. Sin convertir con la zona de la tienda, «ayer» en Railway (que corre
    en UTC) se come 5 horas de la madrugada de Bogota y las mete en el dia
    anterior: el total del mes cuadra y **los dias sueltos no**, que es el error
    mas dificil de ver de los tres.
    """
    from datetime import datetime, time as _time
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(pais["timezone"])
    ini = datetime.combine(desde, _time.min, tzinfo=tz).isoformat()
    fin = datetime.combine(hasta, _time.max, tzinfo=tz).isoformat()
    # `test:false` en el propio filtro: los pedidos de prueba no son venta y
    # descartarlos aqui ahorra cuota en vez de traerlos para tirarlos.
    return f"created_at:>='{ini}' AND created_at:<='{fin}' AND test:false"


def _shopify_pedidos(sesion, dominio, token, filtro, etiqueta) -> tuple[list, bool]:
    """
    Todos los pedidos de la ventana, paginando por cursor.

    Devuelve `(nodos, con_journey)`: si el token no puede leer el recorrido del
    cliente, se repite la consulta SIN ese bloque y se avisa — perder la
    atribucion es aceptable, perder la venta no.
    """
    consulta = Q_SHOPIFY_PEDIDOS % (SHOPIFY_PAGINA, _BLOQUE_JOURNEY)
    con_journey = True
    nodos, cursor = [], None

    while True:
        try:
            datos = _shopify_pedir(sesion, dominio, token, consulta,
                                   {"cursor": cursor, "filtro": filtro}, etiqueta)
        except ErrorShopify as exc:
            texto = str(exc).lower()
            reintentable = con_journey and not nodos and (
                "customerjourney" in texto or "access denied" in texto
                or "scope" in texto)
            if not reintentable:
                raise
            logging.warning("  [aviso] %s: el token no puede leer "
                            "`customerJourneySummary` (%s). Se carga la venta sin "
                            "atribucion por referrer.", etiqueta, exc)
            consulta = Q_SHOPIFY_PEDIDOS % (SHOPIFY_PAGINA, "")
            con_journey = False
            continue

        conexion = datos.get("orders") or {}
        nodos.extend(conexion.get("nodes") or [])
        info = conexion.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return nodos, con_journey
        cursor = info.get("endCursor")
        if not cursor:
            raise ErrorShopify(f"{etiqueta}: `hasNextPage` es true y no hay cursor.")


def _shopify_a_dias(nodos: list, pais, etiqueta: str) -> tuple[list, int]:
    """
    Agrega los pedidos a un registro por dia LOCAL de la tienda.

    ⚠ El dia es el local, no el UTC: es el unico que cuadra con lo que el
    comerciante ve en Shopify Analytics, que es contra lo que se valida esto.

    ⚠⚠ Los cancelados se cuentan y se RESTAN de los pedidos, pero su importe ya
    viene descontado en los campos `current*`. Se devuelve su numero para poder
    decirlo en el log: una tienda con muchas cancelaciones explica una caida que
    de otro modo parece un fallo de carga.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(pais["timezone"])
    por_dia, cancelados = {}, 0

    for n in nodos:
        creado = n.get("createdAt")
        if not creado:
            continue
        dia = pd.Timestamp(creado).tz_convert(tz).date()
        if n.get("cancelledAt"):
            cancelados += 1
            continue

        neta = _num((((n.get("currentSubtotalPriceSet") or {})
                      .get("shopMoney") or {}).get("amount")))
        impuesto = _num((((n.get("currentTotalTaxSet") or {})
                          .get("shopMoney") or {}).get("amount")))

        acc = por_dia.setdefault(dia, {"venta_neta": 0.0, "impuestos": 0.0,
                                       "pedidos": 0})
        acc["venta_neta"] += neta or 0.0
        acc["impuestos"] += impuesto or 0.0
        acc["pedidos"] += 1

    filas = [{"fecha": d, "pais": pais["pais"], **v} for d, v in sorted(por_dia.items())]
    return filas, cancelados


#: Los pedidos ya traidos en esta corrida, por ventana. `web_shopify` y
#: `atribucion` consumen LOS MISMOS pedidos: sin esto, una carga de 6 tiendas
#: recorreria la API dos veces y gastaria el doble de cuota para nada.
#: ⚠ Se limpia solo porque el proceso muere en cada corrida del cron; no es una
#: cache con vencimiento y no debe usarse como tal.
_SHOPIFY_MEMO: dict = {}


def _shopify_traer(paises: pd.DataFrame, desde: date, hasta: date) -> list:
    """
    Los pedidos de la ventana de TODAS las tiendas, con la politica de errores.

    Devuelve `[(pais, nodos, con_journey), ...]`, o `[]` si no hay nada
    configurado todavia (que es el estado de hoy y no es un fallo).

    ⚠⚠ **Que sobren tokens no pasa nada; que falte UNO, si.** Si el catalogo tiene
    seis paises activos y solo cinco tienen credencial, esto LEVANTA en vez de
    cargar cinco: la suma de cinco tiendas presentada como el total es un numero
    creible y falso. Para dar de alta un pais antes de tener su token, se siembra
    con `activo = FALSE` y se enciende despues.

    ⚠⚠ Y lo mismo si una tienda **falla** habiendo cargado las demas: se aborta la
    fuente entera. Aqui no vale el «un fallo de una cuenta no tumba las demas» del
    gasto publicitario, porque el gasto se escribe por `(fecha, pais, plataforma)`
    —una cuenta que falta deja su fila vacia y se ve— mientras la venta web se
    escribe por `(fecha, pais)` y **un pais que falta es indistinguible de un pais
    que no vendio**.
    """
    if paises.empty:
        logging.warning("  [aviso] Shopify: no hay paises activos en "
                        "marts.bi_marketing_pais.")
        return []

    clave = (desde, hasta, tuple(paises["pais"]))
    if clave in _SHOPIFY_MEMO:
        return _SHOPIFY_MEMO[clave]

    con, sin = _shopify_credenciales(paises)
    if not con:
        # Nada configurado todavia. La hoja ya sabe decir «sin dato», y
        # `check_marts §7m` lo delata aparte.
        logging.warning("  [aviso] Shopify: ninguna tienda tiene credenciales "
                        "(faltan SHOPIFY_SHOP_/SHOPIFY_TOKEN_ de %s). Sin venta ni "
                        "pedidos.", ", ".join(sin))
        _SHOPIFY_MEMO[clave] = []
        return []
    if sin:
        raise ErrorShopify(
            f"{len(con)} de {len(con) + len(sin)} tiendas tienen credencial y "
            f"faltan las de {', '.join(sin)}. Se aborta a proposito: cargar solo "
            f"las que hay dejaria un total incompleto con pinta de completo. Pon "
            f"sus SHOPIFY_SHOP_/SHOPIFY_TOKEN_, o marca esos paises "
            f"`activo = FALSE` en marts.bi_marketing_pais.")

    import requests

    sesion = requests.Session()
    traidas, errores = [], []

    for pais, dominio, token in con:
        etiqueta = f"{pais['pais']}/Shopify"
        try:
            _shopify_cotejar(
                _shopify_pedir(sesion, dominio, token, Q_SHOPIFY_TIENDA, {},
                               etiqueta),
                pais, etiqueta)
            nodos, con_journey = _shopify_pedidos(
                sesion, dominio, token, _shopify_filtro(pais, desde, hasta),
                etiqueta)
        except ErrorShopify as exc:
            logging.error("  [ERROR] %s: %s", etiqueta, exc)
            errores.append(f"{etiqueta}: {exc}")
            continue
        traidas.append((pais, nodos, con_journey))

    if errores:
        raise ErrorShopify(
            f"{len(errores)} de {len(con)} tiendas fallaron. Se aborta la fuente "
            f"entera para no escribir un total incompleto con pinta de completo: "
            f"{'; '.join(errores[:3])}")

    sin_journey = [p["pais"] for p, _, cj in traidas if not cj]
    if sin_journey:
        logging.warning("  [aviso] sin atribucion por referrer en: %s",
                        ", ".join(sin_journey))

    _SHOPIFY_MEMO[clave] = traidas
    return traidas


def web_shopify(paises: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Venta neta, impuestos y pedidos por dia y pais, via la Admin API de Shopify.

    Una tienda por pais, con su propio par `SHOPIFY_SHOP_{PAIS}` /
    `SHOPIFY_TOKEN_{PAIS}`: Shopify no emite un token que sirva para varias
    tiendas, ni siquiera dentro de la misma organizacion. La traida y su politica
    de errores viven en `_shopify_traer`, que comparte con `atribucion`.

    ⚠⚠ **`venta_neta` es una APROXIMACION a la «venta neta» de Shopify Analytics
    y hay que validarla tienda por tienda contra un dia CERRADO.** Se usa
    `currentSubtotalPriceSet` (lineas de pedido, ya con descuentos y devoluciones
    aplicadas, sin envio ni impuestos) y `currentTotalTaxSet`. Si un dia no cuadra,
    el orden en que se prueban las alternativas es: `subtotalPriceSet` (antes de
    devoluciones), y luego `currentTotalPriceSet` menos envio. **No se ajusta a
    ojo:** la definicion elegida se escribe en el contrato.

    ⚠ Mas de 60 dias de historico exige que Shopify apruebe `read_all_orders`. Sin
    ese scope un backfill largo devuelve MENOS pedidos **sin ningun error**, asi que
    el log dice siempre el rango real que trajo cada tienda: si el primer dia con
    pedidos es sospechosamente reciente, es esto y no una tienda sin ventas.
    """
    traidas = _shopify_traer(paises, desde, hasta)
    if not traidas:
        return pd.DataFrame()

    filas = []
    for pais, nodos, _ in traidas:
        etiqueta = f"{pais['pais']}/Shopify"
        nuevas, cancelados = _shopify_a_dias(nodos, pais, etiqueta)
        filas.extend(nuevas)

        total = sum(f["venta_neta"] for f in nuevas)
        pedidos = sum(f["pedidos"] for f in nuevas)
        rango = f"{nuevas[0]['fecha']}..{nuevas[-1]['fecha']}" if nuevas else "-"
        logging.info("  %-12s %4d dias  %6d pedidos  %15.2f %s  [%s]",
                     etiqueta, len(nuevas), pedidos, total,
                     pais["moneda_reporte"], rango)
        if cancelados:
            logging.info("      %d pedidos cancelados, no contados", cancelados)
        if nuevas and nuevas[0]["fecha"] > desde:
            logging.warning("      [aviso] %s: se pidio desde %s y el primer dia con "
                            "pedidos es %s. Si el hueco es de ~60 dias, es que falta "
                            "el scope `read_all_orders`.",
                            etiqueta, desde, nuevas[0]["fecha"])

    if not filas:
        logging.warning("  [aviso] Shopify: las tiendas respondieron bien y no hay "
                        "ni un pedido en la ventana. Es un vacio legitimo.")
        return pd.DataFrame()

    df = pd.DataFrame(filas)
    df["pedidos"] = df["pedidos"].astype(int)
    return df[["fecha", "pais", "venta_neta", "impuestos", "pedidos"]]


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


#: De la fuente que reporta Shopify al `plataforma` de `bi_marketing_cuenta`.
#: ⚠⚠ Los nombres de la derecha son un CONTRATO con la intranet, que cruza por
#: igualdad de cadena: un `facebook` aqui contra un `Meta` alli deja el ROAS
#: last-click en null sin que nada lo delate.
SHOPIFY_CANAL = {
    "facebook": "Meta", "facebook_ads": "Meta", "fb": "Meta", "meta": "Meta",
    "meta_ads": "Meta", "instagram": "Meta", "ig": "Meta",
    "google": "Google", "google_ads": "Google", "googleads": "Google",
    "adwords": "Google",
    "tiktok": "TikTok", "tiktok_ads": "TikTok", "tiktokads": "TikTok",
}

#: Un `utm_medium` que significa «esto lo trajo un anuncio».
UTM_PAGO = ("cpc", "ppc", "paid", "paidsocial", "paid_social", "paid-social",
            "cpm", "display", "ads", "retargeting", "remarketing")

#: Parametros de clic que SOLO pone la plataforma al servir un anuncio, asi que
#: valen como prueba de pago aunque falte el UTM.
#: ⚠⚠ `fbclid` NO esta aqui a proposito: Facebook lo anade a **todos** los enlaces
#: salientes, tambien a los organicos, asi que tratarlo como prueba de pago
#: inflaria el ROAS de Meta con visitas que ningun anuncio pago.
CLICK_IDS_PAGO = {"gclid": "Google", "wbraid": "Google", "gbraid": "Google",
                  "ttclid": "TikTok"}


def _shopify_canal_pagado(visita: dict) -> str | None:
    """
    El canal de pago que trajo la visita, o `None` si no se puede afirmar que lo
    trajera un anuncio.

    ⚠⚠ **Devolver `None` en la duda no es pereza, es lo que evita inflar el ROAS.**
    La tentacion es mapear `source = 'google'` a `Google` y ya, pero ese `source`
    incluye la busqueda ORGANICA: atribuirla a Google Ads le regala venta que no
    pago, y el ROAS resultante es creible y falso. Solo cuenta lo que lleva un
    `utm_medium` de pago o un identificador de clic de la propia plataforma.
    """
    if not visita:
        return None

    urls = " ".join(str(visita.get(c) or "") for c in ("landingPage", "referrerUrl"))
    for parametro, canal in CLICK_IDS_PAGO.items():
        if f"{parametro}=" in urls:
            return canal

    utm = visita.get("utmParameters") or {}
    medio = (utm.get("medium") or "").strip().lower()
    if medio in UTM_PAGO:
        fuente = (utm.get("source") or visita.get("source") or "").strip().lower()
        return SHOPIFY_CANAL.get(fuente)
    return None


def _shopify_a_canales(nodos: list, pais, con_journey: bool) -> list[dict]:
    """Agrega los pedidos a `(fecha, pais, canal)` con `fuente='shopify_referrer'`."""
    if not con_journey:
        return []

    from zoneinfo import ZoneInfo

    tz = ZoneInfo(pais["timezone"])
    acumulado = {}

    for n in nodos:
        if n.get("cancelledAt") or not n.get("createdAt"):
            continue
        visita = ((n.get("customerJourneySummary") or {}).get("lastVisit")) or {}
        canal = _shopify_canal_pagado(visita)
        if not canal:
            continue

        dia = pd.Timestamp(n["createdAt"]).tz_convert(tz).date()
        neta = _num((((n.get("currentSubtotalPriceSet") or {})
                      .get("shopMoney") or {}).get("amount"))) or 0.0
        impuesto = _num((((n.get("currentTotalTaxSet") or {})
                          .get("shopMoney") or {}).get("amount"))) or 0.0

        acc = acumulado.setdefault((dia, canal),
                                   {"venta_atribuida": 0.0, "pedidos_atribuidos": 0})
        # Misma definicion que `venta` en mv_marketing_web_dia (neta + impuestos),
        # o el ROAS last-click y el ROAS del Resumen no serian comparables.
        acc["venta_atribuida"] += neta + impuesto
        acc["pedidos_atribuidos"] += 1

    return [{"fecha": d, "pais": pais["pais"], "canal": c,
             "fuente": "shopify_referrer", **v}
            for (d, c), v in sorted(acumulado.items())]


def atribucion(paises: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Venta atribuida por canal (Shopify `lastVisit`; GA4 cuando se implemente).

    Reutiliza los pedidos que ya trajo `web_shopify` en esta misma corrida
    (`_shopify_traer` memoiza), asi que no cuesta ni una llamada extra.

    ⚠ Solo cuenta la venta que se puede afirmar que trajo un ANUNCIO — ver
    `_shopify_canal_pagado`. Lo demas no aparece, que es lo correcto: no es venta
    atribuible a inversion.

    ⚠ GA4 sigue sin implementar, asi que hoy la unica `fuente` que se escribe es
    `shopify_referrer`. La columna existe justo para poder tener las dos y
    compararlas sin que una pise a la otra.
    """
    traidas = _shopify_traer(paises, desde, hasta)
    if not traidas:
        if _falta("GA4_CREDENTIALS_JSON"):
            logging.warning("  [aviso] atribucion por canal: sin credenciales de "
                            "GA4 ni de Shopify. El ROAS last-click no se podra "
                            "calcular.")
        return pd.DataFrame()

    filas = []
    for pais, nodos, con_journey in traidas:
        nuevas = _shopify_a_canales(nodos, pais, con_journey)
        filas.extend(nuevas)
        if nuevas:
            por_canal = {}
            for f in nuevas:
                por_canal[f["canal"]] = por_canal.get(f["canal"], 0) + f["pedidos_atribuidos"]
            logging.info("  %-12s %s", f"{pais['pais']}/referrer",
                         "  ".join(f"{c}:{n}" for c, n in sorted(por_canal.items())))

    if not filas:
        logging.warning("  [aviso] atribucion: ningun pedido de la ventana llega con "
                        "un `utm_medium` de pago ni un identificador de clic. Si hay "
                        "inversion en el mismo periodo, revisa que los anuncios "
                        "lleven UTM.")
        return pd.DataFrame()

    df = pd.DataFrame(filas)
    df["pedidos_atribuidos"] = df["pedidos_atribuidos"].astype(int)
    return df[["fecha", "pais", "canal", "fuente", "venta_atribuida",
               "pedidos_atribuidos"]]


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
           seco: bool = False, solo_gasto: bool = False,
           solo_shopify: bool = False) -> list:
    """Carga todo lo que se pueda. Devuelve el resumen para imprimir."""
    d0, d1 = _ventana(desde)
    logging.info("Ventana: %s -> %s (el dia en curso NO se carga)", d0, d1)

    loader = DBLoader()
    resumen = []

    # 1) TRM. Va PRIMERO porque la MV del gasto la necesita para convertir: sin
    #    tasa del dia, el gasto convertido saldria NULL.
    #    ⚠ Con `--solo-gasto` se salta, y por eso ese modo es para DEPURAR el
    #    conector, no para dejar el almacen al dia.
    if not (solo_gasto or solo_shopify):
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
    if not solo_shopify:
        try:
            _escribir(loader, gasto_publicidad(cuentas, d0, d1),
                      "bi_marketing_gasto_dia",
                      pk=["fecha", "pais", "plataforma"], resumen=resumen, seco=seco)
        except Exception as exc:                            # noqa: BLE001
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
    ap.add_argument("--solo-shopify", action="store_true",
                    help="Solo la venta web y la atribucion, para depurar el "
                         "conector de Shopify. NO carga la TRM ni el gasto: no deja "
                         "el almacen al dia.")
    ap.add_argument("--seco", action="store_true", help="No escribe, solo informa.")
    args = ap.parse_args()

    resumen = cargar(desde=args.desde, solo_trm=args.solo_trm, seco=args.seco,
                     solo_gasto=args.solo_gasto, solo_shopify=args.solo_shopify)

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
