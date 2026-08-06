-- ============================================================================
-- 33_producto_identificadores.sql — identificadores externos del producto.
-- Archivo: sql/marts/33_producto_identificadores.sql. Idempotente (ADD COLUMN IF NOT EXISTS).
--
-- POR QUÉ: `dim_producto` solo tenía el código INTERNO (`codigo` = `default_code` de Odoo, tipo
-- `PCN01`). Eso no sirve para hablar con nadie de fuera: los retailers, Nielsen y el comercio en
-- general identifican el producto por su **código de barras / EAN**.
--
-- ⭐ LO QUE ESTO DESBLOQUEA (medido 2026-08-06): el UPC de Nielsen **SÍ es nuestro EAN**, 18 de 18
-- (100 %). La documentación afirmaba lo contrario («el UPC no casa con ningún código propio, 0 de
-- 18; la hoja es autónoma y no se cruza con ventas») porque la prueba se hizo contra `codigo`
-- (`PCN01`), que jamás iba a coincidir con un EAN de 13 dígitos. Con `codigo_barras` el cruce
-- sell-out (Nielsen) ↔ sell-in (nuestras ventas) es posible producto a producto.
--
-- ── QUÉ SE TRAE Y QUÉ NO (verificado contra la API de Odoo, no supuesto) ─────────────
-- `product.product` expone estos candidatos a identificador. Solo UNO tiene datos:
--
--   campo                              store   cobertura en los 85 comerciales
--   barcode                            SÍ      36 (78 % de los 46 productos SUELTOS)   ← se trae
--   valid_ean (computado)              no      36 de 36 válidos, pero MIENTE en bulk   ← NO se trae
--   hs_code (arancelario)              no      0 %   VACÍO en Odoo                     ← NO se trae
--   unspsc_code_id (categoría DIAN)    no      0 %   VACÍO en Odoo                     ← NO se trae
--   x_studio_char_field_6em_1i24fekik  no      0 %   VACÍO ("Nuevo Texto", de Studio)   ← NO se trae
--
-- ⚠⚠ `ean_valido` NO SE LEE DE ODOO, se calcula (`_ean13_valido` en etl_dw_marts.py). El campo
-- `valid_ean` de Odoo es computado (store=False) y **en lectura masiva devuelve False para la
-- mayoría**: medido 2026-08-06, en un `read` de los 1.102 productos solo **47 de los 330** con
-- código llegaron con True, mientras que leyendo los mismos 10 en un lote pequeño Odoo devuelve
-- True para todos. Traerlo de ahí dejaba la columna mal poblada SEGÚN EL TAMAÑO DEL LOTE, sin un
-- solo error a la vista (se detectó porque el conteo dio 26 de 36 en vez de 36). El checksum
-- EAN-13 es una función pura del código: calcularlo es determinista y no depende de Odoo.
--
-- `hs_code` y `unspsc_code_id` **no se añaden a propósito**: serían columnas 100 % NULL. El día que
-- el negocio las llene en Odoo, añadirlas aquí es una línea en este archivo y otra en
-- `_filas_productos` de etl_dw_marts.py. (`hs_code` sería útil para exportación y `unspsc_code_id`
-- para la facturación electrónica.)
--
-- ── CALIDAD DEL DATO (medida en los 36 comerciales con código) ───────────────────────
--   · 36 de 36 con prefijo **770** = GS1 Colombia ⇒ son EAN registrados, no inventados
--   · 36 de 36 con **13 dígitos** (EAN-13 correcto) y `valid_ean = true` (checksum ok)
--   · **ÚNICO** en todo el catálogo: 0 duplicados en los 330 productos que tienen código
--     ⇒ se puede usar como llave de negocio para cruzar con fuentes externas
--
-- ⚠ LOS KITS NO TIENEN EAN, y no es un error: **0 de 39**. Un kit se arma y se factura como
-- producto propio pero no lleva código de barras registrado. Los 49 comerciales sin código son
-- 39 kits + 10 productos sueltos. **NO se deriva el EAN del kit desde sus componentes**: sería
-- inventar un identificador que GS1 no emitió y que ningún retailer reconocería.
--
-- ⚠ Fuera de los comerciales hay códigos placeholder tipo `1000000000001` (catálogo general, no
-- PCN/KD/TNG/B8). Por eso la columna se llama `codigo_barras` (fiel a `barcode` de Odoo, que
-- admite cualquier simbología) y no `ean`: para filtrar solo EAN reales está `ean_valido`.
-- ============================================================================

ALTER TABLE marts.dim_producto
    ADD COLUMN IF NOT EXISTS codigo_barras VARCHAR(32),
    ADD COLUMN IF NOT EXISTS ean_valido    BOOLEAN;

COMMENT ON COLUMN marts.dim_producto.codigo_barras IS
  'barcode de Odoo (product.product.barcode). El identificador con el que el producto se conoce '
  'FUERA de la casa: retailers y Nielsen. UNICO en el catalogo (0 duplicados en 330). En los 36 '
  'comerciales que lo tienen es EAN-13 con prefijo 770 (GS1 Colombia). ⚠ NULL en los 39 KITS: no '
  'llevan codigo de barras registrado, y NO se deriva de sus componentes.';

COMMENT ON COLUMN marts.dim_producto.ean_valido IS
  'El codigo_barras pasa el checksum EAN-13. ⚠ Se CALCULA en el ETL (_ean13_valido), NO se lee del '
  'campo valid_ean de Odoo, que es computado y en lectura masiva devuelve False para la mayoria '
  '(47 de 330 en un read de 1.102). Separa los EAN reales de los placeholder tipo 1000000000001 '
  '(checksum invalido). NULL = sin codigo, que NO es lo mismo que codigo invalido (false).';

-- Indice: el caso de uso es entrar POR el codigo de barras (cruzar Nielsen, un retailer, un
-- escaneo). Parcial para no indexar los ~772 productos sin codigo.
CREATE INDEX IF NOT EXISTS ix_dim_producto_codigo_barras
    ON marts.dim_producto (codigo_barras)
    WHERE codigo_barras IS NOT NULL;
