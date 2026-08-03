-- ============================================================================
-- Ampliar columnas de texto de dimensiones a TEXT (evita "value too long").
-- Archivo: sql/marts/07_widen_text.sql  (ejecutar DESPUÉS de 01..06).
-- Idempotente DE VERDAD desde el 2026-08-03 (antes reventaba al re-ejecutarlo,
-- ver el aviso del bloque DO).
-- ============================================================================

-- v_cartera depende de columnas de dim_tercero → recrear tras el ALTER.
-- ⚠⚠ CASCADE, y no es opcional: desde 30_cartera_dashboards.sql cuelga de esta
-- vista la MV `mv_cartera_saldo`, asi que sin CASCADE este DROP falla y el
-- fichero deja de ser re-ejecutable. La MV la vuelve a crear el 30, que corre
-- despues — pero si ejecutas ESTE fichero suelto, **tienes que correr el 30 y el
-- 24 detras** o la hoja de cartera se queda sin datos y responde 503.
DROP VIEW IF EXISTS marts.v_cartera CASCADE;

-- ⚠⚠ EL `ALTER` VA COLUMNA A COLUMNA Y SOLO SI HACE FALTA, y esto no es
-- ceremonia. La cabecera decia «Idempotente» y era mentira: PostgreSQL rechaza
-- `ALTER COLUMN ... TYPE` en cuanto una vista depende de la columna, **aunque el
-- tipo ya sea el destino y el ALTER no fuera a cambiar nada**. Con el tiempo
-- fueron naciendo vistas encima (`v_exportaciones` sobre `dim_diario.nombre`,
-- entre otras) y el fichero paso a reventar a media ejecucion:
--
--   cannot alter type of a column used by a view or rule
--   DETAIL: rule _RETURN on view marts.v_exportaciones depends on column "nombre"
--
-- Reventaba ANTES de llegar a recrear `v_cartera`, asi que quien corriera la
-- carpeta en orden se quedaba sin la vista y sin saber por que. Comprobando el
-- tipo primero, lo que ya es TEXT no se toca y el fichero vuelve a ser lo que
-- dice ser. Si alguna columna sigue sin migrar Y tiene vistas encima, ahi si
-- fallara — y entonces el fallo es real y hay que recrear esas vistas.
DO $$
DECLARE
    objetivo TEXT[][] := ARRAY[
        ['dim_tercero',      'nombre'],
        ['dim_tercero',      'identificacion'],
        ['dim_tercero',      'tipo_cliente'],
        ['dim_tercero',      'ciudad'],
        ['dim_tercero',      'departamento'],
        ['dim_tercero',      'pais'],
        ['dim_producto',     'codigo'],
        ['dim_producto',     'nombre'],
        ['dim_producto',     'categoria'],
        ['dim_diario',       'nombre'],
        ['dim_centro_costo', 'nombre'],
        ['dim_vendedor',     'nombre']
    ];
    i INT;
BEGIN
    FOR i IN 1 .. array_length(objetivo, 1) LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'marts'
              AND table_name   = objetivo[i][1]
              AND column_name  = objetivo[i][2]
              AND data_type   <> 'text'
        ) THEN
            EXECUTE format('ALTER TABLE marts.%I ALTER COLUMN %I TYPE TEXT',
                           objetivo[i][1], objetivo[i][2]);
            RAISE NOTICE 'marts.%.% -> TEXT', objetivo[i][1], objetivo[i][2];
        END IF;
    END LOOP;
END $$;

-- Recrear v_cartera (igual que en 06_cartera_en_hecho.sql).
-- ⚠⚠ ESTE ARCHIVO CORRE DESPUÉS DEL 06, así que ESTA es la definición que queda.
-- Las dos tienen que ser idénticas: el 06 explica por qué está `es_nota_debito`.
CREATE VIEW marts.v_cartera AS
SELECT
    f.linea_id, f.factura_id, f.numero, f.tipo_movimiento, f.estado_pago,
    f.tercero_id, t.nombre AS tercero_nombre, t.identificacion, t.tipo_cliente,
    f.empresa_id, e.nombre AS empresa_nombre,
    f.fecha_key, f.fecha_vencimiento_key,
    f.saldo_pendiente,
    COALESCE(dj.codigo IN ('NDY', 'NDEXP'), FALSE)        AS es_nota_debito,
    NULLIF(btrim(split_part(f.referencia, ',', 1)), '')   AS documento_origen
FROM marts.fact_movimiento_contable f
LEFT JOIN marts.dim_tercero t ON t.tercero_id = f.tercero_id
LEFT JOIN marts.dim_empresa e ON e.empresa_id = f.empresa_id
LEFT JOIN marts.dim_diario  dj ON dj.diario_id = f.diario_id
WHERE f.es_cxc IS TRUE
  AND COALESCE(f.saldo_pendiente, 0) <> 0;
