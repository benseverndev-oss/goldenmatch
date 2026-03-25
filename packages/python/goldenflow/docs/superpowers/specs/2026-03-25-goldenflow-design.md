# GoldenFlow Design Spec

**Date:** 2026-03-25
**Status:** Approved
**Scope:** Full build — CLI, TUI, REST API, MCP server, domain packs

---

## Overview

GoldenFlow is a data transformation toolkit that standardizes, reshapes, and normalizes messy data before it enters a pipeline. It sits between GoldenCheck (quality scanning) and GoldenMatch (deduplication) in the Golden Suite. Built on Polars for performance, it supports zero-config auto-detection and repeatable YAML-driven transforms.

**Key capabilities:**
- Zero-config mode: auto-detect and fix common data issues
- Schema mapping: align columns between systems
- Transform library: 40+ composable transforms for text, names, phones, addresses, dates, categoricals, numerics
- Audit trail: JSON manifest of every change applied
- Domain packs: pre-configured transform sets for industries (People/HR in v1)

---

## Architecture

### Approach: Monolithic Engine

Single `TransformEngine` class orchestrates profiling, transform selection, and execution. Transforms are registered functions with metadata. The engine calls GoldenCheck's profiler for column analysis, selects applicable transforms, and executes them as a Polars expression chain.

### Package Structure

```
goldenflow/
├── __init__.py              # Public API: transform_file, map_schemas, TransformResult
├── engine/
│   ├── __init__.py
│   ├── transformer.py       # TransformEngine — core orchestrator
│   ├── profiler_bridge.py   # Wraps GoldenCheck's scan_file() for column analysis
│   ├── selector.py          # Auto-selects transforms based on column profiles
│   ├── manifest.py          # Audit trail generation (JSON sidecar)
│   └── differ.py            # Pre/post transform comparison
├── transforms/
│   ├── __init__.py          # Transform registry + @register_transform decorator
│   ├── text.py              # lowercase, uppercase, strip, title_case, etc.
│   ├── names.py             # split_name, strip_titles, name_proper, etc.
│   ├── phone.py             # phone_e164, phone_national, phone_validate
│   ├── address.py           # address_standardize, state_abbreviate, zip_normalize, split_address
│   ├── dates.py             # date_iso8601, date_us, date_eu, age_from_dob
│   ├── categorical.py       # category_standardize, boolean_normalize, null_standardize
│   └── numeric.py           # currency_strip, percentage_normalize, round, clamp
├── mapping/
│   ├── __init__.py
│   ├── schema_mapper.py     # Auto-map columns between schemas
│   ├── name_similarity.py   # Column name matching heuristics
│   └── profile_similarity.py # Match columns by data shape/content
├── config/
│   ├── __init__.py
│   ├── schema.py            # Pydantic models: GoldenFlowConfig, TransformSpec, etc.
│   ├── loader.py            # Load/merge YAML configs
│   └── learner.py           # Generate config from data patterns (goldenflow learn)
├── domains/
│   ├── __init__.py          # Domain registry
│   ├── base.py              # Base class for domain packs
│   └── people_hr.py         # People/HR domain pack (v1)
├── connectors/
│   ├── __init__.py
│   ├── file.py              # CSV, Excel, Parquet, JSON
│   └── database.py          # Same connector pattern as GoldenMatch
├── cli/
│   ├── __init__.py
│   └── main.py              # Typer app: transform, map, learn, validate, diff, etc.
├── tui/
│   ├── __init__.py
│   └── app.py               # Textual app: Profile, Transform, Map, Export tabs
├── api/
│   ├── __init__.py
│   └── server.py            # FastAPI REST server
├── mcp/
│   ├── __init__.py
│   └── server.py            # MCP server for Claude Desktop
└── reporters/
    ├── __init__.py
    ├── json_reporter.py     # Manifest/findings as JSON
    └── rich_console.py      # Rich terminal output
```

---

## Core Engine

### TransformEngine Flow

```
Input (file/DataFrame)
  → Connector (read into Polars DataFrame)
  → Profiler Bridge (call GoldenCheck's scan_file or profile columns)
  → Selector (match column profiles to applicable transforms)
  → Transform Execution (apply Polars expressions, vectorized)
  → Manifest Generation (log every change)
  → Output (write clean file + manifest JSON sidecar)
```

### Transform Registry

