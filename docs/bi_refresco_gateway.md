# Refresco automático del BI en Power BI Service (gateway + certificado Railway)

Runbook para que **DASHBOARD POCION** se actualice solo en Power BI Service leyendo de
`marts` en Railway PostgreSQL, **sin depender de un PC local que se apaga**.

Origen: **DSN ODBC `pocion_marts`** (driver *PostgreSQL Unicode(x64)*, `SSLmode=require`) → base
`railway`, esquema `marts`. (Antes era el conector nativo `PostgreSQL.Database("switchback.proxy.rlwy.net:37790","railway")`; se cambió a ODBC, ver abajo.)

---

## Resumen de los 2 problemas (son independientes)

| Problema | Causa | Se arregla con |
|---|---|---|
| **Error de refresco** "The remote certificate is invalid according to the validation procedure" | El Postgres de Railway presenta un cert **`CN=localhost`** (SAN `DNS:localhost`) que **nunca** coincide con el hostname del proxy `switchback.proxy.rlwy.net`. Power BI hace *verify-full* → falla aunque desmarques "Encrypt connection" (el Service valida igual). **Importar el cert NO basta** (el problema no es la cadena de confianza sino el mismatch de hostname). | **Conectar por ODBC** (DSN `pocion_marts`, driver psqlODBC) con **`SSLmode=require`**: cifra la conexión **sin verificar hostname**. **Verificado:** `require`/`prefer` conectan; `verify-full` falla. |
| **"Cada hora" no se puede** | Power BI **Pro = máx. 8 refrescos/día** (techo de licencia). | En Pro: **~cada 3 h (8/día)**. Para 48/día real (cada hora): **PPU** o **Fabric/Premium**. |

> ⚠ El gateway es **solo Windows** → **no** corre en Railway. Necesita un host Windows **encendido 24/7**
> (p. ej. el **VPS**). ⚠ Un **VNet data gateway NO sirve** aquí (exige Premium).
> ⚠ **`SSLmode=require` cifra pero no valida la identidad del servidor** (acepta el `CN=localhost`). Es lo
> mismo que hace hoy cualquier cliente que se conecta al proxy de Railway; aceptable para este proxy
> interno. Si algún día Railway expone un CA/hostname estable, subir a `verify-full`.

---

## ODBC — crear el DSN `pocion_marts` (arregla el error de certificado)

