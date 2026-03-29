[![PyPI](https://img.shields.io/pypi/v/infermap?color=d4a017)](https://pypi.org/project/infermap/)
[![CI](https://github.com/benzsevern/infermap/actions/workflows/test.yml/badge.svg)](https://github.com/benzsevern/infermap/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

# infermap

Inference-driven schema mapping engine — automatically maps source fields to target fields using a composable scorer pipeline.

## Install

```bash
pip install infermap
```

Install extras for additional database support:

```bash
pip install infermap[postgres]   # psycopg2-binary
pip install infermap[mysql]      # mysql-connector-python
pip install infermap[duckdb]     # duckdb
pip install infermap[all]        # all extras
```

## Quick Start

```python
import infermap

# Map a CRM export CSV to a canonical customer schema
result = infermap.map("crm_export.csv", "canonical_customers.csv")

for m in result.mappings:
    print(f"{m.source} -> {m.target}  ({m.confidence:.0%})")
# fname -> first_name  (97%)
# lname -> last_name   (95%)
# email_addr -> email  (91%)

# Apply mappings to rename DataFrame columns
import polars as pl
df = pl.read_csv("crm_export.csv")
renamed = result.apply(df)

# Save mappings to a reusable config file
result.to_config("my_mapping.yaml")

# Reload later — no re-inference needed
saved = infermap.from_config("my_mapping.yaml")
```

## CLI Examples

```bash
# Map two files and print a report
infermap map crm_export.csv canonical_customers.csv

# Map and save the config
infermap map crm_export.csv canonical_customers.csv --save mapping.yaml

# Apply a saved mapping config to a DataFrame (prints renamed column list)
infermap apply crm_export.csv mapping.yaml

# Inspect the schema of a file or database table
infermap inspect crm_export.csv
infermap inspect sqlite:///mydb.db --table customers

# Validate a mapping config file
infermap validate mapping.yaml
```

## How It Works

infermap runs each field pair through a pipeline of **5 scorers**. Each scorer returns a score between 0.0 and 1.0 (or abstains with `None`). The engine combines scores via weighted average (requiring at least 2 contributing scorers), then uses the Hungarian algorithm for optimal one-to-one assignment.

| Scorer | Weight | What it detects |
|---|---|---|
| **ExactScorer** | 1.0 | Case-insensitive exact name match |
| **AliasScorer** | 0.9 | Known field aliases (e.g. `fname` == `first_name`, `tel` == `phone`) |
| **PatternTypeScorer** | 0.7 | Semantic type from sample values — email, date_iso, phone, uuid, url, zip, currency |
| **ProfileScorer** | 0.6 | Statistical profile similarity — null rate, unique rate, value count |
| **FuzzyNameScorer** | 0.5 | Token-level fuzzy string similarity on field names |

## Features

- Maps CSV, Parquet, XLSX, Polars DataFrames, Pandas DataFrames, SQLite, and schema YAML files
- Composable scorer pipeline — disable, reweight, or add custom scorers via config or code
- Optimal one-to-one assignment via the Hungarian algorithm
- `required` parameter warns when critical target fields go unmapped
- `MapResult.apply()` renames DataFrame columns in one call
- `to_config()` / `from_config()` roundtrip for repeatable pipelines
- CLI for quick inspection, mapping, and validation

## Custom Scorers

Register a scorer function with the `@infermap.scorer` decorator:

```python
import infermap
from infermap.types import FieldInfo, ScorerResult

@infermap.scorer("my_prefix_scorer", weight=0.8)
def my_prefix_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
    src = source.name.lower()
    tgt = target.name.lower()
    # Abstain if neither name starts with a common prefix
    if not (src[:3] == tgt[:3]):
        return None
    return ScorerResult(score=0.85, reasoning=f"Shared prefix '{src[:3]}'")

from infermap.engine import MapEngine
from infermap.scorers import default_scorers

engine = MapEngine(scorers=[*default_scorers(), my_prefix_scorer])
result = engine.map("source.csv", "target.csv")
```

You can also use a plain class with `name`, `weight`, and `score()`:

```python
class DomainScorer:
    name = "DomainScorer"
    weight = 0.75

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        ...
```

## Config Reference

Load an `infermap.yaml` at engine creation to override scorer weights, disable scorers, or add domain aliases:

```python
engine = MapEngine(config_path="infermap.yaml")
```

See `infermap.yaml.example` for a full annotated example.

## License

MIT