Module-level dict mapping transform names to metadata + execution functions.

**Hybrid signature approach:** Transforms that can be expressed as native Polars expressions return `pl.Expr` and stay in the Rust engine (e.g., `strip`, `lowercase`, `collapse_whitespace`). Transforms that require external libraries (e.g., `phonenumbers` for `phone_e164`, `dateutil` for date parsing) use `Series -> Series` via `map_batches` and exit the lazy engine for those columns only.

```python
@register_transform(
    name="phone_e164",
    input_types=["phone"],        # semantic types from GoldenCheck profiling
    auto_apply=True,              # safe for zero-config mode
    priority=50,                  # higher = runs first (range: 0-100, default: 50)
    mode="series",                # "expr" for native Polars, "series" for map_batches
)
def phone_e164(series: pl.Series) -> pl.Series:
    ...

@register_transform(
    name="strip",
    input_types=["string"],
    auto_apply=True,
    priority=90,
    mode="expr",
)
def strip(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars()
```

- `auto_apply=True` — safe for zero-config mode (e.g., `strip`, `normalize_unicode`)
- `auto_apply=False` — only applied via explicit config (e.g., `split_name` when confidence is low)
- `priority` — execution order within a column (higher runs first, range 0-100, default 50)
- `mode` — `"expr"` for native Polars expressions (preferred), `"series"` for transforms requiring Python libraries

**Parameterized transforms:** Transforms may accept parameters via colon-delimited suffixes. The registry parses `name:param1:param2` and passes params to the function. For example, `truncate:100` calls `truncate(series, n=100)`. In YAML configs, `ops: ["round:2", "clamp:0:100"]` is valid.

### Selector Logic

1. GoldenCheck profiles each column → returns `(list[Finding], DatasetProfile)`. The selector uses both: `Finding` objects inform transform selection (e.g., a `format_inconsistency` finding on a phone column triggers `phone_e164`), and `DatasetProfile.columns` provide type/pattern metadata.
2. Selector matches `input_types` from registry against profiled types
3. Confidence threshold (default 0.8) gates auto-application. Note: schema mapping uses a higher threshold (0.9) for auto-apply because column remapping is higher-risk than applying a safe transform.
4. Ambiguous transforms flagged in manifest but not applied
5. Config override: when YAML config provided, bypasses selector entirely
6. Findings from profiling are included in the manifest for traceability

### Error Handling

When a transform fails on a specific value:
- The original value is preserved (no data loss)
- The failure is logged in the manifest with the row index, column, transform name, and error message
- Processing continues to the next row/column
- A summary of failed transforms is shown in CLI output after completion
- Transforms never produce null from a non-null input unless the transform's purpose is null handling (e.g., `null_standardize`)

---

## Transform Library

### Text Transforms
| Transform | What It Does |
|-----------|-------------|
| `lowercase` | Convert to lowercase |
| `uppercase` | Convert to uppercase |
| `strip` | Trim whitespace |
| `title_case` | Proper casing ("john smith" → "John Smith") |
| `normalize_unicode` | NFKD normalization, strip accents optionally |
| `remove_punctuation` | Strip non-alphanumeric characters |
| `collapse_whitespace` | Multiple spaces → single space |
| `truncate:N` | Limit to N characters |

### Name Transforms
| Transform | What It Does |
|-----------|-------------|
| `split_name` | "John Smith" → first: "John", last: "Smith" |
| `split_name_reverse` | "Smith, John" → first: "John", last: "Smith" |
| `strip_titles` | Remove Mr., Mrs., Dr., Jr., Sr., III |
| `strip_suffixes` | Remove professional suffixes (MD, PhD, Esq) |
| `name_proper` | Capitalize properly ("mcdonald" → "McDonald") |
| `initial_expand` | "J. Smith" stays as-is, flagged for review |

### Phone Transforms
| Transform | What It Does |
|-----------|-------------|
| `phone_e164` | Any format → +15550123456 |
| `phone_national` | Any format → (555) 012-3456 |
| `phone_digits` | Strip to digits only |
| `phone_validate` | Flag invalid numbers |

