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

## Fase 3 — EN MARCHA con Geoapify (gratis), actualizado 2026-10-13

**Decisión:** Geoapify, plan gratis (3.000 créditos/día; lotes a 0,5 por dirección). Su dato es
OpenStreetMap y lo dice por escrito en su página de la Geocoding API: *«Our services are based on
Open Data. So we do not restrict you from storing results»* — hay que conservar la atribución
(«© OpenStreetMap contributors») junto al dato. William pidió acumular gratis; con Google eso no es
legal ni en un CSV local (ver abajo).

**Construido y APLICADO** (`39_tercero_geo.sql`): `bi_geo_direccion` (caché por dirección) y
`bi_geo_tercero` (tercero → llave + barrio escrito), sin conceder a `intranet_ro`; la base, la vista
y la MV ya los leen (vacíos = mismo resultado que antes). La MV trae `comuna`, `barrio`,
`fuente_barrio` y la celda de ~1 km (`celda_lat`/`celda_lng`).

**`geocodificar_terceros.py`** — `--seco`, `--piloto N`, `--sin-llamadas`, `--informe`; tope diario
en `etl_control`; rechaza lo que cae fuera del municipio. **En seco** (2026-10-10), segmento
recompradores Shopify 2026: **7.217 clientes, 4.847 con placa reconocible → 4.657 llamadas**
(~2 días de cupo) y 459 con barrio escrito. ⚠ Unos 2.000 escriben la dirección fuera de la
cuadrícula («Mz 3 casa 5», veredas): no se mandan, porque devolverían el centro del municipio
con apariencia de precisión; se quedan a nivel de municipio.

### El piloto (2026-10-13, 300 direcciones de recompradores, gratis)

| | |
|---|---|
| Coincidencia | **78 % por CALLE** (`match_by_street`, la placa «# 28-66» casi nunca está en OSM), 15 % por edificio |
| Incertidumbre (mitad de la diagonal del `bbox`) | 38 % ≤ 100 m · 25 % 101-250 m · 12 % 251-500 m · 10 % > 500 m |
| Ubicadas con la regla final | **82,3 %** (63 % con confianza ≥ 0,5; +58 calles a 0,41 y ≤ 250 m, decisión de William) |
| Barrio de OSM | 13,8 % de las ubicadas, **vacío** en Bogotá, Medellín y Cali → **no sirve** |
| «Comuna» de OSM | nombres sueltos («El Hormiguero», «Zipaquirá ciudad») → **no se usa** |
| Velocidad | ~4,5 s por consulta en serie desde la oficina (cortes de red) → **4 hilos en paralelo** |

**Regla de estado** (`clasificar` y su espejo SQL `RECLASIFICAR`, `--reclasificar` sin llamadas):
`impreciso` si el resultado es solo barrio/ciudad o la incertidumbre pasa de 500 m; `ok` si la
confianza es ≥ 0,5, o si es una CALLE con confianza ≥ 0,4 a ≤ 250 m; si no, `baja_confianza`.

**Día 1** (2026-10-13): 2.500 consultas en 38,5 min, 78 % ubicadas.

### El cron diario — servicio «cron geocodificar» (keen-wonder)

- `python run_dw.py` con **`TAREA=geocodificar`**, `0 6 * * *` (01:00 Colombia), restart `NEVER`.
  Variables como **referencias** a las del servicio `analisis_datos` (`${{analisis_datos.DB_*}}`,
  `GEOAPIFY_API_KEY`): nadie copia un secreto.
- ⚠⚠ **Por qué `TAREA` y no un fichero de configuración propio**: Railway ya no deja asignar
  `railwayConfigFile` (Config as Code deprecado) y el servicio, del mismo repo, podría heredar
  `railway.toml` (ETL cada 15 min). `run_dw.py` despacha por `TAREA` **antes** del advisory lock,
  así que ese servicio nunca corre el ETL. Medido: a las 15:45 y 16:00 UTC no corrió → manda el
  `0 6 * * *` del servicio.
- `--segmento auto`: asigna gratis a TODOS (barrio escrito incluido) y gasta el cupo por
  prioridad — recompradores → Shopify del año → el resto. 70.716 direcciones distintas ≈ 3,5
  semanas de cupo; después solo los clientes nuevos (~180/día medidos).
- Al final de cada corrida asigna los polígonos oficiales (`poligonos_geo.asignar`).
- Coste estimado: ~US$0,02/mes (casi todo el tiempo espera la red).

### Barrio y comuna OFICIALES — `poligonos_geo.py` (31 fuentes, verificadas 2026-10-13)

Punto-en-polígono (`shapely`, WGS84). Una coordenada de CALLE cae sobre el eje de la vía, entre
barrios: primero «dentro» y, si no, el polígono más cercano a < 60 m (`fuente_poligono = cercano`).

| Ciudad | Barrios | Comunas / localidades | Licencia |
|---|---|---|---|
| Bogotá | UAECD, sector catastral (1.230) | SDP, localidades (20) | CC BY 4.0 |
| Medellín | Catastro, barrio/vereda (349) | Catastro (21) | no declarada |
| Cali | IDESC/DAPM (342) | IDESC/DAPM (22) | CC BY |
| Barranquilla · Bello | datos.gov.co (189 · 132) | DANE 2018 | CC BY-SA 4.0 |
| Bucaramanga, Floridablanca, Girón, Piedecuesta | AMB (247, 209, 101, 84) | DANE (Bucaramanga) | no declarada |
| Pereira · Manizales · Itagüí | Planeación / Alcaldía (486 · 116 · 85) | propias (21 · 12 · 6) | no declarada |
| Soacha | IDE Cundinamarca (696) | propias (6) | CC BY 4.0 |
| Cartagena, Ibagué, Cúcuta, Neiva, Palmira, Villavicencio, Santa Marta, Envigado | — | DANE 2018 | términos DANE |

⛔ Fuera, a propósito: barrios de Cartagena y Santa Marta (no oficiales o sin licencia) y el
«barrio legalizado» de Bogotá (cobertura parcial, sin editar desde 2019). ⚠ Manizales cuelga de
`/wadmzl/rest/services/20_WEB/` e Itagüí de `/waserver/rest/services/`: con `/arcgis/` dan 404.
⚠ El AMB rechaza `resultOffset` (400): `paginar=False`.

**Resultado del día 1:** 1.507 direcciones con barrio/comuna oficial en 21 ciudades (Bogotá
504/504, Medellín 217/218, Cali 285/325, Itagüí 30/30, Manizales 20/20). En la intranet, clientes
2026 con barrio: **11 %** (oficial 1.969 · OSM 330 · escrito 3.660).

**Vista:** barrio = oficial > OSM > escrito (`fuente_barrio`); **comuna solo oficial**.

## Fase 3 — el análisis de proveedores y coste (2026-10-10, antes de decidir)

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
