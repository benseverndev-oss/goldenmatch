# GoldenFlow

**Data transformation toolkit — standardize, reshape, and normalize messy data before it hits your pipeline.**

Works on files (CSV, Excel, Parquet), cloud storage (S3, GCS), or live databases. Zero-config mode auto-detects what needs fixing. One command to clean what GoldenCheck found and prep what GoldenMatch needs.

```bash
pip install goldenflow
goldenflow transform data.csv
```

[![Tests](https://img.shields.io/badge/tests-158%20passing-brightgreen)](https://github.com/benzsevern/goldenflow)
[![DQBench](https://img.shields.io/badge/DQBench-100%2F100-gold)](https://github.com/benzsevern/dqbench)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## The Problem

Your data arrives broken in predictable ways:

- Phone numbers come in 15 different formats
- Dates are mixed between MM/DD/YYYY and YYYY-MM-DD
- Addresses have inconsistent abbreviations
- Column names don't match between systems ("fname" vs "first_name" vs "given_name")
- Values have leading whitespace, unicode garbage, smart quotes
- Categoricals are inconsistent ("USA", "US", "United States")

Every data engineer writes throwaway scripts to fix these. Every script is slightly different. None of them are reusable.

GoldenFlow makes the transforms reusable, composable, and automatic.

---

## Quick Start

```bash
pip install goldenflow

# Auto-transform (zero-config)
goldenflow transform messy_data.csv

# Try the demo first
goldenflow demo
goldenflow transform demo_data.csv -c demo_config.yaml

# With config
goldenflow learn messy_data.csv -o config.yaml
goldenflow transform messy_data.csv -c config.yaml

# Schema mapping
goldenflow map --source system_a.csv --target system_b.csv

# Full pipeline
goldencheck scan data.csv
goldenflow transform data.csv
goldenmatch dedupe data_transformed.csv
```

---

## Zero-Config Mode

```bash
goldenflow transform customers.csv
# or just:
goldenflow customers.csv
```

GoldenFlow profiles every column and applies safe transforms automatically:

- Strips whitespace and normalizes unicode
- Standardizes phone numbers to E.164 format
- Normalizes email casing
- Parses and standardizes date formats to ISO 8601
- Normalizes zip codes (zero-padding, strip +4)
- Replaces smart/curly quotes with straight quotes
- Auto-corrects categorical misspellings via fuzzy matching

Output: a clean CSV with a sidecar manifest showing every transform applied.

```
customers.csv           -> customers_transformed.csv
                        -> customers_manifest.json
```

The manifest is an audit trail — what changed, why, and which rows were affected.

---

## CLI Commands

GoldenFlow has 14 commands. The most common ones:

```bash
# Core transforms
goldenflow transform data.csv                    # Zero-config: auto-detect and fix
goldenflow transform data.csv -c config.yaml     # Apply saved config
goldenflow transform data.csv --domain healthcare # Use a domain pack
goldenflow transform data.csv --strict           # Fail on any transform error
goldenflow transform data.csv --llm              # Enable LLM-enhanced corrections
goldenflow data.csv                              # Shorthand: auto-routes to transform

# Schema & profiling
goldenflow map -s a.csv -t b.csv                 # Auto-map schemas between files
goldenflow profile data.csv                      # Show column profiles
goldenflow learn data.csv -o config.yaml         # Generate config from data patterns
goldenflow validate data.csv                     # Dry-run: show what would change
goldenflow diff before.csv after.csv             # Compare pre/post transform

# Continuous & scheduled
goldenflow watch ./data/                         # Auto-transform new/changed files
goldenflow schedule data.csv --every 1h          # Run on a schedule (5m, 1h, 30s...)
goldenflow stream large_file.csv --chunk-size 50000  # Stream-process in batches

# Discovery & history
goldenflow init data.csv                         # Interactive setup wizard
goldenflow demo                                  # Generate sample data to try
goldenflow history                               # Show recent transform runs
goldenflow history -n 50                         # Last 50 runs

# Integrations
goldenflow interactive data.csv                  # Launch TUI
goldenflow serve                                 # REST API for real-time transforms
goldenflow mcp-serve                             # MCP server for Claude Desktop
```

### Default Routing

Running `goldenflow <file>` without a subcommand auto-routes to `transform`:

```bash
goldenflow customers.csv       # equivalent to: goldenflow transform customers.csv
goldenflow -                   # read from stdin, write to stdout
```

---

## Streaming

For files too large to load into memory, use `StreamProcessor` or the `stream` command:

```bash
goldenflow stream large_file.csv --chunk-size 50000
```

```python
from goldenflow.streaming import StreamProcessor

processor = StreamProcessor(config=config)

# Process a single record
result = processor.transform_one({"name": "  John  ", "phone": "(555) 123-4567"})

# Process a batch
result = processor.transform_batch(df_batch)

# Stream a large file in chunks
for result in processor.stream_file("large_data.csv", chunk_size=10_000):
    write_to_output(result.df)

print(f"Processed {processor.batches_processed} batches")
```

---

## Cloud Connectors

GoldenFlow reads from and writes to S3 and Google Cloud Storage transparently:

```bash
# S3
goldenflow transform s3://my-bucket/raw/customers.csv -o s3://my-bucket/clean/

# GCS
goldenflow transform gs://my-bucket/data/records.csv
```

```python
from goldenflow.connectors.s3 import read_s3, write_s3
from goldenflow.connectors.gcs import read_gcs, write_gcs

df = read_s3("s3://my-bucket/raw/customers.csv")
df = read_gcs("gs://my-bucket/data/records.csv")
```

Cloud paths are detected automatically — no extra flags needed.

---

## Watch Mode

Auto-transform files as they arrive in a directory:

```bash
goldenflow watch ./data/
goldenflow watch ./incoming/ -c config.yaml -o ./processed/
```

GoldenFlow polls the directory and applies transforms to any new or changed files.

---

## Scheduling

Run transforms on a repeating schedule:

```bash
goldenflow schedule data.csv --every 1h
goldenflow schedule data.csv --every 30m -c config.yaml -o ./output/
```

Supported intervals: `30s`, `5m`, `1h`, `2h`, etc.

---

## Setup Wizard

Generate a YAML config interactively:

```bash
goldenflow init data.csv
```

The wizard profiles your data, suggests transforms, and saves a `goldenflow.yaml` ready to use.

---

## History

GoldenFlow tracks every transform run in `~/.goldenflow/history/`:

```bash
goldenflow history         # Last 20 runs
goldenflow history -n 50   # Last 50 runs
```

Each run record captures: source file, row count, transforms applied, errors, and duration.

---

## Schema Mapping

When you need to merge data from different systems:

```bash
goldenflow map --source crm_export.csv --target warehouse_schema.csv
```

GoldenFlow auto-maps columns between schemas using name similarity and data profiling:

```
crm_export.csv              warehouse schema
-----                       -----
email_address      ->       email (rename)
phone_number       ->       phone (rename)
fname              ->       first_name (alias match)
st                 ->       state (alias match)
```

Ambiguous mappings get flagged for human review. Confident mappings apply automatically.

---

## Domain Packs

Pre-configured transform sets for common industries. All 5 are now implemented:

```bash
goldenflow transform patients.csv --domain healthcare
goldenflow transform employees.csv --domain people_hr
goldenflow transform transactions.csv --domain finance
goldenflow transform orders.csv --domain ecommerce
goldenflow transform listings.csv --domain real_estate
```

| Domain Pack | What It Covers |
|-------------|---------------|
| **People/HR** | Name parsing, SSN formatting, employment dates, gender/boolean standardization |
| **Healthcare** | Patient IDs, diagnosis codes, clinical dates, HIPAA-sensitive field handling |
| **Finance** | Currency normalization, account numbers, transaction dates, amount parsing |
| **E-commerce** | SKU normalization, price parsing, order dates, address standardization |
| **Real Estate** | Property addresses, listing dates, price normalization, geo fields |

---

## Transform Library (43+ transforms)

### Text Transforms
| Transform | What It Does |
|-----------|-------------|
| `strip` | Trim whitespace |
| `lowercase` / `uppercase` | Case conversion |
| `title_case` | Proper casing ("john smith" -> "John Smith") |
| `normalize_unicode` | NFKD normalization, strip accents |
| `normalize_quotes` | Smart/curly quotes -> straight quotes |
| `collapse_whitespace` | Multiple spaces -> single space |
| `truncate:N` | Limit to N characters |

### Phone Transforms
| Transform | What It Does |
|-----------|-------------|
| `phone_e164` | Any format -> +15550123456 |
| `phone_national` | Any format -> (555) 012-3456 |
| `phone_digits` | Strip to digits only |
| `phone_validate` | Flag invalid numbers |

### Name Transforms
| Transform | What It Does |
|-----------|-------------|
| `split_name` | "John Smith" -> first: "John", last: "Smith" |
| `split_name_reverse` | "Smith, John" -> first: "John", last: "Smith" |
| `strip_titles` | Remove Mr., Mrs., Dr., Jr., Sr. |
| `name_proper` | "mcdonald" -> "McDonald" |

### Address Transforms
| Transform | What It Does |
|-----------|-------------|
| `address_standardize` | "Street" -> "St", "Avenue" -> "Ave" |
| `state_abbreviate` | "Pennsylvania" -> "PA" |
| `zip_normalize` | Zero-pad, strip +4, validate |
| `split_address` | Single line -> street, city, state, zip |

### Date Transforms
| Transform | What It Does |
|-----------|-------------|
| `date_iso8601` | Any format -> 2024-03-15 |
| `date_us` / `date_eu` | Regional format output |
| `age_from_dob` | Date of birth -> age in years |

### Categorical Transforms
| Transform | What It Does |
|-----------|-------------|
| `category_auto_correct` | Fuzzy-match misspellings to canonical values |
| `category_standardize` | Map variants to canonical values |
| `boolean_normalize` | "Yes"/"Y"/"1"/"True" -> true |
| `null_standardize` | "N/A"/"NULL"/"none" -> null |

### Numeric Transforms
| Transform | What It Does |
|-----------|-------------|
| `currency_strip` | "$1,234.56" -> 1234.56 |
| `percentage_normalize` | "85%" -> 0.85 |
| `round:N` | Round to N decimal places |

---

## Special Modes

### Strict Mode

Fail immediately if any transform error occurs — useful in CI or production pipelines:

```bash
goldenflow transform data.csv --strict
```

Exits with code 1 and prints the first 5 errors if any transform fails.

### LLM Mode

Use an LLM to enhance categorical corrections and handle edge cases that fuzzy matching misses:

```bash
goldenflow transform data.csv --llm
```

Requires `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in your environment. Falls back to standard transforms gracefully.

### Auto-Correct

`category_auto_correct` uses fuzzy matching to fix misspelled categorical values automatically. It is suppressed on high-cardinality columns (>10% unique values) to avoid false positives.

```
"actve" -> "active"
"Pennsylvnia" -> "Pennsylvania"
"Unted States" -> "United States"
```

---

## YAML Config

For repeatable pipelines:

```yaml
# goldenflow.yaml
source: customers.csv
output: customers_clean.csv

transforms:
  - column: name
    ops: [strip, title_case]
  - column: email
    ops: [lowercase, strip]
  - column: phone
    ops: [phone_e164]
  - column: state
    ops: [state_abbreviate]
  - column: signup_date
    ops: [date_iso8601]

renames:
  email_address: email
  phone_number: phone

drop: [internal_id, temp_notes]

dedup:
  columns: [email]
  keep: first
```

```bash
goldenflow transform customers.csv -c goldenflow.yaml
```

Generate a config from your data automatically:

```bash
goldenflow learn data.csv -o config.yaml
```

---

## Python API

```python
import goldenflow

# Zero-config
result = goldenflow.transform_file("messy_data.csv")
print(result.df)          # Clean Polars DataFrame
print(result.manifest)    # Audit trail

# With config
from goldenflow import GoldenFlowConfig, TransformSpec, TransformEngine

config = GoldenFlowConfig(
    transforms=[
        TransformSpec(column="phone", ops=["phone_e164"]),
        TransformSpec(column="date", ops=["date_iso8601"]),
    ]
)
engine = TransformEngine(config=config)
result = engine.transform_df(df)
```

### Jupyter Notebook Support

`TransformResult`, `Manifest`, and `DatasetProfile` all have `_repr_html_()` — they render as rich HTML tables automatically in Jupyter:

```python
import goldenflow

result = goldenflow.transform_file("messy_data.csv")
result           # renders as HTML table in Jupyter
result.manifest  # renders transform audit trail
```

---

## Public API (34 exports)

```python
from goldenflow import (
    # Core engine
    TransformEngine, TransformResult,
    # Config
    GoldenFlowConfig, TransformSpec, SplitSpec, FilterSpec, DedupSpec, MappingSpec,
    # Convenience
    transform_file, transform_df,
    # Manifest
    Manifest, TransformRecord, TransformError,
    # Profiler
    DatasetProfile, ColumnProfile,
    # Selector & differ
    select_transforms, diff_dataframes, DiffResult,
    # Transform registry
    TransformInfo, register_transform, get_transform, list_transforms, parse_transform_name,
    # Mapping
    SchemaMapper, ColumnMapping,
    # Config helpers
    load_config, save_config, merge_configs, learn_config,
    # Domains
    DomainPack, load_domain,
    # Connectors
    read_file, write_file,
)
```

---

## Integrations

### REST API

```bash
goldenflow serve --host 0.0.0.0 --port 8000
```

POST CSV data, get transformed CSV back. Built with FastAPI.

### MCP Server

```bash
goldenflow mcp-serve
```

Exposes GoldenFlow as an MCP tool for Claude Desktop. Configure in your Claude Desktop settings.

### TUI

```bash
goldenflow interactive data.csv
```

Full-featured terminal UI built with Textual. Browse profiles, apply transforms, preview results.

---

## Integration with the Golden Suite

```
Raw Data
   |
   v
+--------------+
|  GoldenCheck |  <- Discover quality issues
|  goldencheck |
|  scan data   |
+------+-------+
       | findings
       v
+--------------+
|  GoldenFlow  |  <- Fix issues, standardize, reshape
|  goldenflow  |
|  transform   |
+------+-------+
       | clean data
       v
+--------------+
|  GoldenMatch |  <- Deduplicate, match, create golden records
|  goldenmatch |
|  dedupe      |
+------+-------+
       | golden records
       v
   Clean, deduplicated,
   production-ready data
```

Chain them:

```bash
goldencheck scan data.csv | goldenflow transform --from-findings | goldenmatch dedupe
```

---

## Performance

Built on [Polars](https://pola.rs/) (Rust-backed DataFrames). Transforms use a hybrid approach: native Polars expressions stay in the Rust engine for simple transforms (strip, lowercase), while complex transforms (phone parsing, date parsing) use optimized Python via `map_batches`.

---

## Benchmarks

GoldenFlow scores **100/100** on the [DQBench](https://github.com/benzsevern/dqbench) transform benchmark across all three tiers (customer database, e-commerce, healthcare claims).

```bash
pip install dqbench
dqbench run goldenflow
```

---

## Why GoldenFlow?

| | GoldenFlow | pandas scripts | [Great Expectations](https://greatexpectations.io/) | [dbt](https://www.getdbt.com/) | [Dataprep.Clean](https://docs.dataprep.ai/user_guide/clean/) |
|---|---|---|---|---|---|
| Zero-config transforms | Yes (auto-detect) | No | No (validation only) | No (SQL transforms) | Partial |
| 43+ built-in transforms | Yes | Manual | No (validator, not transformer) | Via SQL | ~30 cleaners |
| Domain packs (healthcare, finance...) | 5 built-in | No | No | No | No |
| Schema mapping | Auto + manual | Manual | No | Via ref/source | No |
| Audit trail (manifest) | Automatic JSON | Manual | No | Via logs | No |
| Streaming / large files | Built-in | Manual chunking | No | Yes (warehouse) | No |
| MCP server | Yes | No | No | No | No |
| Polars-native | Yes | No (pandas) | No (pandas/Spark) | No (SQL) | No (pandas) |
| DQBench transform score | 100/100 | N/A | N/A | N/A | N/A |

GoldenFlow is purpose-built for the transform step between validation and matching — not a general ETL tool. It turns messy data into clean, standardized data automatically.

---

## Error Handling

GoldenFlow catches errors at the CLI boundary and shows friendly, actionable messages — no raw stack traces. Individual transform errors are captured in the manifest rather than crashing the run. Use `--strict` to change this behavior.

---

## Progress Bars

Long-running operations (streaming, watch mode, scheduling) display a Rich progress spinner showing batch count, rows processed, and estimated completion.

---

**GitHub:** [github.com/benzsevern/goldenflow](https://github.com/benzsevern/goldenflow)
**License:** MIT
**Python:** 3.11+