### Address Transforms
| Transform | What It Does |
|-----------|-------------|
| `address_standardize` | "Street" → "St", "Avenue" → "Ave", etc. |
| `address_expand` | "St" → "Street", "Ave" → "Avenue" |
| `state_abbreviate` | "Pennsylvania" → "PA" |
| `state_expand` | "PA" → "Pennsylvania" |
| `zip_normalize` | Zero-pad, strip +4, validate |
| `split_address` | Single line → street, city, state, zip |

### Date Transforms
| Transform | What It Does |
|-----------|-------------|
| `date_iso8601` | Any format → 2024-03-15 |
| `date_us` | Any format → 03/15/2024 |
| `date_eu` | Any format → 15/03/2024 |
| `date_parse` | Auto-detect format, normalize |
| `age_from_dob` | Date of birth → age in years |

### Categorical Transforms
| Transform | What It Does |
|-----------|-------------|
| `category_standardize` | Map variants to canonical values |
| `category_from_file` | Load mapping from CSV/YAML lookup table |
| `boolean_normalize` | "Yes"/"Y"/"1"/"True" → true |
| `gender_standardize` | "M"/"Male"/"m" → "M" |
| `null_standardize` | "N/A"/"NULL"/"none"/"" → null |

### Numeric Transforms
| Transform | What It Does |
|-----------|-------------|
| `currency_strip` | "$1,234.56" → 1234.56 |
| `percentage_normalize` | "85%" → 0.85 |
| `round:N` | Round to N decimal places |
| `clamp:min:max` | Clamp values to range |

---

## Schema Mapping

### Two-Pass Matching

1. **Name similarity** — Jaro-Winkler distance + common alias lookup dict (`fname` → `first_name`, `ph` → `phone`, `addr` → `address`). No ML.
2. **Profile similarity** — When name matching is ambiguous, compare column data profiles (type, cardinality, value distribution, format patterns) from GoldenCheck.

### Confidence Tiers

- **Auto-apply** (≥0.9) — Applied without review
- **Suggest** (0.6–0.9) — Flagged for human review
- **Skip** (<0.6) — No mapping proposed

### Composite Handling

When a source column maps to multiple targets (e.g., `full_name` → `first_name` + `last_name`), the mapper checks if a matching transform exists in the registry (like `split_name`) and chains it.

### Output

Reusable YAML mapping config for `goldenflow map -c` repeat runs.

---

## CLI

Typer app with 9 commands:

| Command | What It Does |
|---------|-------------|
| `transform` | Core — zero-config or config-driven transforms |
| `map` | Schema mapping between source and target |
| `validate` | Dry-run — show what would change without writing |
| `diff` | Compare pre/post transform files |
| `learn` | Auto-generate YAML config from data patterns |
| `profile` | Delegates to GoldenCheck's scan_file |
| `interactive` | Launch Textual TUI |
| `serve` | FastAPI REST server |
| `mcp-serve` | MCP server for Claude Desktop |

Shared flags: `--output-dir`, `--config`, `--domain`, `--format`. Rich console output.

**Stdin/stdout support:** When input path is `-`, reads from stdin (JSON or CSV). When no `--output-dir` is specified and input is stdin, writes to stdout. This enables the pipe chain: `goldencheck scan data.csv | goldenflow transform --from-findings - | goldenmatch dedupe`.

---

## TUI

Textual app with 4 tabs:

- **Profile** — Column types, samples, issues (calls profiler bridge)
- **Transform** — Select columns, pick/reorder transforms, live before/after preview
- **Map** — Schema mapping editor, drag-to-connect or auto-suggest
- **Export** — Save cleaned data, config YAML, manifest JSON

---

## REST API

FastAPI server matching CLI commands:

- `POST /transform` — Upload file or send DataFrame JSON, returns transformed data + manifest
- `POST /map` — Schema mapping
- `GET /transforms` — List available transforms
- `GET /health` — Health check

Same pattern as GoldenMatch's `api/server.py`.

---

## MCP Server

Exposes `transform`, `map`, `profile`, and `learn` as MCP tools for Claude Desktop. Same implementation approach as GoldenCheck/GoldenMatch MCP servers.

---

## Config

### Pydantic Models

```python
class GoldenFlowConfig(BaseModel):
    source: str | None = None
    output: str | None = None
    transforms: list[TransformSpec] = []
    splits: list[SplitSpec] = []
    renames: dict[str, str] = {}
    drop: list[str] = []
    filters: list[FilterSpec] = []
    dedup: DedupSpec | None = None

class TransformSpec(BaseModel):
    column: str
    ops: list[str]

class SplitSpec(BaseModel):
    source: str
    target: list[str]
    method: str

class FilterSpec(BaseModel):
    column: str
    condition: str

class DedupSpec(BaseModel):
    columns: list[str]
    keep: Literal["first", "last"] = "first"
```

