# infermap — Design Specification

**Date:** 2026-03-29
**Status:** Draft
**Author:** Ben Severn

---

## Overview

`infermap` is an open-source, inference-driven schema mapping engine. It maps messy source columns to a target schema using a weighted scorer pipeline — running multiple independent scoring strategies in parallel, combining their confidence-weighted signals, and applying optimal assignment to produce the best global mapping.

The goal is to make schema mapping more accurate, explainable, and backend-friendly than manual workflows or black-box ETL guesses. It ships as a Python library and CLI first, with a TypeScript SDK planned for v1.1.

### Design Principles

- **Inference by default** — point it at two schemas and it figures out the mapping. No configuration required for the common case.
- **Transparent reasoning** — every mapping includes per-scorer breakdowns explaining why it was chosen.
- **Backend-first** — designed to be embedded in data ingestion pipelines, not just used interactively.
- **Pluggable** — custom scorers and providers slot in without modifying core code.
- **Resilient to drift** — DB introspection mode means the system adapts when the target schema gains new fields.

### Non-Goals

- Value transformation (renaming columns, not cleaning values — use GoldenFlow or similar).
- Record matching or deduplication (use GoldenMatch or similar).
- Schema migration or DDL generation.
- Composite column detection (e.g., `full_name` -> `first_name` + `last_name`) — planned for a future release.

### Known Limitations (v1)

- **1:1 mappings only** — each source field maps to at most one target field and vice versa. Composite/split column mappings (one source field expanding to multiple targets or multiple sources merging into one target) are not supported in v1. This is a deliberate scope constraint for the optimal assignment algorithm.

---

## Architecture

Four layers, each independently testable:

```
+---------------------------------------------------+
|  Consumer Layer (CLI, Python API, TS SDK)          |
+---------------------------------------------------+
|  Orchestrator (MapEngine)                          |
|  - collects schema info from sources               |
|  - runs scorer pipeline on all (src, tgt) pairs    |
|  - applies optimal assignment                      |
|  - produces MapResult                              |
+---------------------------------------------------+
|  Scorer Pipeline (pluggable)                       |
|  - ExactScorer, AliasScorer, PatternTypeScorer,    |
|    ProfileScorer, FuzzyNameScorer, LLMScorer       |
|  - each returns (score, reasoning) per pair        |
|  - user can register custom scorers                |
+---------------------------------------------------+
|  Schema Providers (pluggable)                      |
|  - FileProvider (CSV, Parquet, Excel)              |
|  - DBProvider (Postgres, MySQL, SQLite, DuckDB)    |
|  - SchemaFileProvider (YAML/JSON definition)       |
|  - InMemoryProvider (DataFrame / dict)             |
+---------------------------------------------------+
```

### Data Flow

```
Source (file/DB/dict) --> SchemaProvider.extract() --> SchemaInfo
Target (file/DB/dict) --> SchemaProvider.extract() --> SchemaInfo
                                                        |
                                                        v
MapEngine.map(source_info, target_info)
  -> for each (src_field, tgt_field) pair:
       -> run all scorers -> weighted sum -> score matrix
  -> scipy.linear_sum_assignment(cost_matrix)
  -> MapResult
       .report()     -> structured dict/JSON
       .apply(df)    -> remapped DataFrame
       .to_config()  -> YAML file
```

---

## Core Types

### FieldInfo

Normalized representation of a single field, produced by any provider:

```python
@dataclass
class FieldInfo:
    name: str                    # original column name
    dtype: str                   # normalized: "string", "integer", "float", "boolean", "date", "datetime"
    sample_values: list[str]     # up to N sampled values (stringified for portability across providers and JSON serialization)
    null_rate: float             # 0.0-1.0
    unique_rate: float           # 0.0-1.0
    value_count: int             # total non-null values
    metadata: dict               # provider-specific extras (DB constraints, schema file annotations)
```

### SchemaInfo

A complete schema extracted from any source:

```python
@dataclass
class SchemaInfo:
    fields: list[FieldInfo]
    source_name: str             # e.g., "customers.csv", "public.users"
    required_fields: list[str]   # fields the caller marked as required (union of all sources: map() param, CLI --required flag, schema file required: true)
```

### ScorerResult

Output from a single scorer for a single field pair:

```python
@dataclass
class ScorerResult:
    score: float        # 0.0-1.0
    reasoning: str      # human-readable explanation
```

