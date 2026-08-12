# notebooks/pruebas

Notebooks de **exploración y pruebas**. ⚠ **Nada de aquí forma parte de la automatización**: no los
llama el cron de Railway (`run_dw.py`), no los importa ningún script y no hace falta que funcionen.
Se conservan por el historial de exploración, no como código vivo.

| Notebook | Qué es | Estado |
|---|---|---|
| `conexion_odoo.ipynb` | conexión XML-RPC suelta a Odoo | ⚠ no corre: usa `getenv("username")`, y la variable real es `username_odoo` |
| `odoo_api_test.ipynb` | exploración de la API de Odoo (`fields_get`) | ⚠ no corre: importa `etl_odoo_incremental` (archivado) y `from db_loader import` (hoy es `classes.db_loader`) |
| `pruebas_cami.ipynb` | conciliación de Shopify contra Addi/MercadoPago | depende de archivos locales en `G:\` |

**Lo que sí es operacional está fuera de aquí:**

- `ejecuciones_anilista.ipynb` (raíz) — los procesos que se ejecutan a mano desde el área: ventas
  diarias, informes de mayoristas y envío de correos. **Ese sí se usa.**
- Los scripts del cron y los de carga a demanda: ver [`../../checkpoint.md`](../../checkpoint.md).

Más notebooks retirados, en [`../../archivado/`](../../archivado/).
