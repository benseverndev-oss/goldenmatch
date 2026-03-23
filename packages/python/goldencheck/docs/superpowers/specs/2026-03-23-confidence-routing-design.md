# Confidence-Based LLM Routing Design Spec

**Date:** 2026-03-23
**Author:** Ben Severn
**Status:** Draft
**Parent specs:** `2026-03-22-goldencheck-design.md`, `2026-03-23-llm-boost-design.md`

## Overview

Improve profiler accuracy with aggressive auto-config, add confidence scoring to every finding, and route only low-confidence findings to the LLM. This makes profiler-only mode better AND makes LLM boost cheaper and more surgical.

## Goals

1. Push profiler-only column recall from 87% toward 95%+ via smarter auto-config
2. Add confidence scores to findings so users know what's certain vs. ambiguous
3. Route only low-confidence columns to LLM (fewer tokens, lower cost)
4. Without LLM, low-confidence findings report as INFO instead of WARNING

## Part 1: Aggressive Auto-Config (Profiler Improvements)

### 1a. Type Inference — Minority Wrong Type Detection

Current: only flags if >80% of values are numeric in a string column.

New: add a **second check** — if <5% of values are a *different* type than the majority, flag them as suspicious. This catches "12345" in a name column (0.06% numeric).

Logic:
```
If column is string:
    numeric_pct = cast to Float64, count non-null
    if numeric_pct >= 0.80:
        WARNING: "Column is string but mostly numeric" (existing)
    elif numeric_pct > 0 and numeric_pct < 0.05:
        INFO (low confidence): "Column has {n} numeric values in an otherwise text column"
        confidence = 0.3  (low — could be valid data like "Suite 100")
```

### 1b. Range Profiler — Chain with Type Inference

Current: skips non-numeric dtypes entirely.

New: if a column is String dtype but type inference detected it as "mostly numeric" (>80%), run range profiler on the castable subset.

**Inter-profiler communication:** The scanner maintains a `profiler_context: dict[str, dict]` that profilers can read/write. After type inference runs, it writes `profiler_context[col_name]["mostly_numeric"] = True` for flagged columns. The range profiler checks this context before deciding to skip a string column. The `BaseProfiler.profile()` signature gains an optional `context: dict | None = None` keyword argument (default None for backward compatibility). Profilers that don't need context ignore it.

```python
class BaseProfiler(ABC):
    @abstractmethod
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        ...
```

Existing tests calling `.profile(df, column)` continue to work since `context` defaults to None.

### 1c. Temporal Order — Broaden Heuristics

Current keyword pairs: `(start, end)`, `(created, updated)`, `(begin, finish)`.

New: add these pairs:
- `(signup, login)`, `(signup, last_login)`
- `(open, close)`, `(opened, closed)`
- `(hire, termination)`, `(birth, death)`
- `(order, delivery)`, `(order, ship)`

Also: **any two Date-typed columns** in the same dataset get checked as a candidate pair, regardless of name. Report as low confidence if the pair wasn't matched by keyword heuristic.

**Guard:** If the dataset has more than 10 date columns, skip the any-date-pair check and log a debug warning: "Too many date columns ({n}) for exhaustive pair check. Only keyword-matched pairs will be checked."

### 1d. Null Correlation — Reduce Noise

Current: reports any column pair with >90% null agreement.

New:
- Require at least one column to have >5% nulls (skip pairs where both are nearly complete)
- Only report groups of 3+ correlated columns (pairs are too noisy on small datasets)
- Raise threshold from 90% to 95%

**Grouping algorithm:** Continue scanning all column pairs. Build an adjacency graph of correlated pairs. Use union-find to merge connected pairs into groups. Only report groups with 3+ members. Report as a single finding per group with all member columns listed.

## Part 2: Confidence Scoring

### Confidence Levels

