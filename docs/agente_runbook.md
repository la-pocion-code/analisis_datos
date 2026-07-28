# Runbook — Agente financiero conversacional (n8n + reportes-api + Claude)

Gerencia y contabilidad preguntan por **Google Chat** (luego WhatsApp) y reciben respuestas +
informes PDF/datos. Arquitectura: **n8n** (canales + AI Agent Claude + memoria 1-a-1) → **reportes-api**
(FastAPI, este repo) → **Postgres `marts`** (cifras deterministas = calzan con el BI).

## Componentes en el repo
- `sql/marts/20_agente.sql` — esquema `agente` (whitelist + auditoría) y rol `agente_ro` (SELECT-only).
- `reportes-api/` — microservicio FastAPI (endpoints curados + tool SQL SELECT-only).

---

## F0 — Datos y seguridad (Postgres)
1. Aplicar `psql < sql/marts/20_agente.sql`.
2. Poner contraseña al rol RO: `ALTER ROLE agente_ro PASSWORD '<secreto>';`
3. Cargar whitelist:
   ```sql
   INSERT INTO agente.usuarios_autorizados (canal, identidad, nombre, rol, empresas) VALUES
     ('google_chat','gerente@lapocion.com','Gerencia','gerencia','{1,8}'),
     ('google_chat','conta@lapocion.com','Contabilidad','contabilidad','{1,8}');
   ```
4. Verificar cifras de las vistas vs deck (PCN mayo): `estado_resultados(8, 2026, 5)` → Ingresos ≈ 6.830M,
   Utilidad Bruta ≈ 4.029M (59%).

## F1 — Desplegar `reportes-api` en Railway
- Nuevo **service** en el proyecto Railway, **Root Directory = `reportes-api`**.
- Build: `pip install -r requirements.txt`. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- Variables: `RO_DB_HOST/PORT/NAME=railway`, `RO_DB_USER=agente_ro`, `RO_DB_PASSWORD=<secreto>`,
  `REPORTES_API_KEY=<clave larga>` (la misma que usará n8n).
- Probar: `POST /health`; `POST /estado-resultados` con `{"canal":"google_chat","identidad":"conta@lapocion.com","empresa_id":8,"anio":2026,"mes":5}` y header `X-API-Key`.

## F2 — n8n en Railway
- Desplegar n8n (template de Railway; requiere su propio Postgres/volumen y `N8N_ENCRYPTION_KEY`,
  `WEBHOOK_URL` con el dominio público).
- Credenciales en n8n: **Anthropic** (Claude) y **HTTP Header Auth** (`X-API-Key` → reportes-api).
- Workflow **Agente Financiero**:
  1. Trigger del canal (F3/F4).
  2. Nodo de autorización (opcional; la API ya valida whitelist).
  3. **AI Agent** (LangChain) con modelo Claude, **Memory = Postgres Chat Memory** (session key = id de
     usuario/espacio → conversación 1-a-1), y **Tools = HTTP Request** a cada endpoint de reportes-api
     (`estado-resultados`, `balance`, `top-clientes`, `ventas-categoria`, `query`).
  4. System prompt: rol de analista financiero de La Poción; responder solo con datos de las herramientas;
     pedir empresa/periodo si faltan; confidencialidad (no revelar fuera de alcance).
  5. **Multi-agente (opcional):** sub-workflows como tools — "contable" (ER/Balance/Flujo), "comercial"
     (ventas/canales/clientes), "ad-hoc" (SQL). El agente raíz enruta.
  6. Responder al canal (texto + link/adjunto del informe).

## F3 — Canal Google Chat (primero)
- En Google Cloud (proyecto del service account existente): habilitar **Google Chat API**, configurar la
  **Chat app** con endpoint HTTP = webhook del workflow n8n, publicarla **interna** al dominio lapocion.com.
- Probar DM 1-a-1 con un usuario de la whitelist.

## F4 — Canal WhatsApp (después)
- Meta Business + número + **WhatsApp Cloud API**; webhook a n8n; plantillas aprobadas para mensajes fuera
  de la ventana de 24h. Reusa el mismo AI Agent (solo cambian trigger y nodo de salida).

---

## Pendientes / mejoras
- **`canal` en ventas:** exponer `f.canal` en `marts.v_ventas_producto` para la hoja "Canales de venta"
  (hoy se usa `categoria` de cliente como proxy). Cambio de 1 línea en `sql/marts/14_ventas.sql`.
- ✅ HECHO **Notas débito:** ya excluidas en SQL (`v_ventas_producto`), salvo las que **anulan una nota
  crédito**, que cuentan en el mes de la factura que reviven (`marts.map_nd_factura`,
  `sql/marts/25_nd_factura.sql`). El ajuste DAX de FASE 4 queda redundante. Las excluidas quedan en
  `marts.v_notas_debito_excluidas`. Ver `docs/guia_bi_ventas.md` §6.5.
- **Cuentas clave (UB por cliente):** crear vista que cruce ingreso (clase 4) y costo (clase 6) por
  `tercero_id` para dar Utilidad Bruta por cliente.
- **PDF/Excel:** implementar `/pdf` y `/excel` reusando `classes/clase_reportes_new.py`,
  `classes/send_mail.py` y `classes/drive_loader.py`; devolver link de Drive o adjunto.
- **Balance "Resultado del ejercicio":** en SQL, calcular como neto de clases 4–7 acumulado (igual que la
  medida DAX `marts valor balance`) para que cuadre Activo = Pasivo + Patrimonio.
