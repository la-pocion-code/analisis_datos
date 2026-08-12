# AGENTS.md — Proyecto BI La Poción (analisis_datos)

⚠ **Este archivo ya NO contiene el contexto del repo. Es solo un puntero, y es deliberado.**

Hasta el 2026-08-12 aquí vivía una copia del contenido de `CLAUDE.md`. **Divergió**: se quedó en el
2026-08-05 y perdió, entre otras cosas, la **hoja de compras entera**, los identificadores de
producto (EAN), las tres lecturas del IVA en ventas y la corrección de la jerarquía de markets de
Nielsen. Un agente que leyera este archivo trabajaría con un mapa desactualizado del repo y no
sabría que existen esas cosas.

**Por eso hay UNA sola fuente de verdad. No volver a copiar el contexto aquí:** si se duplica,
vuelve a divergir en una semana.

## Empezar por aquí

| Archivo | Para qué |
|---|---|
| ⭐ [`checkpoint.md`](checkpoint.md) | **estado del repo**: qué corre solo, en qué estado está cada hoja, qué falta. ≤200 líneas |
| ⭐ [`CLAUDE.md`](CLAUDE.md) | **el contexto completo** y las trampas medidas de cada dominio |
| [`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md) | qué comando correr y cuándo |
| [`docs/ARQUITECTURA_DW.md`](docs/ARQUITECTURA_DW.md) | árbol del repo, cron y plan por fases |
| [`docs/dashboards_intranet.md`](docs/dashboards_intranet.md) | contrato de datos de los tableros de la intranet |

## Reglas que no dependen de qué agente seas

- **Idioma del proyecto: español.**
- **NO ejecutar el cron ni conectarse a Odoo/PostgreSQL en vivo sin que el usuario lo pida.**
- **NUNCA exponer valores de `.env`**: referenciar las variables por su nombre.
- Antes de tocar el ETL, leer [`docs/ARQUITECTURA_DW.md`](docs/ARQUITECTURA_DW.md).
- ⚠ **Los repos no se mezclan**: aquí vive todo lo de base de datos (DDL, MV, roles, refresco); la
  intranet (`proyecto pocion/intranet`) es frontend y backend y solo hace `SELECT`.
- ⚠ **La lógica de negocio baja al SQL**: lo que era medida DAX o paso de Power Query pasa a
  vistas/MV/columnas del hecho.