El modelo ahora lee por **ODBC** (código M en `docs/bi_conexiones_marts.md`). El DSN debe existir con el
**mismo nombre `pocion_marts`** en **cada máquina que abra el `.pbix` o refresque** (PC del analista y,
sobre todo, el **VPS del gateway**). Driver requerido: **PostgreSQL Unicode(x64)** (psqlODBC); si no está,
instalar *psqlODBC* (https://www.postgresql.org/ftp/odbc/versions/).

**Sin guardar la contraseña en el DSN** (la suministra Power BI / el gateway, cifrada).

### Local (PC del analista) — User DSN (no requiere admin)
```powershell
Add-OdbcDsn -Name "pocion_marts" -DriverName "PostgreSQL Unicode(x64)" -DsnType User -SetPropertyValue @(
  "Servername=switchback.proxy.rlwy.net",   # = DB_HOST del .env
  "Port=37790",                              # = DB_PORT
  "Database=railway",                        # = DB_NAME
  "SSLmode=require",                         # cifra sin verificar hostname (clave del fix)
  "Username=postgres"                        # = DB_USER  (SIN Password)
)
Get-OdbcDsn -Name "pocion_marts" -DsnType User      # verificar
```

### VPS Windows 24/7 (producción, host del gateway) — **System DSN** (requiere admin)
El servicio del gateway corre como cuenta de servicio y **solo ve los System DSN** → en el VPS debe ser
`-DsnType System` (PowerShell **como administrador**):
```powershell
Add-OdbcDsn -Name "pocion_marts" -DriverName "PostgreSQL Unicode(x64)" -DsnType System -SetPropertyValue @(
  "Servername=switchback.proxy.rlwy.net","Port=37790","Database=railway","SSLmode=require","Username=postgres"
)
```

### Probar el DSN (solo lectura, sin exponer la clave)
```powershell
$cfg=@{}; Get-Content .\.env | %{ if($_ -match '^\s*([^#=]+?)\s*=\s*(.*)\s*$'){ $cfg[$matches[1]]=$matches[2].Trim('"').Trim("'") } }
$cs="DSN=pocion_marts;Uid=$($cfg['DB_USER']);Pwd=$($cfg['DB_PASSWORD']);"
$c=New-Object System.Data.Odbc.OdbcConnection $cs; $c.Open(); "State=$($c.State)"; $c.Close()
```

> ⚠ **Railway reasigna el proxy** (host/puerto) en reinicios/migraciones. Cuando pase, editar **solo el
> DSN** — no las consultas M — con `Set-OdbcDsn -Name pocion_marts -DsnType <User|System> -SetPropertyValue @("Servername=<nuevo>","Port=<nuevo>")`.
> Esa es la ventaja de ODBC: el endpoint vive en **un** sitio, no repetido en ~20 consultas.

---

## Parte 1 — Quitar la dependencia del PC local (Opción A, en Pro, ya)

### 1. Elegir el host 24/7 del gateway
- **Si hay un servidor/mini-PC de oficina siempre encendido:** úsalo (costo $0).
- **NAS con VM Windows (recomendado si el NAS lo soporta, costo $0 de nube):** el gateway es **solo
  Windows** → NO se instala nativo en el NAS (corren Linux) ni en Docker (no soportado por Microsoft).
  Pero se puede correr una **VM Windows dentro del NAS** e instalar el gateway ahí. Requisitos del NAS:
  CPU **x86-64** (los ARM no virtualizan Windows), app de virtualización (Synology *Virtual Machine
  Manager* / QNAP *Virtualization Station* / TrueNAS SCALE-KVM), **≥4 GB** asignables a la VM (NAS de
  8 GB+), licencia Windows y salida de red a Railway. Como el NAS ya está 24/7, es la mejor opción sin
  costo de nube.
- **Si no hay ninguno de los anteriores:** una **VM pequeña en la nube** (Azure B1s/B2s ~USD 8–30/mes o
  equivalente). El software del gateway es **gratis**; solo pagas el host.

### 2. Instalar el gateway estándar
1. Descargar **On-premises data gateway (standard mode)** (no "personal").
2. Instalarlo en el host 24/7 e iniciar sesión con la **misma cuenta** del Power BI Service.
3. Registrar el gateway (nombre y clave de recuperación → guardar la clave en sitio seguro).

### 3. Crear el DSN ODBC `pocion_marts` en el host del gateway
Esto reemplaza al viejo "importar el certificado" (que **no** arreglaba el mismatch `CN=localhost`).
Crear el **System DSN** `pocion_marts` con `SSLmode=require` en el VPS del gateway — pasos en la sección
**"ODBC — crear el DSN `pocion_marts`"** de arriba. Requisitos: driver *PostgreSQL Unicode(x64)* (psqlODBC)
instalado en el VPS y el **mismo nombre de DSN** que usa el `.pbix`.

> (La importación del cert de Railway a *Raíz de confianza* quedó como nota histórica en
> *Troubleshooting*; con ODBC `SSLmode=require` no hace falta.)

### 4. Configurar el origen en Power BI Service
1. app.powerbi.com → **Configuración → Conexiones y puertas de enlace → Gateways**.
2. En tu gateway → **Nuevo origen de datos**:
   - Tipo: **ODBC**.
   - Cadena de conexión: **`dsn=pocion_marts`** (⚠ el DSN debe existir como **System DSN** en el VPS).
   - Autenticación: **Básica** → usuario/clave = variables `DB_USER` / `DB_PASSWORD` (del `.env`, **no**
     pegar valores en ningún doc).
   - **Nivel de privacidad: Organizational**.
3. Guardar. Debe decir *Conexión correcta* (si no, ver Troubleshooting).

### 5. Asignar el dataset al gateway y programar refresco
1. Workspace → dataset **DASHBOARD POCION** → **Configuración**.
2. **Conexión de puerta de enlace** → seleccionar el gateway y mapear el origen ODBC → *Aplicar*.
3. **Credenciales del origen de datos**: si pide, volver a autenticar (Básica, Organizational).
4. **Actualización programada → Activada** → añadir horarios. En Pro son **8 franjas/día máx.**;
   ponerlas **~15 min después de cada hora del cron** para leer datos frescos, p.ej.:
   `06:15, 09:15, 12:15, 15:15, 18:15, 21:15` (+2 si se quiere).
   ⚠ El cron del DW pasó a **`*/15 * * * *`**, así que ya no hace falta esperar a la hora en punto:
   cualquier franja sirve. Eso sí, el **cierre** (categoría, puentes NC/ND, reversos) solo corre en el
   tick `:00`, así que para leer los datos ya consolidados conviene seguir apuntando a `hh:10`-`hh:15`.
5. **Actualizar ahora** para probar → el historial debe salir en verde, sin el error de certificado.

---

## Parte 2 — Si "cada hora" (24/día) es requisito

En **Pro no es posible** (8/día). Para 48/día:

- **Fabric capacity F2 (pausable)** — vía más económica a capacidad dedicada; los lectores pueden seguir
  en Pro/Free según reparto. O **PPU** (cada consumidor del informe necesita PPU → escala con lectores).
- Con Premium/Fabric se abre el **XMLA endpoint** → se puede **disparar el refresco desde el cron de
  Railway** vía **Power BI REST API** justo al terminar de cargar `marts` (event-driven: el dashboard
  queda fresco a los minutos, sin refrescos en vacío). Requiere un **service principal** (App
  Registration) con permiso de dataset refresh. ⚠ **El gateway de la Parte 1 sigue siendo necesario**
  (el pull sigue saliendo de Postgres). Esto se planifica aparte si se elige.

---

## Alternativa C (sin gateway ni VM, más rework)
Que el cron de Railway **exporte** las tablas del modelo a **Azure Blob (CSV/Parquet)** o a un
**Dataflow**; Power BI Service refresca Blob **sin gateway** y desaparece el problema del certificado.
⚠ **No sube el techo de refrescos**: en Pro sigue 8/día. Solo conviene si además quieres eliminar el
host Windows.

---

## Troubleshooting
- **Sigue "remote certificate invalid":** verifica que las consultas M usan **ODBC** (`Odbc.DataSource`/
  `Odbc.Query` con `dsn=pocion_marts`) y **no** el conector nativo `PostgreSQL.Database`, y que el DSN
  tiene **`SSLmode=require`** (no `verify-full`/`verify-ca`). `verify-full` **falla** con este servidor
  (`CN=localhost`). Si aún falla, revisa que el driver del DSN sea *PostgreSQL Unicode(x64)*.
- **"No aparece / no reconoce el origen":** el DSN debe existir con el **nombre exacto `pocion_marts`** en
  el host (y como **System DSN** en el VPS del gateway; un User DSN no lo ve el servicio del gateway).
- **Railway reasignó el proxy** (cambió host/puerto) → editar **solo el DSN** con `Set-OdbcDsn` (ver
  §ODBC); no hay que tocar las consultas M.
- **Refresco falla por privacidad/firewall:** poner el origen en **Organizational**; si combina
  consultas, en Desktop: *Opciones → ARCHIVO ACTUAL → Privacidad → "Omitir siempre los niveles de
  privacidad"* y republicar.
- **Vistas (`v_*`) que no cargan:** este driver **no lista vistas** en la navegación → deben leerse con
  `Odbc.Query("dsn=pocion_marts","select * from marts.NOMBRE_VISTA")` (ver `docs/bi_conexiones_marts.md`).
- **(Histórico — ya no aplica) Importar el cert de Railway:** antes se intentó capturar el cert
  (`openssl s_client -starttls postgres -connect switchback.proxy.rlwy.net:37790 -showcerts`) e importarlo
  a *Raíz de confianza (Equipo local)*. **No funcionaba**: el cert es `CN=localhost` y el conector hacía
  *verify-full* → mismatch de hostname. La solución ODBC `SSLmode=require` lo hace innecesario.

## Verificación final
1. `Test-NetConnection switchback.proxy.rlwy.net -Port 37790` en el host → `TcpTestSucceeded : True`.
2. `Get-OdbcDsn -Name pocion_marts` existe (System en el VPS) + test `SELECT` con `SSLmode=require` → OK.
3. Origen ODBC del gateway = *Conexión correcta*.
4. **Actualizar ahora** el dataset → historial en verde, **sin** el error de certificado.
5. Una medida `marts …` conocida coincide con Power BI Desktop.
6. El **refresco programado** corre solo aunque el PC del analista esté apagado.
