-- ============================================================================
-- 38_tercero_contacto.sql — `v_lk_tercero_contacto`: el puente TELÉFONO → TERCERO
--
-- Idempotente (CREATE OR REPLACE VIEW). ⚠ Re-ejecutar `24_rol_intranet.sql` DESPUÉS: el `GRANT`
-- a `intranet_ro` vive allí, que es su casa.
--
-- POR QUÉ EXISTE
-- --------------
-- La intranet carga las conversaciones de WhatsApp de los chatbots y necesita saber **quién de los
-- que escribe ya es cliente**. La alternativa era darle credenciales de Odoo: el ERP entero y con
-- capacidad de escritura. Una vista de solo lectura sobre `marts` que expone DOS columnas es
-- estrictamente mejor.
--
-- `intranet_ro` no puede leer `marts.dim_tercero` (verificado con `has_table_privilege`) y
-- `v_lk_tercero` no lleva el teléfono a propósito — la decisión escrita en `24_rol_intranet.sql` es
-- que un tablero de ventas no necesita datos personales de 208 k terceros.
--
-- ⚠⚠ ESTE ES EL PRIMER OBJETO CON DATO DE CONTACTO AL ALCANCE DE `intranet_ro`. Antes de esta
-- vista, ese rol no alcanzaba NI UNA columna personal: ni cédula, ni teléfono, ni correo (medido
-- 2026-09-28 sobre `pg_attribute` + `has_table_privilege`). Si mañana hay que ampliar, que se piense
-- con esto delante.
--
-- ⛔ NO LLEVA `identificacion` (la cédula). Decisión de William del 2026-09-29: el nombre, la ciudad
-- y el tipo de cliente ya salen de `v_lk_tercero` uniendo por `tercero_id`, así que para saber
-- «quién es cliente» la cédula no hace falta. Exponerla es otra decisión, con otro dueño.
--
-- ⛔ EL FILTRO NO ES `length >= 10`, Y NO ES UN DETALLE. Medido el 2026-09-29 sobre los 215.133
-- terceros con algún dígito en el teléfono, el filtro laxo se queda con 214.136 y el estricto con
-- **213.067**: la diferencia son **1.069 registros (0,50 %) cuya llave sería FABRICADA o de otro
-- país**. Desglose de esos 1.069 por largo:
--     11 dígitos → 480  ⛔ `'3218200 EXT 8530'` → llave `2182008530` (¡una extensión!);
--                          `'+1 860-574-7812'` (EE.UU.); `'30443399392'` (un dígito de más)
--     20 dígitos → 411  ⛔ DOS números concatenados en el mismo campo
--     12 sin 57  →  62  ⛔ 12 dígitos que no empiezan por indicativo colombiano
--     13-30      → 116  ⛔ variantes de lo mismo
-- `right(regexp_replace(...), 10)` no distingue: convierte la basura en una llave con forma de
-- teléfono. Los 10 dígitos son 188.085 (187.461 empiezan por 3) y los 12 con `57`, 24.979.
-- ⚠ NO confundir con los **997 terceros de menos de 10 dígitos**: esos los descarta cualquier
-- filtro, el laxo también, y no son un argumento a favor de este. El total que la vista deja fuera
-- es 2.072 = 1.069 + 997, y solo los 1.069 son mérito del filtro estricto.
--
-- ⚠⚠ LA AMBIGÜEDAD SE RESUELVE AQUÍ, NO EN QUIEN CONSUME. Medido: **14.883 de 196.549 teléfonos
-- (7,57 %) apuntan a MÁS DE UN tercero**, y hay uno con **15**. Una vista que devuelva las N filas
-- y calle invita a «tomar el primero», que atribuye la identidad equivocada **en silencio**. Por eso
-- `terceros_con_este_telefono` es una columna y no un comentario: el consumidor filtra `= 1` para
-- los cruces seguros y trata los `> 1` explícitamente.
-- ⚠ EL 7,57 % ES DE LLAVES, NO DE FILAS. En filas son **31.401 de 213.067 = 14,74 %**, porque una
-- llave ambigua aporta tantas filas como terceros tenga. Las dos cifras son correctas y miden cosas
-- distintas; decir «7,5 % de las filas» es el error fácil (lo cometí al planear esto).
--
-- ⚠ LA NORMALIZACIÓN VIVE EN UN SOLO SITIO (aquí). Odoo guarda tanto `3124950102` como
-- `+573017321181`; los últimos 10 dígitos son la parte estable. Si cada consumidor normalizara por
-- su cuenta, dos que se separen darían un cruce que baja sin que nadie sepa por qué.
--
-- ⚠ NO SE HASHEA el teléfono. Se consideró para que la intranet no reciba un directorio de ~197 k
-- números, y **no sirve**: el espacio de un móvil de 10 dígitos se enumera en segundos, así que un
-- hash sin secreto compartido es complejidad sin protección. La mitigación real es el alcance del
-- `GRANT`.
--
-- ⚠ COBERTURA: el ETL guarda UN SOLO teléfono — `_fila_tercero` hace `r.get("phone") or
-- r.get("mobile")`, así que con los dos poblados se queda con `phone` y descarta el celular. Medido
-- en Odoo: `mobile` solo está en 5.591 de 215.382 (2,6 %) y el celular real vive en `phone`, así que
-- la pérdida es de **~3.340 terceros (1,5 %)**. ⇒ El cruce del lado de la intranet dará **por debajo
-- del 29 % que midieron contra Odoo**, y eso es esperado, no un fallo. Si diera MUCHO menos, hay que
-- investigar antes de confiar en la cifra.
--
-- ⛔ SI EL CONSUMIDOR DEJA DE SER UN CRON: hoy lo consume el cargador de conversaciones (por lotes,
-- sin LLM), y por eso `intranet_ro` es el rol correcto. Si pasa a ser un **bot conversacional** —que
-- lee texto escrito por desconocidos y responde a desconocidos— esta decisión se revisa: ahí entran
-- la inyección de prompt y la exfiltración, y el patrón que corresponde es el de `20_agente.sql`
-- (rol propio + whitelist de identidades + log de auditoría de cada consulta).
-- ============================================================================

