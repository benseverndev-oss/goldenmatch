# GoldenPipe -- Design Spec

## Overview

Orchestrator for the Golden Suite. Chains GoldenCheck → GoldenFlow → GoldenMatch with adaptive logic. One file in, golden records out.

**Goal:** `gp.run("customers.csv")` checks quality, fixes issues, deduplicates records, and returns everything in one `PipeResult` -- with reasoning for every decision.

**Path:** v0.1 (files) → v0.2 (databases) → v0.3 (A2A/MCP agent)

---

## Data Handoff

All stages pass **Polars DataFrames in memory**. No temp files.

```
File path → load as DataFrame
  → GoldenCheck.scan_file(path) → ScanResult (findings + profiles)
  → GoldenFlow.transform_df(df) → TransformResult (df + manifest)
  → GoldenMatch.dedupe_df(df) → DedupeResult (clusters + golden)
```

- `pipeline.py` loads the file once with `polars.read_csv()` (or `read_excel`/`scan_parquet`)
- Check receives the original path (its API expects a path)
- Flow receives the DataFrame via `gf.transform_df(df)`
- Match receives Flow's output DataFrame via `gm.dedupe_df(df)`
- If Flow is skipped, Match receives the original DataFrame

---

## Python API

```python
import goldenpipe as gp

# One-liner
result = gp.run("customers.csv")
result.status       # "success" | "partial" | "failed"
result.check        # ScanResult (findings, profiles)
result.transform    # TransformResult (manifest, what changed)
result.match        # DedupeResult (clusters, golden records)
result.skipped      # ["flow"] if no quality issues found
result.errors       # ["GoldenFlow: ValueError: ..."] if any stage failed
result.reasoning    # {"check": "...", "flow": "...", "match": "..."}
result.timing       # {"check": 1.2, "flow": 0.8, "match": 3.1}
result.source       # "customers.csv"
result.input_rows   # 1000

# Step by step
pipe = gp.Pipeline("customers.csv")
pipe.check()        # Can be called independently
pipe.flow()         # Requires check() first, raises if not called
pipe.match()        # Requires check() first, flow() optional
pipe.result

# Force PPRL
result = gp.run("customers.csv", strategy="pprl")
```

---

## Adaptive Logic

`goldenpipe/decisions.py` -- pure functions on dataclasses, no tool imports.

### decide_flow

```python
def decide_flow(check_result) -> FlowDecision:
    if not check_result.findings:
        return FlowDecision(skip=True, reason="No quality issues found")

    # Separate fatal from fixable
    fatal = [f for f in check_result.findings if f.severity == "critical"]
    fixable = [f for f in check_result.findings if f.severity != "critical"]

    if fatal and not fixable:
        return FlowDecision(skip=True, abort=True,
                           reason=f"Fatal issues found: {[f.check for f in fatal]}. Cannot proceed.")

    return FlowDecision(skip=False, findings=fixable,
                       reason=f"{len(fixable)} fixable issues, {len(fatal)} fatal (will be in errors)")
```

### decide_match

```python
def decide_match(check_result, row_count, strategy_override=None) -> MatchDecision:
    # User override takes priority
    if strategy_override:
        return MatchDecision(strategy=strategy_override, reason=f"User specified: {strategy_override}")

    # Too few rows to match
    if row_count < 2:
        return MatchDecision(skip=True, reason=f"Only {row_count} rows -- nothing to deduplicate")

    # PII detection
    sensitive = any(f.check == "pii_detection" for f in (check_result.findings if check_result else []))
    if sensitive:
        return MatchDecision(strategy="pprl", reason="Sensitive fields detected")

    return MatchDecision(strategy="auto")
```

Decisions are recorded in `PipeResult.reasoning`.

---

## Error Handling

### Stage failures

If a stage raises an exception:
1. The error is caught and recorded in `PipeResult.errors`
2. The pipeline continues to the next stage with whatever data it has
3. `PipeResult.status` is set to `"partial"`
4. If ALL stages fail, status is `"failed"`

```python
try:
    check_result = goldencheck.scan_file(path)
except Exception as e:
    self.errors.append(f"GoldenCheck: {e}")
    check_result = None  # Skip to Match with original data
```

### Missing dependencies

Each tool import is wrapped:

```python
try:
    import goldencheck
    HAS_CHECK = True
except ImportError:
    HAS_CHECK = False
```

If a tool is missing, its stage is skipped with a clear message in `PipeResult.skipped` and `PipeResult.reasoning`.

### State validation

Calling `pipe.match()` before `pipe.check()` is allowed -- it skips check and flow, goes straight to match. The step-by-step API is permissive. The one-liner `gp.run()` always runs in order.

---

## CLI

```bash
goldenpipe run data.csv                    # Full pipeline
goldenpipe run data.csv --skip-flow        # Check + Match only
goldenpipe run data.csv --skip-match       # Check + Flow only
goldenpipe run data.csv --strategy pprl    # Force PPRL matching
goldenpipe run data.csv --output golden.csv
goldenpipe run data.csv --verbose          # Show each stage's reasoning
```

---

## PipeResult

```python
@dataclass
class PipeResult:
    status: str                         # "success" | "partial" | "failed"
    source: str                         # Input file path
    input_rows: int                     # Row count of input
    check: ScanResult | None            # GoldenCheck output
    transform: TransformResult | None   # GoldenFlow output
    match: DedupeResult | None          # GoldenMatch output
    skipped: list[str]                  # Stages skipped and why
    errors: list[str]                   # Stage error messages
    reasoning: dict[str, str]           # {"check": "...", "flow": "...", "match": "..."}
    timing: dict[str, float]            # {"check": 1.2, "flow": 0.8, "match": 3.1}
```

---

## Repo Structure

```
D:\show_case\goldenpipe\
├── goldenpipe/
│   ├── __init__.py          # run(), Pipeline, PipeResult
│   ├── pipeline.py          # Pipeline class (imports tools)
│   ├── decisions.py         # Adaptive logic (no tool imports)
│   ├── cli/
│   │   └── main.py          # Typer CLI
│   └── config/
│       └── schema.py        # PipeConfig Pydantic model
├── tests/
│   ├── test_pipeline.py     # End-to-end pipeline tests
│   ├── test_decisions.py    # Decision logic (no tool deps)
│   └── test_cli.py          # CLI tests
├── docs/
│   └── _config.yml          # Jekyll + Just the Docs
├── CLAUDE.md
├── pyproject.toml
├── README.md
├── LICENSE
└── .github/workflows/
    ├── ci.yml
    └── publish.yml
```

**Dependencies:** `goldencheck`, `goldenflow`, `goldenmatch` as required. `polars`, `typer`, `rich` for core.

**Key design:** Only `pipeline.py` imports from the three tools. `decisions.py` works on dataclasses only -- testable without the full suite.

---

## A2A Port Convention

| Tool | A2A Port |
|------|----------|
| GoldenCheck | 8100 |
| GoldenFlow | 8150 |
| GoldenMatch | 8200 |
| GoldenPipe | 8250 (future v0.3) |
