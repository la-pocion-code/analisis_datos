# La Poción — repositorio de datos (`analisis_datos`)

Scripts de **ETL y BI** del área de análisis de datos. Lo principal es un **cron en Railway** que
carga el **Data Warehouse** (`Odoo → PostgreSQL`, esquema `marts`) **cada 15 minutos**, y que
alimenta los tableros de la **intranet** de la compañía.

⚠ **Este repo es la capa de DATOS.** Todo lo de base de datos (DDL, vistas materializadas, roles,
refresco) vive aquí; la intranet (`proyecto pocion/intranet`) es el frontend/backend y solo hace
`SELECT`. Los dos no se mezclan.

## Por dónde empezar

| Archivo | Para qué |
|---|---|
| ⭐ **[`checkpoint.md`](checkpoint.md)** | **el estado del repo**: qué corre solo, en qué estado está cada hoja y qué falta |
| [`CLAUDE.md`](CLAUDE.md) | el contexto completo y las trampas medidas de cada dominio |
| [`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md) | qué comando correr y cuándo |
| [`docs/ARQUITECTURA_DW.md`](docs/ARQUITECTURA_DW.md) | árbol del repo, cómo funciona el cron y el plan por fases |
| [`docs/dashboards_intranet.md`](docs/dashboards_intranet.md) | contrato de datos de los tableros |

## Requisitos

`pip install -r requirements.txt` y un `.env` con las credenciales de Odoo, PostgreSQL, correo y
Google Drive. ⚠ **El `.env` no se versiona**: las variables se referencian por nombre, nunca por
valor. Los nombres están listados en [`CLAUDE.md`](CLAUDE.md).

Idioma del proyecto: **español**.
