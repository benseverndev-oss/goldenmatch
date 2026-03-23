# Architecture

## Module Layout

```
goldencheck/
├── cli/
│   └── main.py              # Typer app, all CLI commands and flag parsing
│
├── config/
│   ├── loader.py            # Load and parse goldencheck.yml
│   ├── schema.py            # Pydantic models: GoldenCheckConfig, ColumnRule, etc.
│   └── writer.py            # Serialize config back to YAML
│
├── engine/
│   ├── reader.py            # Read CSV/Parquet/Excel into a Polars DataFrame
│   ├── sampler.py           # maybe_sample() — reservoir sample for large files
│   ├── scanner.py           # Orchestrate profilers, return findings + profile
│   └── validator.py         # Apply pinned rules from config, return violations
│
├── llm/
│   ├── budget.py            # Cost estimation, budget enforcement, CostReport
│   ├── merger.py            # Merge LLM findings into profiler findings
│   ├── parser.py            # Parse LLM JSON response into LLMResponse Pydantic model
│   ├── prompts.py           # System prompt and Pydantic models for LLM I/O
│   ├── providers.py         # call_llm() wrappers for Anthropic and OpenAI
│   └── sample_block.py      # build_sample_blocks() — compact column summaries
│
├── mcp/
│   ├── __init__.py
│   └── server.py            # MCP server: 6 tools (scan, validate, profile, etc.)
│
├── models/
│   ├── finding.py           # Finding dataclass, Severity enum (with _repr_html_)
│   └── profile.py           # ColumnProfile, DatasetProfile (with _repr_html_)
│
├── notebook.py              # ScanResult wrapper + HTML renderers for Jupyter/Colab
│
├── profilers/
│   ├── base.py              # BaseProfiler ABC
│   ├── cardinality.py
│   ├── drift_detection.py   # Categorical and numeric drift between dataset halves
│   ├── encoding_detection.py# Zero-width Unicode, smart quotes, Latin-1 mojibake
│   ├── format_detection.py
│   ├── nullability.py
│   ├── pattern_consistency.py
│   ├── range_distribution.py
│   ├── sequence_gap.py      # Gap detection for integer sequences
│   ├── type_inference.py
│   └── uniqueness.py
│
├── relations/
│   ├── null_correlation.py
│   └── temporal.py
│
├── semantic/
│   ├── classifier.py        # SemanticTypeClassifier — maps columns to semantic types
│   └── types.py             # Built-in semantic type definitions
│
├── suppression/
│   └── engine.py            # SuppressionEngine — applies ignore rules and deduplication
│
├── reporters/
│   ├── ci_reporter.py       # report_ci() — compute exit code from findings
│   ├── json_reporter.py     # report_json() — serialize findings to JSON stdout
│   └── rich_console.py      # report_rich() — pretty console output via Rich
│
└── tui/
    ├── app.py               # GoldenCheckApp (Textual App, bindings, save logic)
    ├── column_detail.py     # Tab 3: column drill-down
    ├── findings.py          # Tab 2: findings table with pin toggle
    ├── overview.py          # Tab 1: health score and dataset stats
    └── rules.py             # Tab 4: pinned rules and config rules
```

---

## Data Flow

### Scan (no config)

```
goldencheck data.csv
         │
         ▼
    cli/main.py  →  _do_scan()
         │
         ▼
    engine/reader.py
    read_file(path) → pl.DataFrame
         │
         ▼
    engine/sampler.py
    maybe_sample(df, max_rows=100_000) → pl.DataFrame
         │
         ▼
    engine/scanner.py  scan_file()
         │
         ├─ for each column:
         │    COLUMN_PROFILERS (10 profilers) → list[Finding]
         │
         ├─ RELATION_PROFILERS (2 profilers) → list[Finding]
         │
         ├─ semantic/classifier.py
         │    SemanticTypeClassifier → annotate findings with semantic context
         │
         ├─ suppression/engine.py
         │    SuppressionEngine → apply ignore rules, dedup
         │
         ├─ confidence scoring pipeline
         │    assign H/M/L confidence to each Finding
         │
         └─ sort by severity → (list[Finding], DatasetProfile)
                  │
                  ▼
         ┌─────────────────────────┐
         │  --no-tui?   --json?    │
         │  reporters/rich_console │
         │  reporters/json_reporter│
         │  tui/app.py             │
         └─────────────────────────┘
```

### Scan with LLM Boost

```
scan_file_with_llm()
         │
         ├─ check_llm_available()  (fail fast if no API key)
         ├─ scan_file(..., return_sample=True)
         │    └─ (findings, profile, sample_df)
         │
         ├─ estimate_cost()  →  check_budget()
         │
         ├─ build_sample_blocks(sample_df, findings)
         │    └─ JSON summary per column
         │
         ├─ call_llm(provider, user_prompt)
         │    └─ (raw_text, input_tokens, output_tokens)
         │
         ├─ CostReport.record()  →  log actual cost
         │
         ├─ parse_llm_response(raw_text)  →  LLMResponse
         │
         └─ merge_llm_findings(findings, llm_response)
              └─ new issues appended, upgrades/downgrades applied
```

### Validate

