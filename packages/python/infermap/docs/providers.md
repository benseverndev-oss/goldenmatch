---
layout: default
title: Providers
nav_order: 5
---

# Schema Providers

Providers extract a normalized `SchemaInfo` from any data source. infermap auto-detects the right provider based on the input.

## Auto-Detection

| Input | Provider |
|-------|----------|
| `.csv`, `.parquet`, `.xlsx` file path | FileProvider |
| `postgresql://`, `mysql://`, `sqlite://`, `duckdb://` URI | DBProvider |
| `.yaml`, `.yml`, `.json` file path | SchemaFileProvider |
| Polars/Pandas DataFrame | InMemoryProvider |
| `list[dict]` | InMemoryProvider |

## FileProvider

Reads CSV, Parquet, and Excel files via Polars. Samples up to 500 rows (configurable) for type detection and profiling.

```python
import infermap

# Auto-detected
result = infermap.map("source.csv", "target.csv")

# Or use directly
from infermap.providers.file import FileProvider
schema = FileProvider().extract("data.csv", sample_size=1000)
```

**Excel** requires `openpyxl`: `pip install infermap[excel]`

## DBProvider

Connects to databases and extracts column metadata + sample rows.

| Database | Install | URI Format |
|----------|---------|------------|
| SQLite | (built-in) | `sqlite:///path/to/db.sqlite` |
| PostgreSQL | `pip install infermap[postgres]` | `postgresql://user:pass@host:5432/dbname` |
| DuckDB | `pip install infermap[duckdb]` | `duckdb:///path/to/db.duckdb` |
| MySQL | `pip install infermap[mysql]` | `mysql://user:pass@host:3306/dbname` |

```bash
# Map a CSV to a live database table
infermap map incoming.csv "postgresql://user:pass@host/db" --table customers
```

**Schema drift resilience:** Because the provider introspects the database at runtime, the mapping automatically adapts when columns are added or removed from the target table.

## SchemaFileProvider

Reads YAML or JSON schema definition files with explicit field metadata.

```yaml
# target_schema.yaml
fields:
  - name: email
    type: string
    aliases: [email_address, e_mail, contact_email]
    required: true
  - name: phone
    type: string
    aliases: [telephone, tel, mobile]
  - name: zip_code
    type: string
    aliases: [postal, postcode, zip]
```

Schema files provide stronger signals to the AliasScorer and PatternTypeScorer. They augment the pipeline -- they don't bypass it.

```python
result = infermap.map("source.csv", "target.csv", schema_file="target_schema.yaml")
```

## InMemoryProvider

Works with Polars DataFrames, Pandas DataFrames, or `list[dict]`.

```python
import polars as pl

source_df = pl.DataFrame({"fname": ["John"], "tel": ["555-1234"]})
target_df = pl.DataFrame({"first_name": ["Jane"], "phone": ["555-5678"]})

result = infermap.map(source_df, target_df)
```

For `list[dict]` input, all dicts must have the same keys.

## SchemaInfo

All providers produce a `SchemaInfo` containing `FieldInfo` objects:

```python
@dataclass
class FieldInfo:
    name: str                # column name
    dtype: str               # "string", "integer", "float", "boolean", "date", "datetime"
    sample_values: list[str] # sampled values (stringified)
    null_rate: float         # 0.0-1.0
    unique_rate: float       # 0.0-1.0
    value_count: int         # non-null count
    metadata: dict           # provider-specific extras
```
