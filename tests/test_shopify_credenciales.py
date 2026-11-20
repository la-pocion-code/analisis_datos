"""
Pruebas EN MEMORIA de las credenciales de Shopify (sin red, sin Postgres).

    python -m unittest tests.test_shopify_credenciales

Nacen del 2026-11-20: la guia pedia crear la app en el admin de cada tienda y copiar su
token fijo, pero desde el 1-ene-2026 Shopify ya no deja crear esas apps. Las del Dev
Dashboard dan client ID + secret y un token que caduca a las 24 h, asi que el loader lo
canjea en cada corrida. Lo que se fija aqui:
  - una tienda cuenta como configurada con el par O con el token fijo, nunca con medio par;
  - el canje va a `/admin/oauth/access_token` con `grant_type=client_credentials` y el
    token canjeado es el que viaja a la Admin API;
  - un canje fallido LEVANTA (nunca un vacio en verde) y su mensaje no lleva el secreto.
"""
import json
import os
import sys
import unittest
from datetime import date
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cargar_marketing as M  # noqa: E402

GID_CO = "gid://shopify/Shop/76335644950"
SECRETO = "shpss_secreto_que_no_debe_salir_nunca"


class Resp:
    def __init__(self, status, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload


class SesionFalsa:
    """`requests.Session` que entiende el canje del token y las dos consultas GraphQL."""

    def __init__(self, canje=None, gid=GID_CO, nodos=None):
        self.canje = canje or Resp(200, {"access_token": "shpat_canjeado",
                                         "scope": "read_orders,read_all_orders",
                                         "expires_in": 86399})
        self.gid = gid
        self.nodos = nodos or []
        self.posts = []

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "data": data, "headers": headers or {}})
        if url.endswith("/admin/oauth/access_token"):
            return self.canje
        if "shop {" in json["query"]:
            return Resp(200, {"data": {"shop": {"id": self.gid}}})
        return Resp(200, {"data": {"orders": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": self.nodos}}})

    def canjes(self):
        return [p for p in self.posts if p["url"].endswith("/admin/oauth/access_token")]

    def graphql(self):
        return [p for p in self.posts if p["url"].endswith("/graphql.json")]


def entorno(**variables):
    """El entorno real SIN ninguna SHOPIFY_*, mas las que pida el test."""
    limpio = {k: v for k, v in os.environ.items() if not k.startswith("SHOPIFY_")}
    limpio.update(variables)
    return mock.patch.dict(os.environ, limpio, clear=True)


def paises(*codigos):
    datos = {"CO": ("Colombia", "COP", "America/Bogota", GID_CO),
             "EC": ("Ecuador", "USD", "America/Guayaquil", "gid://shopify/Shop/63174410321")}
    return pd.DataFrame([{"pais": c, "nombre": datos[c][0], "moneda_reporte": datos[c][1],
                          "timezone": datos[c][2], "shopify_shop": datos[c][3],
                          "ga4_property_id": None, "gsc_site_url": None} for c in codigos])


class Base(unittest.TestCase):
    def setUp(self):
        M._SHOPIFY_TOKENS.clear()
        M._SHOPIFY_MEMO.clear()


class TestQueCuentaComoCredencial(Base):

    def _reparto(self, **variables):
        with entorno(**variables):
            con, sin = M._shopify_credenciales(paises("CO"))
        return [c[0]["pais"] for c in con], sin

    def test_el_par_del_dev_dashboard_cuenta(self):
        self.assertEqual(self._reparto(SHOPIFY_SHOP_CO="t.myshopify.com",
                                       SHOPIFY_CLIENT_ID_CO="id",
                                       SHOPIFY_CLIENT_SECRET_CO=SECRETO), (["CO"], []))

    def test_el_token_fijo_de_una_app_vieja_sigue_contando(self):
        self.assertEqual(self._reparto(SHOPIFY_SHOP_CO="t.myshopify.com",
                                       SHOPIFY_TOKEN_CO="shpat_viejo"), (["CO"], []))

    def test_medio_par_NO_cuenta(self):
        self.assertEqual(self._reparto(SHOPIFY_SHOP_CO="t.myshopify.com",
                                       SHOPIFY_CLIENT_ID_CO="id"), ([], ["CO"]))

    def test_sin_dominio_no_cuenta(self):
        self.assertEqual(self._reparto(SHOPIFY_CLIENT_ID_CO="id",
                                       SHOPIFY_CLIENT_SECRET_CO=SECRETO), ([], ["CO"]))

    def test_la_credencial_nunca_se_imprime(self):
        cred = M.CredencialShopify(token="shpat_x", client_id="id", client_secret=SECRETO)
        self.assertNotIn(SECRETO, repr(cred))
        self.assertNotIn("shpat_x", repr(cred))


class TestElCanje(Base):

    def test_con_token_fijo_no_se_canjea_nada(self):
        s = SesionFalsa()
        tok = M._shopify_token(s, "t.myshopify.com", M.CredencialShopify(token="shpat_viejo"), "CO")
        self.assertEqual(tok, "shpat_viejo")
        self.assertEqual(s.posts, [])

    def test_el_canje_va_al_endpoint_de_client_credentials(self):
        s = SesionFalsa()
        cred = M.CredencialShopify(client_id="id", client_secret=SECRETO)
        tok = M._shopify_token(s, "https://t.myshopify.com/", cred, "CO")
        self.assertEqual(tok, "shpat_canjeado")
        (p,) = s.canjes()
        self.assertEqual(p["url"], "https://t.myshopify.com/admin/oauth/access_token")
        self.assertEqual(p["data"], {"grant_type": "client_credentials",
                                     "client_id": "id", "client_secret": SECRETO})
        self.assertNotIn("scope", p["data"])   # Shopify lo ignora: no se manda
        self.assertEqual(p["headers"]["Content-Type"], "application/x-www-form-urlencoded")

    def test_si_estan_los_dos_manda_el_par(self):
        s = SesionFalsa()
        cred = M.CredencialShopify(token="shpat_viejo", client_id="id", client_secret=SECRETO)
        self.assertEqual(M._shopify_token(s, "t.myshopify.com", cred, "CO"), "shpat_canjeado")

    def test_dentro_de_la_corrida_se_canjea_una_sola_vez(self):
        s = SesionFalsa()
        cred = M.CredencialShopify(client_id="id", client_secret=SECRETO)
        M._shopify_token(s, "t.myshopify.com", cred, "CO")
        M._shopify_token(s, "t.myshopify.com", cred, "CO")
        self.assertEqual(len(s.canjes()), 1)

    def test_un_token_a_punto_de_caducar_se_renueva(self):
        s = SesionFalsa(canje=Resp(200, {"access_token": "corto", "scope": "read_orders",
                                         "expires_in": M.SHOPIFY_TOKEN_MARGEN_S - 1}))
        cred = M.CredencialShopify(client_id="id", client_secret=SECRETO)
        M._shopify_token(s, "t.myshopify.com", cred, "CO")
        M._shopify_token(s, "t.myshopify.com", cred, "CO")
        self.assertEqual(len(s.canjes()), 2)


class TestUnCanjeFallidoLevantaYNoFiltraElSecreto(Base):

    def _falla(self, canje):
        cred = M.CredencialShopify(client_id="id", client_secret=SECRETO)
        with self.assertRaises(M.ErrorShopify) as ctx:
            M._shopify_token(SesionFalsa(canje=canje), "t.myshopify.com", cred, "CO/Shopify")
        msg = str(ctx.exception)
        self.assertNotIn(SECRETO, msg)
        return msg

    def test_otra_organizacion_lo_dice_con_su_nombre(self):
        msg = self._falla(Resp(400, {"error": "shop_not_permitted"}))
        self.assertIn("shop_not_permitted", msg)
        self.assertIn("MISMA organizacion", msg)

    def test_un_eco_del_secreto_en_la_respuesta_se_tapa(self):
        msg = self._falla(Resp(401, None, text=f'{{"error":"invalid_client {SECRETO}"}}'))
        self.assertIn("***", msg)

    def test_un_secreto_partido_por_el_recorte_tampoco_sale(self):
        # El secreto empieza justo antes del corte de 300: tapar despues de recortar
        # dejaria su primera mitad en el mensaje.
        relleno = "x" * (300 - len(SECRETO) // 2)
        msg = self._falla(Resp(401, None, text=relleno + SECRETO))
        self.assertNotIn(SECRETO[: len(SECRETO) // 2], msg)

    def test_sin_access_token(self):
        self._falla(Resp(200, {"scope": "read_orders"}))

    def test_sin_read_orders_lo_dice_antes_de_pedir_pedidos(self):
        msg = self._falla(Resp(200, {"access_token": "t", "scope": "read_products"}))
        self.assertIn("read_orders", msg)


class TestDePuntaAPunta(Base):
    """`web_shopify` con el par del Dev Dashboard: el token canjeado es el que viaja."""

    NODOS = [{"createdAt": "2026-11-18T15:00:00Z", "test": False, "cancelledAt": None,
              "currentSubtotalPriceSet": {"shopMoney": {"amount": "100000.00",
                                                        "currencyCode": "COP"}},
              "currentTotalTaxSet": {"shopMoney": {"amount": "19000.00"}},
              "customerJourneySummary": None}]

    def _cargar(self, sesion, pais_df, **variables):
        with entorno(**variables), mock.patch("requests.Session", return_value=sesion):
            return M.web_shopify(pais_df, date(2026, 11, 18), date(2026, 11, 18))

    def test_carga_con_el_token_canjeado(self):
        s = SesionFalsa(nodos=self.NODOS)
        df = self._cargar(s, paises("CO"), SHOPIFY_SHOP_CO="t.myshopify.com",
                          SHOPIFY_CLIENT_ID_CO="id", SHOPIFY_CLIENT_SECRET_CO=SECRETO)
        self.assertEqual(len(s.canjes()), 1)
        self.assertTrue(s.graphql())
        for p in s.graphql():
            self.assertEqual(p["headers"]["X-Shopify-Access-Token"], "shpat_canjeado")
        self.assertEqual(df["venta_neta"].tolist(), [100000.0])
        self.assertEqual(df["pedidos"].tolist(), [1])

    def test_el_cotejo_del_GID_sigue_mandando_con_el_par(self):
        s = SesionFalsa(gid="gid://shopify/Shop/1", nodos=self.NODOS)
        with self.assertRaises(M.ErrorShopify):
            self._cargar(s, paises("CO"), SHOPIFY_SHOP_CO="t.myshopify.com",
                         SHOPIFY_CLIENT_ID_CO="id", SHOPIFY_CLIENT_SECRET_CO=SECRETO)

    def test_todas_o_ninguna_sigue_valiendo_con_el_par(self):
        s = SesionFalsa(nodos=self.NODOS)
        with self.assertRaises(M.ErrorShopify) as ctx:
            self._cargar(s, paises("CO", "EC"), SHOPIFY_SHOP_CO="t.myshopify.com",
                         SHOPIFY_CLIENT_ID_CO="id", SHOPIFY_CLIENT_SECRET_CO=SECRETO)
        self.assertIn("EC", str(ctx.exception))
        self.assertEqual(s.posts, [])   # aborta ANTES de llamar a nadie

    def test_un_canje_fallido_aborta_la_fuente_y_no_devuelve_vacio(self):
        s = SesionFalsa(canje=Resp(400, {"error": "shop_not_permitted"}), nodos=self.NODOS)
        with self.assertRaises(M.ErrorShopify):
            self._cargar(s, paises("CO"), SHOPIFY_SHOP_CO="t.myshopify.com",
                         SHOPIFY_CLIENT_ID_CO="id", SHOPIFY_CLIENT_SECRET_CO=SECRETO)
        self.assertEqual(s.graphql(), [])


if __name__ == "__main__":
    unittest.main()