| Level | Score Range | Meaning |
|-------|------------|---------|
| High | >= 0.8 | Clear signal, almost certainly a real issue |
| Medium | >= 0.5 and < 0.8 | Likely real but some ambiguity |
| Low | < 0.5 | Uncertain, could be noise or a real issue |

### Model Change

Add `confidence: float = 1.0` field to the `Finding` dataclass:

```python
@dataclass
class Finding:
    severity: Severity
    column: str
    check: str
    message: str
    affected_rows: int = 0
    sample_values: list[str] = field(default_factory=list)
    suggestion: str | None = None
    pinned: bool = False
    source: str | None = None
    confidence: float = 1.0  # NEW: 0.0-1.0, default high
```

### Confidence Assignment Rules

Each profiler assigns confidence based on statistical signal strength:

**Type inference:**
- >80% numeric in string column → confidence 0.9 (high)
- <5% numeric in string column → confidence 0.3 (low — "minority wrong type")

**Nullability:**
- 0 nulls in 1000+ rows → confidence 0.95 (high, likely required)
- 0 nulls in <50 rows → confidence 0.5 (medium, small sample)
- All null → confidence 0.99 (high)

**Uniqueness:**
- 100% unique, 100+ rows → confidence 0.95 (high)
- 95-99% unique → confidence 0.6 (medium)

**Format detection:**
- >95% match a format → confidence 0.9 (high)
- 70-95% match → confidence 0.6 (medium)

**Range/distribution:**
- Outlier >5 stddev → confidence 0.9 (high, extreme)
- Outlier 3-5 stddev → confidence 0.7 (medium)

**Cardinality:**
- <10 unique in 1000+ rows → confidence 0.9 (high, clearly an enum)
- 10-20 unique in 50-100 rows → confidence 0.5 (medium)

**Pattern consistency:**
- Minority pattern <5% → confidence 0.8 (high, clear outlier)
- Minority pattern 5-30% → confidence 0.5 (medium, could be valid variant)

**Temporal order:**
- Keyword-matched pair with violations → confidence 0.9 (high)
- Auto-detected date pair with violations → confidence 0.4 (low, might not be related)

**Null correlation:**
- 3+ columns, >95% agreement → confidence 0.8 (high)
- 2 columns, 90-95% agreement → confidence 0.4 (low)

### Multi-Profiler Corroboration

After all profilers run, boost confidence for columns flagged by multiple profilers:

```
Count WARNING/ERROR profilers per column.
If count == 2: boost each finding's confidence by 0.1 (cap at 1.0)
If count >= 3: boost each finding's confidence by 0.2 (cap at 1.0)
These are exclusive tiers, not cumulative. A 3+ column gets +0.2, not +0.3.
```

**Context dict:** `profiler_context` is created once per `scan_file` call and accumulated across all columns. Each column's profiler writes to `profiler_context[col_name]`, so there's no stale-key risk — keys are namespaced by column name. Scanner must run `TypeInferenceProfiler` before `RangeDistributionProfiler` in `COLUMN_PROFILERS` (ordering dependency).

## Part 3: Confidence-Based LLM Routing

### Without `--llm-boost`

Low-confidence findings are **downgraded to INFO** with a note:
```
INFO: Column 'last_name' has 3 numeric values (low confidence — use --llm-boost to verify)
```

Medium and high confidence findings keep their original severity.

### With `--llm-boost`

1. Collect all columns that have at least one finding with confidence < 0.5
2. Build sample blocks for **only those columns** via `build_sample_blocks(df, findings, focus_columns=low_conf_cols)`
3. Include high-confidence findings on those same columns as context

**`build_sample_blocks` signature change:**
```python
def build_sample_blocks(
    df: pl.DataFrame,
    findings: list[Finding],
    max_columns: int = 50,
    focus_columns: set[str] | None = None,  # NEW: if provided, only build blocks for these columns
) -> dict[str, dict]:
```
When `focus_columns` is None (default), builds all columns (existing behavior, backward compatible). When provided, only builds blocks for columns in the set.
4. Send to LLM with a focused prompt: "These columns have uncertain findings. Confirm or reject each."
5. LLM confirms → upgrade to high confidence, keep severity
6. LLM rejects → downgrade to INFO or remove
7. LLM adds new issues → add as high confidence