CREATE OR REPLACE VIEW marts.v_lk_tercero_contacto AS
WITH base AS (
    SELECT tercero_id,
           regexp_replace(coalesce(telefono, ''), '\D', '', 'g') AS dig
      FROM marts.dim_tercero
),
validos AS (
    SELECT tercero_id,
           right(dig, 10) AS telefono_clave
      FROM base
     -- Ver el aviso ⛔ de la cabecera: aceptar `length >= 10` metería 1.069 llaves fabricadas.
     WHERE length(dig) = 10                            -- celular nacional (3xx xxx xxxx)
        OR (length(dig) = 12 AND dig LIKE '57%')       -- el mismo, con indicativo de país
)
SELECT tercero_id,
       telefono_clave,
       count(*) OVER (PARTITION BY telefono_clave)::int AS terceros_con_este_telefono
  FROM validos;

COMMENT ON VIEW marts.v_lk_tercero_contacto IS
  'Puente TELÉFONO → TERCERO para el cargador de conversaciones de WhatsApp de la intranet. '
  '`telefono_clave` son los últimos 10 dígitos, la parte estable (Odoo guarda 3124950102 y '
  '+573017321181 indistintamente); la normalización vive AQUÍ y en un solo sitio. '
  '⛔ NO lleva `identificacion`: para saber quién es cliente basta el `tercero_id`, y el nombre y la '
  'ciudad salen de `v_lk_tercero`. ⚠⚠ El 7,57 % de los teléfonos apunta a VARIOS terceros (hasta 15): '
  'usar `terceros_con_este_telefono = 1` para el cruce seguro y tratar el resto aparte — tomar el '
  'primero atribuye la identidad equivocada en silencio. ⚠ Cobertura ~1,5 % por debajo de Odoo: el '
  'ETL guarda `phone or mobile` y descarta el celular cuando hay fijo.';

COMMENT ON COLUMN marts.v_lk_tercero_contacto.telefono_clave IS
  'Últimos 10 dígitos del teléfono, sin separadores. Solo se emiten números de 10 dígitos o de 12 '
  'que empiecen por 57: el filtro laxo `length >= 10` fabricaba llaves a partir de extensiones '
  '(''3218200 EXT 8530''), números extranjeros y campos con DOS teléfonos concatenados.';

COMMENT ON COLUMN marts.v_lk_tercero_contacto.terceros_con_este_telefono IS
  'Cuántos terceros comparten esta llave. ⚠ 1 = cruce seguro; > 1 = el teléfono NO identifica a una '
  'persona y el consumidor debe decidir explícitamente. Medido 2026-09-29: 7,57 % de las LLAVES '
  'están por encima de 1 (14.883 de 196.549), con un máximo de 15 terceros. ⚠ En FILAS son el '
  '14,74 %: una llave ambigua aporta tantas filas como terceros tenga.';