### FieldMapping

A single source-to-target mapping with full audit trail:

```python
@dataclass
class FieldMapping:
    source: str                          # source field name
    target: str                          # target field name
    confidence: float                    # 0.0-1.0 combined score
    breakdown: dict[str, ScorerResult]   # per-scorer results
    reasoning: str                       # human-readable summary
```

### MapResult

The complete output of a mapping operation:

```python
@dataclass
class MapResult:
    mappings: list[FieldMapping]
    unmapped_source: list[str]
    unmapped_target: list[str]
    warnings: list[str]                  # e.g., "required field 'email' has no match"
    metadata: dict                       # timing, scorer config, sample sizes

    def report(self) -> dict: ...
    def apply(self, df: pl.DataFrame | pd.DataFrame) -> pl.DataFrame | pd.DataFrame: ...
    def to_config(self, path: str) -> None: ...
    def to_json(self) -> str: ...
```

---

## Schema Providers

Each provider normalizes any source into a `SchemaInfo`. Auto-detection based on input type:

- String ending in `.csv`/`.parquet`/`.xlsx` -> `FileProvider`
- String starting with `postgresql://`/`mysql://`/`sqlite://`/`duckdb://` -> `DBProvider`
- String ending in `.yaml`/`.json` -> `SchemaFileProvider` (validated: must contain a `fields` key conforming to the schema file format; raises `ConfigError` if the file doesn't match the expected structure)
- Polars/Pandas DataFrame -> `InMemoryProvider`

### Provider Protocol

```python
class Provider(Protocol):
    def extract(self, source: Any, **kwargs) -> SchemaInfo: ...
```

### FileProvider

Reads CSV, Parquet, Excel via Polars. Samples up to N rows (default 500). Infers types from Polars dtypes, converts to normalized type strings.

### DBProvider

Connects via dialect-specific drivers. Reads column metadata from system catalogs, samples rows via SQL.

| DB         | Type query                      | Sample query                                |
|------------|---------------------------------|---------------------------------------------|
| PostgreSQL | `information_schema.columns`    | `SELECT * FROM table TABLESAMPLE SYSTEM(n)` |
| MySQL      | `information_schema.columns`    | `SELECT * FROM table LIMIT n`               |
| SQLite     | `pragma_table_info(table)`      | `SELECT * FROM table LIMIT n`               |
| DuckDB     | `information_schema.columns`    | `SELECT * FROM table USING SAMPLE n`        |

Connection strings follow standard URI format: `dialect://user:pass@host:port/dbname`.

The `table` parameter is required. Alternatively, a raw SQL query can be provided for sampling from views or joins.

### SchemaFileProvider

Parses a YAML/JSON definition file. Provides explicit field metadata including aliases, types, and required flags. When used alongside another provider, its aliases and type declarations feed into the scorers as stronger priors — they augment the pipeline rather than bypassing it.

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
    required: false
  - name: zip_code
    type: string
    aliases: [postal, postcode, zip]
```

### InMemoryProvider

Accepts Polars DataFrame, Pandas DataFrame, or `list[dict]`. Profiles directly in memory — same logic as FileProvider without I/O. For `list[dict]` input, all dicts must have the same keys; nested values are stringified.

### Sample Size

Default 500 rows, configurable via `sample_size` parameter. Enough for type detection and profile stats without being expensive on large tables.

---

## Scorer Pipeline

Each scorer implements a simple protocol and contributes one signal to the overall score matrix. Scorers are independent — they don't know about each other.

### Scorer Protocol

```python
class Scorer(Protocol):
    name: str
    weight: float

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None: ...
    # Return None to abstain (no signal available).
    # Return ScorerResult(0.0, ...) to signal "evaluated, no match."
```

### Built-in Scorers (v1)

| Scorer             | Weight | What it does                                                                                      |
|--------------------|--------|---------------------------------------------------------------------------------------------------|
| `ExactScorer`      | 1.0    | Case-insensitive exact name match. Returns 1.0 or 0.0.                                           |
| `AliasScorer`      | 0.95   | Checks source name against target's aliases (schema file or built-in registry). Returns 0.95/0.0. Returns `None` if neither field has known aliases. |
| `PatternTypeScorer`| 0.7    | Regex/heuristic classification of sampled values into semantic types. Compares inferred types. Returns `None` if no samples available. Score = `min(source_match_pct, target_match_pct)` when both classify to the same type; 0.0 when types differ. |
| `ProfileScorer`    | 0.5    | Compares statistical profiles: dtype, null rate, uniqueness, value length, cardinality. Returns `None` if no samples available on either side. |
| `FuzzyNameScorer`  | 0.4    | RapidFuzz Jaro-Winkler on normalized column names. Low weight prevents false positives.            |
| `LLMScorer`        | 0.8    | v1.1. Sends field name + samples to LLM for classification. High weight, high accuracy. Not included in v1 default scorers. See v1.1 section for details. |

### Built-in Alias Registry

The AliasScorer ships with a default alias registry covering common field name variations. Users can extend it via schema definition files or YAML config. Representative sample:

```python
ALIASES = {
    "first_name": ["fname", "first", "given_name", "first_nm", "forename"],
    "last_name": ["lname", "last", "surname", "family_name", "last_nm"],
    "email": ["email_address", "e_mail", "email_addr", "mail", "contact_email"],
    "phone": ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
    "address": ["addr", "street_address", "addr_line_1", "address_line_1", "mailing_address"],
    "city": ["town", "municipality"],
    "state": ["st", "province", "region"],
    "zip": ["zipcode", "zip_code", "postal_code", "postal", "postcode"],
    "name": ["full_name", "fullname", "customer_name", "display_name", "contact_name"],
    "company": ["organization", "org", "business", "employer", "firm", "company_name"],
    "dob": ["date_of_birth", "birth_date", "birthdate", "birthday"],
    "country": ["nation", "country_code"],
    "gender": ["sex"],
    "id": ["identifier", "record_id", "uid"],
    "created_at": ["signup_date", "create_date", "date_created"],
}
```

Custom aliases via config:

```yaml
# infermap.yaml
aliases:
  mrn: [medical_record_number, patient_id, chart_number]
  npi: [provider_id, national_provider_identifier]
```

### Score Combination

For each `(source, target)` pair:

```
combined_score = sum(scorer.weight * result.score for all enabled scorers)
               / sum(scorer.weight for all enabled scorers)
```

All enabled scorers contribute to the denominator, whether they returned 0.0 or not. A score of 0.0 means "I evaluated this pair and found no match" — it is a genuine negative signal, not an abstention. This ensures scores are comparable across pairs regardless of which scorers fire.

If a scorer cannot evaluate a pair at all (e.g., PatternTypeScorer when all sample values are null), it should return `None` instead of `ScorerResult`. The engine excludes `None`-returning scorers from both numerator and denominator for that pair. The `ScorerResult` type is therefore `ScorerResult | None` as the return type of `score()`.

**Minimum contributor threshold:** A pair must have at least 2 non-None scorer contributions to receive a combined score. Pairs with fewer contributors are assigned a score of 0.0 to prevent a single weak signal from producing an inflated match.

### PatternTypeScorer — Semantic Type Registry

```python
SEMANTIC_TYPES = {
    "email":    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$",
    "phone":    r"^[\+]?[\d\s\-\(\)]{7,15}$",
    "zip_us":   r"^\d{5}(-\d{4})?$",
    "date_iso": r"^\d{4}-\d{2}-\d{2}",
    "uuid":     r"^[0-9a-f]{8}-[0-9a-f]{4}-",
    "url":      r"^https?://",
    "currency": r"^[\$\u20ac\u00a3\u00a5]\s?\d",
    "ip_v4":    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",
    # extensible via config or plugin
}
```

A field is classified by the semantic type where the highest percentage of sampled values match. Classification threshold: 60% of samples must match (configurable). The scorer compares: does the source's inferred type match the target's inferred type?

### Plugin Registration

```python
import infermap

@infermap.scorer(name="fhir_type", weight=0.8)
def fhir_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult:
    """Domain-specific scorer for FHIR healthcare fields."""
    # custom logic
    return ScorerResult(score=0.9, reasoning="Both fields match FHIR Patient.name pattern")
```

Registered scorers are automatically included in the pipeline.

### Scorer Configuration

Users can disable built-in scorers or override weights via YAML config:

```yaml
# infermap.yaml
scorers:
  FuzzyNameScorer:
    weight: 0.2          # downweight fuzzy for this use case
  LLMScorer:
    enabled: false
  fhir_type:
    weight: 0.9          # boost custom scorer
```

---

## Assignment

Once all scorers have run, the engine has an `M x N` score matrix (M source fields, N target fields).

### Optimal Assignment

```python
cost_matrix = 1.0 - score_matrix
row_indices, col_indices = scipy.optimize.linear_sum_assignment(cost_matrix)
```

### Post-Assignment Filtering

- Pairs where `combined_score < min_confidence` (default 0.3) are dropped as noise.
- Target fields marked `required` that have no mapping above threshold generate warnings.
- Unmatched source fields go into `MapResult.unmapped_source`.
- Unmatched target fields go into `MapResult.unmapped_target`.

The M != N case is handled naturally — extra source or target fields are simply unmapped.

---

## Output Modes

### report() — Structured Dict

```json
{
  "mappings": [
    {
      "source": "tel",
      "target": "phone",
      "confidence": 0.92,
      "breakdown": {
        "AliasScorer": {"score": 0.95, "reasoning": "tel is a known alias for phone"},
        "PatternTypeScorer": {"score": 0.88, "reasoning": "82% of values match phone pattern"},
        "ProfileScorer": {"score": 0.71, "reasoning": "similar cardinality and null rate"}
      },
      "reasoning": "High-confidence match: alias hit + phone pattern detected"
    }
  ],
  "unmapped_source": ["internal_ref"],
  "unmapped_target": [],
  "warnings": []
}
```

### apply(df) — Remapped DataFrame

Takes a source DataFrame, returns it with columns renamed to target names. Only renames mapped columns; unmapped source columns are preserved as-is. Does not do value transformation — out of scope.

**Return type preservation:** `apply()` returns the same DataFrame type as the input. Pass a Polars DataFrame, get a Polars DataFrame. Pass a Pandas DataFrame, get a Pandas DataFrame.

### to_config(path) — Saveable YAML

```yaml
# Generated by infermap v0.1.0
version: "1"
mappings:
  - source: tel
    target: phone
    confidence: 0.92
  - source: email_addr
    target: email
    confidence: 0.97
unmapped_source:
  - internal_ref
unmapped_target: []
```

Reloading skips inference entirely:

```python
result = infermap.from_config("infermap_mapping.yaml")
remapped_df = result.apply(source_df)
```

---

## Python API

```python
import infermap

# Simplest case: two files
result = infermap.map("source.csv", "target.csv")

# DB target (resilient to schema drift)
result = infermap.map(
    "incoming_data.csv",
    "postgresql://host/db?table=customers",
    required=["email", "phone"],
)

# Two-sided alignment
result = infermap.map("system_a.csv", "system_b.csv")

# In-memory DataFrames
result = infermap.map(source_df, target_df)

# With schema definition file for fine-tuning
result = infermap.map(
    "source.csv",
    "target.csv",
    schema_file="infermap.target.yaml",
)

# Reapply saved config (no inference)
result = infermap.from_config("infermap_mapping.yaml")
remapped = result.apply(source_df)
```

### Advanced Usage

```python
engine = infermap.MapEngine(
    min_confidence=0.4,
    sample_size=1000,
    scorers=infermap.default_scorers() + [my_custom_scorer],
    config_path="infermap.yaml",
)
result = engine.map(source, target)
```

### Auto-Detection

The `map()` function auto-detects the provider based on input type:

- String ending in `.csv`/`.parquet`/`.xlsx` -> `FileProvider`
- String starting with `postgresql://`/`mysql://`/`sqlite://`/`duckdb://` -> `DBProvider`
- String ending in `.yaml`/`.json` -> `SchemaFileProvider`
- Polars/Pandas DataFrame -> `InMemoryProvider`

---

## CLI

```bash
# Basic mapping
infermap map source.csv target.csv

# Map to a DB table
infermap map incoming.csv "postgresql://host/db" --table customers

# With required fields
infermap map source.csv target.csv --required email,phone

# Output formats
infermap map source.csv target.csv --format json
infermap map source.csv target.csv --format yaml --output mapping.yaml

# Apply a saved mapping
infermap apply source.csv --config mapping.yaml --output remapped.csv

# Inspect a schema (useful for debugging)
infermap inspect source.csv
infermap inspect "postgresql://host/db" --table customers

# Validate: does source satisfy target schema?
infermap validate source.csv --config mapping.yaml --strict
```

`infermap validate --strict` exits code 1 if required target fields have no mapping above threshold. Useful as a CI gate.

---

## TypeScript SDK (v1.1)

Thin wrapper over the Python engine. Two integration modes:

### Subprocess Mode (default)

Shells out to `infermap` CLI, parses JSON output:

```typescript
import { InferMap } from 'infermap';

const mapper = new InferMap();
const result = await mapper.map('source.csv', 'target.csv');
```

### HTTP Mode (for hosted backends)

Talks to `infermap serve` over REST:

```bash
infermap serve --port 8400
```

```typescript
const mapper = new InferMap({ endpoint: 'http://localhost:8400' });
const result = await mapper.map(sourceRows, targetRows);
```

Published as `infermap` on npm. Ships TypeScript types mirroring the Python types. JSON is the interchange format.

---

## Project Structure

```
infermap/
├── infermap/                    # Python package
│   ├── __init__.py              # public API: map(), from_config(), MapEngine
│   ├── engine.py                # MapEngine orchestrator
│   ├── types.py                 # SchemaInfo, FieldInfo, FieldMapping, MapResult, ScorerResult
│   ├── assignment.py            # score matrix -> optimal assignment via Hungarian
│   ├── scorers/
│   │   ├── __init__.py          # default_scorers(), scorer decorator
│   │   ├── base.py              # Scorer protocol
│   │   ├── exact.py             # ExactScorer
│   │   ├── alias.py             # AliasScorer + built-in alias registry
│   │   ├── pattern_type.py      # PatternTypeScorer + semantic type registry
│   │   ├── profile.py           # ProfileScorer
│   │   ├── fuzzy_name.py        # FuzzyNameScorer
│   │   └── llm.py               # LLMScorer (optional)
│   ├── providers/
│   │   ├── __init__.py          # auto-detect provider from input
│   │   ├── base.py              # Provider protocol
│   │   ├── file.py              # FileProvider (CSV, Parquet, Excel)
│   │   ├── db.py                # DBProvider (Postgres, MySQL, SQLite, DuckDB)
│   │   ├── schema_file.py       # SchemaFileProvider (YAML/JSON)
│   │   └── memory.py            # InMemoryProvider (DataFrame/dict)
│   ├── config.py                # YAML config loading, scorer weight overrides
│   └── cli.py                   # Typer CLI
├── tests/
│   ├── test_scorers/            # one test file per scorer
│   ├── test_providers/          # one test file per provider
│   ├── test_engine.py           # integration tests
│   ├── test_assignment.py       # optimal assignment edge cases
│   ├── test_cli.py              # CLI smoke tests
│   └── conftest.py              # shared fixtures
├── sdk/                         # TypeScript SDK (v1.1)
│   ├── src/
│   │   ├── index.ts
│   │   ├── client.ts            # subprocess + HTTP modes
│   │   └── types.ts             # mirrored from Python types
│   ├── package.json
│   └── tsconfig.json
├── pyproject.toml
├── infermap.yaml.example        # example config
├── LICENSE                      # MIT
└── README.md
```

---

## Dependencies

### Core (always installed)

| Package     | Why                                            |
|-------------|------------------------------------------------|
| `polars`    | DataFrame sampling, profiling, apply() output  |
| `rapidfuzz` | Fuzzy name matching                            |
| `scipy`     | `linear_sum_assignment` for optimal mapping    |
| `pyyaml`    | Config and schema file parsing                 |
| `typer`     | CLI                                            |

### Optional Extras

| Extra                    | Package                   | Why                  |
|--------------------------|---------------------------|----------------------|
| `infermap[postgres]`     | `psycopg2-binary`         | PostgreSQL connector |
| `infermap[mysql]`        | `mysql-connector-python`  | MySQL connector      |
| `infermap[duckdb]`       | `duckdb`                  | DuckDB connector     |
| `infermap[excel]`        | `openpyxl`                | Excel file reading   |
| `infermap[llm]`          | `openai` / `anthropic`    | LLM scorer           |
| `infermap[all]`          | all of the above          | Everything           |

SQLite uses stdlib `sqlite3` — no extra dependency.

---

## Error Handling

| Scenario                              | Behavior                                                                                       |
|---------------------------------------|------------------------------------------------------------------------------------------------|
| Source file doesn't exist             | `InferMapError("File not found: path.csv")`                                                    |
| DB connection fails                   | `ConnectionError` with dialect + host info, no credentials leaked                              |
| Zero mappings above threshold         | `MapResult` with empty `mappings`, all fields unmapped, warning emitted                        |
| Required target field unmapped        | `MapResult.warnings`: `"required field 'email' has no match (best candidate: 'e_addr' at 0.24)"` |
| Schema file invalid YAML             | `ConfigError` with line number                                                                 |
| Custom scorer raises exception        | Caught, logged as warning, scorer skipped for that pair. Pipeline continues.                   |
| All sample values are null            | `FieldInfo.sample_values = []`, sample-dependent scorers (PatternTypeScorer, ProfileScorer) return `None` (abstain); only name-based scorers contribute |
| DB table has zero rows                | Same as all-null: sample-dependent scorers abstain, name-based scorers contribute. Warning emitted. |
| `apply()` with missing source columns | `ApplyError` listing which expected columns are missing                                        |

No silent swallowing. Every degradation is visible in `MapResult.warnings` or logged.

### Logging

Uses Python stdlib `logging` with logger name `"infermap"`. Default level: `WARNING`. Users configure verbosity via standard Python mechanisms:

```python
import logging
logging.getLogger("infermap").setLevel(logging.DEBUG)
```

CLI supports `--verbose` (sets `INFO`) and `--debug` (sets `DEBUG`). Log output includes: scorer execution times, provider connection status, assignment matrix dimensions, and filtered pair counts.

---

## Testing Strategy

| Layer                | What                                              | How                                                                                     |
|----------------------|---------------------------------------------------|-----------------------------------------------------------------------------------------|
| Scorers (unit)       | Each scorer independently against known pairs     | Parametrized pytest: given two FieldInfos, expect score in range                        |
| Providers (unit)     | Each provider extracts correct SchemaInfo          | Fixture files, SQLite in-memory, mock DB for Postgres/MySQL/DuckDB                      |
| Assignment (unit)    | Optimal assignment edge cases                      | Score matrices with ties, M!=N shapes, all-zeros, single column                         |
| Engine (integration) | End-to-end: file -> map -> result                  | Real CSV fixtures with known correct mappings, assert pairs and confidence ranges        |
| CLI (smoke)          | Commands run without error                         | `infermap map`, `infermap inspect`, `infermap validate` against fixtures                 |
| Regression           | Known tricky schemas that previously failed        | Curated fixture set that grows over time                                                |

### Test Fixtures

Shipped in `tests/fixtures/`:

- `crm_export.csv` — `fname`, `lname`, `email_addr`, `tel`
- `canonical_customers.csv` — `first_name`, `last_name`, `email`, `phone`
- `healthcare_hl7.csv` — `PID`, `PatientName`, `DOB`, `MRN`
- `ambiguous.csv` — columns named `ref`, `code`, `val` (stress test for type inference)

---

## Release Plan

### v1.0 — Python Library + CLI

- Core types (FieldInfo, SchemaInfo, MapResult, etc.)
- 5 built-in scorers (exact, alias, pattern type, profile, fuzzy name)
- 4 providers (file, DB, schema file, in-memory)
- MapEngine with optimal assignment
- CLI (map, apply, inspect, validate)
- Plugin system (custom scorers via decorator)
- PyPI: `pip install infermap`

### v1.1 — TypeScript SDK + LLM Scorer

- TypeScript SDK (subprocess + HTTP modes)
- npm: `npm install infermap`
- `infermap serve` HTTP server for SDK integration (API spec to be a separate document)
- LLM scorer (optional extra, `pip install infermap[llm]`):
  - **Prompt design:** sends field name + 10 sample values + target schema summary; asks LLM to classify the semantic type and suggest the best target match
  - **Batching:** groups all unresolved source fields into a single prompt to minimize API calls
  - **Model:** configurable, defaults to `gpt-4o-mini` (cheapest capable model)
  - **Cost controls:** max calls per mapping run (default 1), budget cap in config
  - **Error handling:** API timeout/failure -> scorer returns `None` (abstains), pipeline continues with other scorers
  - **Latency:** expected 1-3s per batch call; acceptable for batch/offline mapping, not for real-time

### Future

- MCP server for AI agent integration
- Composite column detection (full_name -> first_name + last_name)
- Confidence calibration from user feedback
- Batch mode for mapping many sources to one target
