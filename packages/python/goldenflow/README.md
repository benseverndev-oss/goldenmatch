# GoldenFlow

**Data transformation toolkit — standardize, reshape, and normalize messy data before it hits your pipeline.**

Works on files (CSV, Excel, Parquet) or live databases. Zero-config mode auto-detects what needs fixing. One command to clean what GoldenCheck found and prep what GoldenMatch needs.

```bash
pip install goldenflow
goldenflow transform data.csv
```

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

## Zero-Config Mode

```bash
goldenflow transform customers.csv
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

## Transform Library

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

---

## CLI Commands

```bash
goldenflow transform data.csv              # Zero-config: auto-detect and fix
goldenflow transform data.csv -c config.yaml  # Apply saved config
goldenflow map -s a.csv -t b.csv           # Auto-map schemas
goldenflow learn data.csv -o config.yaml   # Generate a config from data patterns
goldenflow validate data.csv               # Dry-run: show what would change
goldenflow diff before.csv after.csv       # Compare pre/post transform
goldenflow profile data.csv               # Show column profiles
goldenflow interactive data.csv           # Launch TUI
goldenflow serve                          # REST API for real-time transforms
goldenflow mcp-serve                      # MCP server for Claude Desktop
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

Or chain them:

```bash
goldencheck scan data.csv | goldenflow transform --from-findings | goldenmatch dedupe
```

---

## Domain Packs

Pre-configured transform sets for common industries:

```bash
goldenflow transform patients.csv --domain people_hr
```

**People/HR** (v1): Name parsing, SSN formatting, employment dates, gender/boolean standardization.

Healthcare, Finance, E-commerce, Real Estate packs coming soon.

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

## Quick Start

```bash
pip install goldenflow

# Auto-transform
goldenflow transform messy_data.csv

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

**GitHub:** [github.com/benzsevern/goldenflow](https://github.com/benzsevern/goldenflow)
**License:** MIT
**Python:** 3.11+
