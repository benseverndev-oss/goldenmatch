# Database Scanning

Scan database tables directly — no CSV export needed.

## Install

```bash
pip install goldencheck[db]  # adds connectorx
```

## Usage

```bash
# Postgres
goldencheck scan-db "postgresql://user:pass@host/db" --table orders

# With domain pack
goldencheck scan-db "postgresql://..." --table patients --domain healthcare

# Custom SQL query
goldencheck scan-db "snowflake://..." --query "SELECT * FROM orders WHERE date > '2024-01-01'"

# JSON output
goldencheck scan-db "bigquery://project" --table events --json

# HTML report
goldencheck scan-db "postgresql://..." --table users --html report.html
```

## Supported Databases

Any database supported by [connectorx](https://github.com/sfu-db/connector-x):

- PostgreSQL
- MySQL
- SQLite
- SQL Server
- Oracle
- Snowflake
- BigQuery
- Redshift
- Trino/Presto

Falls back to SQLAlchemy + pandas if connectorx is not available.

## How It Works

1. Fetches rows from the database (respects `--sample-size`, default 100K)
2. Writes to a temp CSV
3. Runs the full GoldenCheck profiler pipeline
4. Returns findings + profile (same as file scanning)

## Python API

```python
from goldencheck.engine.db_scanner import scan_database

findings, profile = scan_database(
    "postgresql://user:pass@host/db",
    table="orders",
    sample_size=50000,
    domain="ecommerce",
)
```
