-- ============================================================================
-- 36_producto_comercial.sql — `dim_producto.disponible_pos`: el marcador de COMERCIALIZACIÓN
--
-- Idempotente. Aplicar antes de repoblar con `python etl_dw_marts.py --backfill-productos`.
--
-- POR QUÉ EXISTE
-- --------------
-- Hasta el 2026-09-08, «producto comercial» en `v_ventas_producto` era el PREFIJO del
-- `default_code` (`PCN%/KD%/TNG%/B8%`). Eso es una CONVENCIÓN DE NOMBRES, no un dato del negocio,
-- y fallaba de tres maneras medidas en 2026:
--   · producto SIN `default_code` -> invisible: 9 kits, 390.085.902 (el 99,7 % vendido por Shopify)
--   · código con otro prefijo -> invisible: sachets `SCHT0x` 13,4 M, neceser, merchandising
--   · prefijo correcto pero no es producto terminado -> entra sin deber: PCNKIT16 7,7 M, PCNKIT39 0,9 M
--
-- La definición del negocio (decisión de William, 2026-09-08) es: **producto terminado, dentro de
-- una LÍNEA, y marcado como disponible en el punto de venta**. Los `Add On's` y los `Sachet` son
-- producto terminado pero NO son comerciales.
--
-- ⚠⚠ `available_in_pos` está STORED **solo en `product.template`**; en `product.product` es un
-- campo `related` con `store = False` (verificado con `fields_get`). Leerlo del producto repetiría
-- el fallo de `valid_ean`, que en lecturas masivas devolvía valores falsos. El ETL lo resuelve por
-- `product_tmpl_id` y lee el TEMPLATE (ver `plantillas_pos` en etl_dw_marts.py).
--
-- ⛔ EL FLAG SOLO NO SIRVE: de los 93 templates del catálogo con `available_in_pos = true`, 3 están
-- FUERA de `Inventario/Producto Terminado`, y uno es `Descuento financiero en ventas`
-- (−2.876 M en 2026). Definir «comercial» solo por esta columna hundiría las ventas. La condición
-- de `v_ventas_producto` lleva SIEMPRE la categoría además del flag.
--
-- ⛔ Y NUNCA se filtra por `active`: los productos ARCHIVADOS siguen contando (decisión de William).
-- La base tiene histórico; si se filtraran, la venta del año pasado desaparecería de los informes y
-- un mes cerrado cambiaría de cifra solo con el tiempo, cada vez que alguien archive un producto.
-- De hecho los 9 kits de arriba están archivados en Odoo y venden todos los meses.
-- ============================================================================

ALTER TABLE marts.dim_producto
  ADD COLUMN IF NOT EXISTS disponible_pos BOOLEAN;

COMMENT ON COLUMN marts.dim_producto.disponible_pos IS
  'available_in_pos de product.TEMPLATE (en product.product es related sin almacenar). Marcador de '
  'comercialización del negocio: junto con la categoría bajo Inventario/Producto Terminado/<Línea> '
  'define qué es un PRODUCTO COMERCIAL en v_ventas_producto. ⛔ El flag SOLO no sirve: 3 templates '
  'marcados están fuera de Producto Terminado y uno es «Descuento financiero en ventas» (−2.876 M '
  'en 2026). NULL = producto aún sin repoblar (por eso la vista usa IS TRUE, no = TRUE).';

-- Índice parcial: los comerciales son ~76 de 1.100 productos, así que el filtro de la vista se
-- resuelve por índice en vez de recorrer la dimensión entera.
CREATE INDEX IF NOT EXISTS ix_dim_producto_pos
  ON marts.dim_producto (producto_id)
  WHERE disponible_pos IS TRUE;