### Updated LLM Prompt Addition

Append to the existing system prompt:
```
FOCUS: The following columns have uncertain findings (low confidence).
Your primary job is to confirm or reject these uncertain findings.
Also check for any issues the automated profilers missed entirely.
High-confidence findings are included for context only — don't re-evaluate those.
```

### Cost Impact

- Current: send all columns (~15 columns, ~2000 tokens) → ~$0.01
- New: send only uncertain columns (~3-4 columns, ~500 tokens) → ~$0.003
- ~70% cost reduction

## New/Modified Files

```
Modified:
  goldencheck/models/finding.py          # Add confidence field
  goldencheck/profilers/type_inference.py # Minority wrong type detection
  goldencheck/profilers/range_distribution.py  # Chain with type inference
  goldencheck/relations/temporal.py      # Broader heuristics + any-date-pair
  goldencheck/relations/null_correlation.py    # Noise reduction
  goldencheck/engine/scanner.py          # Corroboration boost, confidence routing
  goldencheck/llm/sample_block.py        # Add focus_columns filter parameter
  goldencheck/reporters/rich_console.py  # Show confidence indicator
  goldencheck/reporters/json_reporter.py # Include confidence in JSON
  goldencheck/tui/findings.py            # Show confidence column

New:
  goldencheck/engine/confidence.py       # Corroboration boost + INFO downgrade

Tests:
  tests/engine/test_confidence.py
  Modified: tests for all changed profilers

### confidence.py Interface

```python
"""Post-scan confidence processing."""
from __future__ import annotations
from dataclasses import replace
from goldencheck.models.finding import Finding, Severity

def apply_corroboration_boost(findings: list[Finding]) -> list[Finding]:
    """Boost confidence for columns flagged by multiple profilers. Returns new list."""
    # Count WARNING/ERROR findings per column
    # 2+ profilers on same column: boost each by 0.1
    # 3+ profilers: boost by 0.2
    # Cap at 1.0. Uses dataclasses.replace(), never mutates.

def apply_confidence_downgrade(findings: list[Finding], llm_boost: bool) -> list[Finding]:
    """Downgrade low-confidence findings to INFO when LLM boost is not enabled.
    When llm_boost=True, leave them as-is (LLM will handle them).
    Returns new list. Uses dataclasses.replace(), never mutates."""
```

Both functions are called in `scanner.py` after all profilers run:
1. `findings = apply_corroboration_boost(findings)`
2. `findings = apply_confidence_downgrade(findings, llm_boost=False)` (or True if opted in)
```

## TUI Changes

Findings tab gains a "Conf" column showing confidence as a colored indicator:
- High: green dot or `H`
- Medium: yellow dot or `M`
- Low: red dot or `L`

## JSON Output Changes

Each finding gains a `confidence` field. Unlike `source` (omitted when None), `confidence` is **always included** in JSON output since it has a meaningful default (1.0) that consumers need:

```json
{
  "severity": "warning",
  "column": "last_name",
  "check": "type_inference",
  "message": "3 numeric values in text column",
  "confidence": 0.3
}
```

## Success Criteria

- Profiler-only column recall: 87% → 95%+
- LLM boost column recall: stays at 100%
- LLM token usage: reduced ~70%
- No regression on existing benchmark scores

## Fallback

If column-level LLM filtering produces worse benchmark results than sending all columns, the fix is to change the `focus_columns` filter in `scanner.py` from `confidence < 0.5` to `None` (send all columns). This is a one-line change. No architectural redesign needed — the `build_sample_blocks` function already supports both modes via the `focus_columns` parameter.
