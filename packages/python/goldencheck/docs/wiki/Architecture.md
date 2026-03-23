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
├── models/
│   ├── finding.py           # Finding dataclass, Severity enum
│   └── profile.py           # ColumnProfile, DatasetProfile dataclasses
│
├── profilers/
│   ├── base.py              # BaseProfiler ABC
│   ├── cardinality.py
│   ├── format_detection.py
│   ├── nullability.py
│   ├── pattern_consistency.py
│   ├── range_distribution.py
│   ├── type_inference.py
│   └── uniqueness.py
│
├── relations/
│   ├── null_correlation.py
│   └── temporal.py
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
         │    COLUMN_PROFILERS (7 profilers) → list[Finding]
         │
         ├─ RELATION_PROFILERS (2 profilers) → list[Finding]
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