Note: `DedupSpec` is a lightweight convenience for exact-match deduplication only (`DataFrame.unique()`). For fuzzy deduplication, use GoldenMatch. This is not a GoldenMatch integration point — it exists to handle trivial exact duplicates before data leaves GoldenFlow.

### YAML Format

Maps directly to the Pydantic models. Loader merges CLI flags → config file → defaults.

### `goldenflow learn`

Profiles the data, runs the selector, serializes selected transforms into a YAML config. Snapshot of what zero-config mode would do, made editable.

---

## Domain Packs

```python
class DomainPack(BaseModel):
    name: str
    transforms: list[str]
    default_config: GoldenFlowConfig
```

### People/HR (v1)
- Name parsing (split, titles, suffixes, proper casing)
- SSN formatting: `ssn_mask` (123-45-6789 → ***-**-6789), `ssn_validate` (flag invalid formats). These are domain-specific transforms registered by the People/HR pack into the main registry at import time.
- Employment date handling
- Gender/boolean standardization

Domain packs register their own transforms into the main registry when imported. This is consistent with the `@register_transform` architecture — the pack module simply calls the decorator at module scope.

Other packs (Healthcare, Finance, E-commerce, Real Estate) stubbed with `NotImplementedError`.

---

## GoldenCheck Integration (Inbound)

- `profiler_bridge.py` wraps `goldencheck.scan_file()` as a Python dependency
- `--from-findings` CLI flag accepts piped JSON findings from `goldencheck scan --format json`
- Finding-to-transform mapping is a dict in the selector module, extensible by domain packs
- If GoldenCheck not installed, falls back to lightweight built-in profiler that supports: type inference (string, numeric, date, boolean), basic pattern detection (email, phone, date formats via regex), null/unique counts, and sample value extraction. The fallback produces objects compatible with GoldenCheck's `ColumnProfile` interface so the selector works identically. Semantic types detected by fallback: `string`, `numeric`, `date`, `boolean`, `email`, `phone`, `name` (heuristic: title-cased strings with spaces). More advanced types (address, SSN, MRN) require GoldenCheck.
- `goldencheck` is an optional dependency: `pip install goldenflow[check]`

## GoldenMatch Integration (Outbound)

- GoldenFlow output is a clean CSV/Parquet that GoldenMatch consumes directly
- Manifest JSON includes column metadata that GoldenMatch can read to inform matchkey selection
- Not a dependency — GoldenFlow just produces files GoldenMatch reads

## Pipe Chain

```bash
goldencheck scan data.csv | goldenflow transform --from-findings | goldenmatch dedupe
```

Works via stdin/stdout JSON streaming.

---

## Dependencies

### Core
- `polars` — DataFrames (Rust-backed, vectorized)
- `pydantic` — Config models and validation
- `typer` — CLI framework
- `rich` — Terminal output
- `textual` — TUI framework
- `fastapi` + `uvicorn` — REST API
- `phonenumbers` — Phone parsing/validation
- `python-dateutil` — Date parsing
- `pyyaml` — Config file loading
- `rapidfuzz` — String similarity for schema mapping

### Optional
- `goldencheck` — Column profiling (`pip install goldenflow[check]`)
- `openpyxl` — Excel file support (`pip install goldenflow[excel]`)
- Database connectors — Same pattern as GoldenMatch

### Python Version
- 3.11+

---

## Performance Target

Built on Polars. Transforms are vectorized — no Python loops in the hot path. String operations use Polars' Rust string kernels. Phone/address parsing uses compiled regex.

| Records | Transform Time | Throughput |
|---------|---------------|-----------|
| 10,000 | 0.2s | 50K rec/s |
| 100,000 | 1.5s | 67K rec/s |
| 1,000,000 | 12s | 83K rec/s |

Targets assume: 10-15 columns, mix of expr-mode and series-mode transforms (strip, phone_e164, date_iso8601, state_abbreviate). Throughput increases with scale due to amortized Polars startup cost. These are goals for initial release, to be validated with benchmarks.
