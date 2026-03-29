---
layout: default
title: Getting Started
nav_order: 2
---

# Getting Started

## Installation

```bash
pip install infermap
```

### Optional extras

```bash
pip install infermap[postgres]    # PostgreSQL support
pip install infermap[mysql]       # MySQL support
pip install infermap[duckdb]      # DuckDB support
pip install infermap[excel]       # Excel file support
pip install infermap[all]         # Everything
```

## Python API

### Map two files

```python
import infermap

result = infermap.map("source.csv", "target.csv")
```

### Map to a database table

```python
result = infermap.map(
    "incoming_data.csv",
    "postgresql://user:pass@host/db",
    table="customers",
    required=["email", "phone"],
)
```

### Map in-memory DataFrames

```python
import polars as pl

source = pl.DataFrame({"fname": ["John"], "email_addr": ["j@x.com"]})
target = pl.DataFrame({"first_name": ["Jane"], "email": ["j@y.com"]})
result = infermap.map(source, target)
```

### Use a schema definition file

```python
result = infermap.map(
    "source.csv",
    "target.csv",
    schema_file="infermap.target.yaml",
)
```

Schema files add aliases and required fields as extra signals:

```yaml
# infermap.target.yaml
fields:
  - name: email
    type: string
    aliases: [email_address, e_mail, contact_email]
    required: true
  - name: phone
    type: string
    aliases: [telephone, tel, mobile]
```

## Working with results

```python
# Structured report with per-scorer reasoning
report = result.report()

# Remap a DataFrame (Polars or Pandas)
remapped_df = result.apply(source_df)

# Save as reusable YAML config
result.to_config("mapping.yaml")

# JSON output
json_str = result.to_json()
```

## Reuse a saved mapping

```python
result = infermap.from_config("mapping.yaml")
remapped = result.apply(new_source_df)
```

This skips inference entirely -- it applies the saved column renames directly.

## Advanced: custom engine

```python
engine = infermap.MapEngine(
    min_confidence=0.4,
    sample_size=1000,
    config_path="infermap.yaml",
)
result = engine.map(source, target)
```
