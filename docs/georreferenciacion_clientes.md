# Georreferenciación de clientes — mapas de calor (2026-10-10)

Para pintar **mapas de calor de los clientes que compran** por país, departamento, ciudad y
comuna, y exponerlos en la intranet (hoja `clientes-geo` y tool `bi_clientes_geo` del conector).

## La respuesta corta: qué necesita geocodificar y qué no

| Nivel del mapa | ¿Geocodificar? | Llave | Estado |
|---|---|---|---|
| País | No | `pais_codigo` (ISO-2 de `res.country.code`) | ✅ fase 1 |
| Departamento | No | `codigo_dane_departamento` (2 primeros dígitos DIVIPOLA) + `departamento` | ✅ fase 1 |
| Municipio / ciudad | No | `codigo_dane_municipio` (`res.city.zipcode`, 5 dígitos) | ✅ fase 1 |
| Comuna / localidad / barrio | **Sí** | coordenada + polígonos oficiales | ⏳ fase 3, espera OK al coste |
| Mapa de puntos (densidad) | **Sí** | coordenada | ⏳ fase 3 |

Una dirección colombiana («CL 14 7 85 BRR CENTRO») **no dice la comuna**: hace falta la
coordenada y un punto-en-polígono contra las comunas oficiales de cada ciudad.

## Lo medido (fase 0) — `diagnosticar_direcciones_terceros.py`

Corrida del 2026-10-10 sobre los **130.028 clientes con venta** de `mv_ventas_mes`:

- **País:** 130.016 en Colombia (99,99 %). Ecuador, Perú, República Dominicana y EE. UU. tienen
  **un cliente cada uno** (los distribuidores de exportación); 8 sin país. ⇒ Un mapa «mundial» son
  cinco puntos: el análisis útil es Colombia por departamento/municipio.
- **Dirección:** `street` al 99,95 %; con al menos un dígito (geocodificable) **122.438 (94,16 %)**.
  7.590 no se pueden geocodificar y se quedan a nivel de ciudad.
- **Municipio:** `city_id` al 97,79 %. El código DIVIPOLA está en `res.city.zipcode`
  (1.133 de 1.185 con 5 dígitos); `codigo_municipio` lo trae **sin el cero inicial** ('5001') y no sirve.
- **Coordenada en Odoo:** `partner_latitude`/`partner_longitude` existen pero están en 0 **salvo 4**.
- **Ciudades:** Bogotá 23,1 %, Cali 12,4 %, Medellín 9,8 %; las diez con comunas suman ~70.650 clientes.
- **Clientes nuevos:** ~3.000-4.500 al mes (picos de 8.765 en dic-2025 y 13.491 en abr-2026).

## ⚠ Quién se lista con dirección (dato personal, Ley 1581)

Decisión de William (2026-10-10): **las personas naturales solo aparecen agregadas**; los negocios
con su dirección. La regla vive en `v_tercero_geo_base.es_listable` (un solo sitio):

> NIT (`tipo_documento` '31' o '50') **o** canal `MAYORISTA NV` / `DISTRIBUIDOR`.

- `is_company` **no sirve**: el 83 % de los MAYORISTA NV está en `False`.
- `person_type` solo **tampoco**: 829 clientes del canal CLIENTE (consumidores) están marcados
  '1' (jurídica) con **cédula**, una contradicción de captura.
- Lista **cerrada** (fail-closed): un canal nuevo de tenderos con cédula queda sin listar, no se filtra.

## Objetos (`sql/marts/39_tercero_geo.sql`)

| Objeto | Qué es | ¿Concedido a `intranet_ro`? |
|---|---|---|
| `dim_tercero` + 7 columnas | `direccion`, `direccion2`, `es_compania`, `tipo_persona`, `tipo_documento`, `codigo_dane_municipio`, `pais_codigo` | ⛔ no (como siempre) |
| `v_tercero_geo_base` | la regla `es_listable` | ⛔ no |
| `v_lk_tercero_geo` | una fila por tercero; nombre y calle **solo si es listable**; columnas de la fase 3 reservadas en NULL | ✅ (sección de PII del 24) |
| `mv_clientes_geo_mes` | mes × tercero × canal con la geografía; la intranet cuenta `DISTINCT tercero_id` y nunca publica filas | ✅ |

Refresco: `MVS_GEO` en el tick `:00`, **al final** de `MVS` porque deriva de `mv_ventas_mes`.

## Despliegue (el orden importa)

1. Aplicar `39_tercero_geo.sql` **antes** de desplegar el ETL: `upsert` escribe todas las columnas
   del DataFrame y el código nuevo fallaría contra la tabla vieja.
2. Re-ejecutar `24_rol_intranet.sql` (falla si el 39 no está: concede sobre sus objetos).
3. Push a `main`.
4. `python etl_dw_marts.py --backfill-terceros` (`--dims` no puebla un campo nuevo).
5. `python refrescar_mv_dashboards.py --mv mv_clientes_geo_mes`.

⚠ La receta de `GUIA_OPERACION.md §2.3` (`c=DBLoader().get_connection(); c.cursor()`) **ya no
funciona**: `get_connection()` es un context manager → `with DBLoader().get_connection() as c:`.

## Fase 3 — comuna y coordenada (NO construida; espera el OK al coste)

**Proveedor.** ⛔ **Google Geocoding no sirve para guardar**: sus términos solo permiten conservar
la latitud/longitud **30 días** y luego hay que borrarla (Service Specific Terms, Geocoding API).
Los que sí permiten almacenar: **Esri ArcGIS «stored geocoding» (~US$4/1.000)** y Mapbox
permanente (US$5/1.000, sin cupo gratis). El MCP de Google Maps (Grounding Lite) es para lugares,
clima y rutas, no para geocodificar en lote.

**Coste medido** (direcciones distintas = llamadas, una sola vez gracias a la caché):

| Alcance | Direcciones | Esri | Mapbox | Google (1 corrida, no almacenable) |
|---|---|---|---|---|
| Todos los clientes con calle | 115.801 | ~US$463 | ~US$579 | ~US$513 |
| Solo las 10 ciudades con comunas | 65.739 | ~US$263 | ~US$329 | ~US$279 |
| Solo negocios (`is_company`) | 10.648 | ~US$43 | ~US$53 | ~US$3 |
| Mantenimiento (clientes nuevos/mes) | ~4.000 | ~US$16/mes | ~US$20/mes | — |

Precios públicos consultados el 2026-10-10: **re-verificarlos al crear la cuenta**.

**Diseño previsto:** piloto de 500 direcciones para medir la tasa de acierto exacto/aproximado;
paso `geocodificar_terceros.py` en el tick completo (patrón del 2b de marketing) con `--seco` que
estima llamadas y coste, **tope mensual de llamadas** en `etl_control`, caché `bi_geo_direccion`
por hash de dirección normalizada, y comunas por punto-en-polígono (`shapely`, sin PostGIS) contra
datos abiertos oficiales. Rellena las columnas reservadas de `v_lk_tercero_geo` —**con el mismo
`CASE WHEN es_listable`**— y `comuna` de la MV.

**Limitación declarada:** se usa la dirección del **tercero facturado**. Una cadena grande pone toda
su venta en la ciudad de su sede; la dirección de entrega (`partner_shipping_id`) queda como mejora.
