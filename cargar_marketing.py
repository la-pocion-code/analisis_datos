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

⚠⚠ ESTADO: SOLO LA TRM FUNCIONA HOY (2026-08-03)
Las otras cuatro fuentes necesitan credenciales que **no existen todavia en este
repo**: no hay ni un rastro de SUPERMETRICS, GA4, SEARCH_CONSOLE ni SHOPIFY en el
`.env`. Sus funciones estan escritas y aisladas, pero **no se han podido probar
contra las APIs reales**. Cada una comprueba su credencial y, si falta, avisa y
devuelve vacio: la corrida no se cae y las demas siguen. Lo que hace falta para
encenderlas esta en el contrato, `marketing-contrato.md` §0 Fase A.

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


# ── Las cuatro fuentes que esperan credenciales ───────────────────────────────

def gasto_publicidad(cuentas: pd.DataFrame, desde: date, hasta: date) -> pd.DataFrame:
    """
    Gasto, compras y ROAS auto-reportado de Meta, Google Ads y TikTok, via la
    Query API de Supermetrics.

    ⚠ SIN PROBAR: requiere `SUPERMETRICS_API_KEY`, que no existe todavia.

    El patron de la API es asincrono y esta documentado en el artefacto de Cowork
    (lineas 1144-1199): se envia la consulta, devuelve un `schedule_id`, y se
    sondea `get_async_query_results` hasta que el estado sea `completed`.

    ⚠ `filtro` y `ajustes` de `bi_marketing_cuenta` se pasan TAL CUAL. Sin ellos
    las cifras no cuadran con las que marketing usa hoy: Colombia limita Google a
    Search y Performance Max, y Ecuador y Rep. Dominicana limitan Meta a las
    campanas de conversion.

    ⚠ El gasto se guarda en `moneda_nativa`, sin convertir (ver la cabecera).
    """
    faltan = _falta("SUPERMETRICS_API_KEY")
    if faltan:
        logging.warning(
            "  [aviso] gasto publicitario: falta %s. Se omiten las %d cuentas; "
            "el resto de la carga sigue.", ", ".join(faltan), len(cuentas))
        return pd.DataFrame()

    # TODO(credenciales): implementar contra la Query API de Supermetrics.
    # Campos por plataforma, tal como los pide el artefacto (linea 1138):
    #   meta:   cost,Date,offsite_conversions_fb_pixel_purchase,
    #           offsite_conversion_value_fb_pixel_purchase,website_purchase_roas
    #   google: cost,date,Conversions,ConversionValue,ROAS
    #   tiktok: cost,date,complete_payment,total_complete_payment_rate,
    #           complete_payment_roas
    # Salida esperada, una fila por (fecha, pais, plataforma):
    #   gasto_nativo, moneda_nativa, compras_auto, valor_compras_auto, roas_auto
    logging.warning("  [aviso] gasto publicitario: conector sin implementar "
                    "(pendiente de credenciales).")
    return pd.DataFrame()


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
           seco: bool = False) -> list:
    """Carga todo lo que se pueda. Devuelve el resumen para imprimir."""
    d0, d1 = _ventana(desde)
    logging.info("Ventana: %s -> %s (el dia en curso NO se carga)", d0, d1)

    loader = DBLoader()
    resumen = []

    # 1) TRM. Va PRIMERO porque la MV del gasto la necesita para convertir: sin
    #    tasa del dia, el gasto convertido saldria NULL.
    try:
        _escribir(loader, trm(d0, d1), "bi_trm_dia",
                  pk=["fecha", "moneda_origen", "moneda_destino"], resumen=resumen,
                  seco=seco)
    except Exception as exc:                                # noqa: BLE001
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
                    help="Solo la tasa de cambio (la unica fuente sin credenciales).")
    ap.add_argument("--seco", action="store_true", help="No escribe, solo informa.")
    args = ap.parse_args()

    resumen = cargar(desde=args.desde, solo_trm=args.solo_trm, seco=args.seco)

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