```
goldencheck validate data.csv
         │
         ▼
    config/loader.py  load_config("goldencheck.yml")
         │
         ▼
    engine/validator.py  validate_file(path, config)
         │  applies ColumnRule constraints + RelationRule checks
         └─ returns list[Finding] (violations only)
         │
         ▼
    engine/scanner.py  scan_file(path)
         └─ for column profile display in TUI
         │
         ▼
    reporters / tui  (same as scan)
         │
         ▼
    reporters/ci_reporter.py  report_ci(findings, fail_on)
         └─ returns exit code 0 or 1
```

---

## Key Data Structures

### `Finding`

```python
@dataclass
class Finding:
    severity: Severity        # INFO=1, WARNING=2, ERROR=3
    column: str               # column name (or "col_a,col_b" for relations)
    check: str                # profiler check name
    message: str              # human-readable description
    affected_rows: int = 0    # number of affected rows
    sample_values: list[str]  # example bad values
    suggestion: str | None    # recommended fix
    pinned: bool = False      # promoted to a rule by the user
    source: str | None        # "llm" if from LLM Boost, None for profiler
    confidence: str | None    # "H", "M", or "L" — set by confidence scoring pipeline
    semantic_type: str | None # e.g., "email", "phone", "name" — from SemanticTypeClassifier
```

### `GoldenCheckConfig`

```python
class GoldenCheckConfig(BaseModel):
    version: int = 1
    settings: Settings = Settings()
    columns: dict[str, ColumnRule] = {}
    relations: list[RelationRule] = []
    ignore: list[IgnoreEntry] = []
```

---

## Semantic Type Classification

**Module:** `goldencheck/semantic/`

The semantic type classifier runs after all profilers and annotates each column with an inferred semantic type. This enables smarter severity assessment — for example, knowing a column is an email means nulls in it are more likely to be errors, not just INFO.

**Built-in semantic types** (from `goldencheck/semantic/types.py`):

| Semantic Type | Detection heuristic |
|---------------|-------------------|
| `email` | FormatDetectionProfiler classified it + >70% match |
| `phone` | FormatDetectionProfiler classified it as phone |
| `url` | FormatDetectionProfiler classified it as URL |
| `name` | Column name contains `name`, `first`, `last`, `full` |
| `id` | Column is 100% unique integer |
| `currency` | Column name contains `price`, `amount`, `cost`, `total` |
| `date` | Temporal column (parsed as date) |
| `category` | Low cardinality string (<20 unique values) |

**Custom types** can be added in `goldencheck_types.yaml` — see [Configuration](Configuration.md#semantic-types).

---

## Suppression Engine

**Module:** `goldencheck/suppression/engine.py`

The suppression engine applies the `ignore` list from `goldencheck.yml` to filter findings before they are returned to the CLI or TUI. It runs after all profilers and after the confidence scoring pipeline.

**Rules:** An `ignore` entry matches a finding if both `column` and `check` match. Suppressed findings are dropped entirely — they do not appear in output or count toward the health score.

The suppression engine also deduplicates findings: if two profilers emit the same `(column, check)` pair, only the higher-severity finding is kept.

---

## Confidence Scoring Pipeline

Each `Finding` carries a `confidence` field set to `"H"` (high), `"M"` (medium), or `"L"` (low). Confidence is assigned based on how deterministic the detection logic is:

| Confidence | Meaning | Examples |
|------------|---------|---------|
| `H` | Deterministic — rule is exact | Type mismatch, format violation, enum violation, temporal order error |
| `M` | Heuristic — likely but not certain | Outlier detection, near-unique duplicates, pattern inconsistency |
| `L` | Statistical — could be noise | Null correlation, drift detection, sequence gaps in sparse data |

Confidence is displayed in the TUI (Findings tab, `Conf` column) and included in JSON output. The LLM Boost pass can revise confidence when it upgrades or downgrades a finding.

---

## How to Extend

### Add a column profiler

1. Create `goldencheck/profilers/my_profiler.py` implementing `BaseProfiler.profile(df, column) -> list[Finding]`.
2. Add an instance to `COLUMN_PROFILERS` in `goldencheck/engine/scanner.py`.

See [Profilers](Profilers.md#adding-a-custom-profiler) for a full example.

### Add a cross-column profiler

1. Create `goldencheck/relations/my_relation.py` with a `profile(df: pl.DataFrame) -> list[Finding]` method.
2. Add an instance to `RELATION_PROFILERS` in `goldencheck/engine/scanner.py`.

### Add a reporter

Reporters are simple functions. The interface used by the CLI:

```python
def report_myformat(findings: list[Finding], profile: DatasetProfile, out=sys.stdout) -> None:
    ...
```

Add a call site in `goldencheck/cli/main.py` inside `_do_scan()` alongside the existing `--json` branch.

### Add a config rule type

1. Add a new field to `ColumnRule` or `RelationRule` in `goldencheck/config/schema.py`.
2. Implement the validation logic in `goldencheck/engine/validator.py`.
3. Update the YAML writer in `goldencheck/config/writer.py` if serialization needs adjusting.

### Add a TUI tab

1. Create a new Textual widget in `goldencheck/tui/my_tab.py` (subclass `Vertical` or `Widget`).
2. Add a `TabPane` for it in `GoldenCheckApp.compose()` in `goldencheck/tui/app.py`.
3. Add a `Binding` for the number key in `BINDINGS`.
