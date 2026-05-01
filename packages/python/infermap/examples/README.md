# infermap Examples

Hands-on examples to try infermap. Each example is self-contained — just run it.

**Two languages:**

- **Python examples** (below, and in this directory) — the original implementation. Run with `python examples/01_basic_mapping.py`.
- **[TypeScript examples](./typescript/)** — for the `infermap` npm package, including a Next.js Edge Runtime route handler, custom scorer, and SQLite → JSON schema-definition mapping.

## Quick Start

```bash
pip install infermap
cd examples/
```

| Example | What it shows |
|---------|--------------|
| `01_basic_mapping.py` | Map a messy CSV to a clean target schema |
| `02_dataframe_mapping.py` | Map in-memory DataFrames (Polars + Pandas) |
| `03_database_mapping.py` | Map a CSV to a SQLite database table |
| `04_schema_file.py` | Use a YAML schema definition for extra control |
| `05_custom_scorer.py` | Write your own scorer plugin |
| `06_save_and_reuse.py` | Save a mapping config and reapply it later |
| `07_cli_walkthrough.sh` | CLI commands you can copy-paste |

## Sample Data

| File | Description |
|------|------------|
| `data/crm_export.csv` | Messy CRM data (10 records) |
| `data/erp_customers.csv` | Clean ERP target schema |
| `data/healthcare_records.csv` | Healthcare source data |
| `data/patient_schema.yaml` | YAML schema for healthcare target |
| `data/ecommerce_orders.csv` | E-commerce order data |
| `data/warehouse_schema.csv` | Warehouse target schema |
