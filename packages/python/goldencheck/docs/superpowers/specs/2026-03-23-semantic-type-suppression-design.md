# Semantic Type Classification + Suppression Engine Design Spec

**Date:** 2026-03-23
**Author:** Ben Severn
**Status:** Draft
**Parent specs:** `2026-03-22-goldencheck-design.md`, `2026-03-23-llm-boost-design.md`, `2026-03-23-confidence-routing-design.md`

## Overview

Add a semantic type classifier that infers what each column represents (email, name, ID, free text, etc.) and a suppression engine that downgrades irrelevant findings based on the column's type. This improves precision without sacrificing recall — the biggest lever for DQBench score improvement.

## Problem

GoldenCheck emits findings on every column regardless of context. Pattern_consistency on a free-text column, cardinality on an ID column, and type_inference on a phone column are all noise. A human analyst would instantly dismiss these, but GoldenCheck reports them as WARNING, hurting precision.

## Architecture

```
Column data + column name
    ↓
Semantic Type Classifier (name heuristics → value-based fallback)
    ↓
Column type assignments: {"email": "email", "notes": "free_text", ...}
    ↓
Profilers run as normal → all findings collected
    ↓
Suppression Engine: downgrade findings where check is in type's suppress list
    ↓
Cleaner findings with fewer false positives
```

With LLM boost (two-stage):
```
Stage 1: LLM classifies column types (overrides heuristics where it disagrees)
    ↓
Profilers run → findings collected → suppression applied
    ↓
Stage 2: LLM reviews remaining WARNING/ERROR findings (existing behavior)
```

## New Files

```
goldencheck/
├── semantic/
│   ├── __init__.py
│   ├── classifier.py      # classify_columns(df) -> dict[str, str]
│   ├── suppression.py     # apply_suppression(findings, column_types, type_defs) -> list[Finding]
│   └── types.yaml         # Base semantic type definitions
tests/
├── semantic/
│   ├── __init__.py
│   ├── test_classifier.py
│   └── test_suppression.py
```

## Semantic Type Definitions (types.yaml)

```yaml
types:
  identifier:
    name_hints: ["id", "key", "pk", "code", "sku", "number", "num", "record"]
    value_signals:
      min_unique_pct: 0.95
    suppress: ["cardinality", "pattern_consistency", "drift_detection"]

  person_name:
    name_hints: ["name", "first_name", "last_name", "full_name"]
    value_signals:
      mixed_case: true
    suppress: ["pattern_consistency", "cardinality"]

  email:
    name_hints: ["email", "mail", "e_mail"]
    value_signals:
      format_match: "email"
      min_match_pct: 0.70
    suppress: ["pattern_consistency"]

  phone:
    name_hints: ["phone", "tel", "fax", "mobile", "cell"]
    value_signals:
      format_match: "phone"
      min_match_pct: 0.70
    suppress: ["type_inference", "pattern_consistency"]

  address:
    name_hints: ["address", "street", "addr", "line1", "line2"]
    value_signals:
      avg_length_min: 15
    suppress: ["pattern_consistency", "cardinality"]

  free_text:
    name_hints: ["notes", "comment", "description", "text", "memo", "message", "remarks"]
    value_signals:
      avg_length_min: 30
      min_unique_pct: 0.80
    suppress: ["pattern_consistency", "cardinality", "type_inference", "drift_detection"]

  datetime:
    name_hints: ["date", "time", "created", "updated", "_at", "timestamp"]
    value_signals:
      format_match: "date"
    suppress: ["pattern_consistency"]

  boolean:
    name_hints: ["is_", "has_", "flag", "active", "enabled", "disabled"]
    value_signals:
      max_unique: 3
    suppress: ["range_distribution", "uniqueness"]

  currency:
    name_hints: ["amount", "price", "cost", "total", "fee", "payment", "charge", "balance"]
    value_signals:
      numeric: true
    suppress: ["pattern_consistency"]

  code_enum:
    name_hints: ["status", "type", "category", "level", "tier", "grade", "rating", "priority"]
    value_signals:
      max_unique: 20
    suppress: ["uniqueness", "range_distribution"]

  geo:
    name_hints: ["country", "state", "city", "zip", "postal", "region", "province"]
    value_signals:
      short_strings: true
    suppress: ["pattern_consistency"]
```

Users can extend by creating a `goldencheck_types.yaml` in their project directory. User types are merged with (and can override) the base types.

## Classifier Interface

```python
@dataclass
class ColumnClassification:
    type_name: str | None      # semantic type (e.g., "email", "free_text") or None
    source: str                # "name" (heuristic), "value" (inference), "llm", "none"

def classify_columns(
    df: pl.DataFrame,
    custom_types_path: Path | None = None,
) -> dict[str, ColumnClassification]:
    """Classify each column's semantic type with provenance.

    Returns dict mapping column name to ColumnClassification.
    Source tracks HOW the type was determined, enabling LLM Stage 1
    to override name-heuristic classifications but not value-based ones.
    """
```

### TypeDef Data Model

```python
@dataclass
class TypeDef:
    name_hints: list[str]
    value_signals: dict[str, Any]
    suppress: list[str]
```

Loaded from YAML at runtime. Base types from `goldencheck/semantic/types.yaml`. User types from `goldencheck_types.yaml` in working directory (if present). **Merge strategy:** User types with the same name as base types REPLACE the base definition entirely. User types with new names are PREPENDED (higher priority in definition-order matching).

### Name Heuristic Matching

For each column name, check against each type's `name_hints`:
- Convert column name to lowercase
- **Matching rules by hint format:**
  - Hint ending with `_` (e.g., `is_`, `has_`): **prefix match** — column must start with the hint
  - Hint starting with `_` (e.g., `_at`): **suffix match** — column must end with the hint
  - All other hints: **substring match** — hint must appear in the column name
- Match on first hit (types are checked in definition order, same order for value-signal fallback)
- If no name match, fall back to value-based inference

### Value-Based Inference (fallback for unmatched columns)

If no name match, analyze the column values:
- `min_unique_pct`: if `n_unique / n_rows >= threshold`, matches
- `format_match: "email"`: if >70% of string values contain `@` and a domain
- `format_match: "phone"`: if >70% of string values are digit groups
- `format_match: "date"`: if column dtype is Date/Datetime, or >70% parse as dates
- `mixed_case`: if values contain both upper and lowercase letters
- `avg_length_min`: if mean string length >= threshold
- `max_unique`: if unique count <= threshold
- `numeric`: if column dtype is numeric
- `short_strings`: if mean string length < 5

A column matches if ALL specified value_signals are satisfied.

## Suppression Engine Interface

```python
def apply_suppression(
    findings: list[Finding],
    column_types: dict[str, ColumnClassification],
    type_defs: dict[str, TypeDef],
) -> list[Finding]:
    """Downgrade findings where the check type is in the column type's suppress list.

    Uses column_types[col].type_name to look up the type's suppress list in type_defs.
    Suppressed findings: severity changed to INFO, message gets " (suppressed: {type} column)" suffix.
    Uses dataclasses.replace() — never mutates originals.
    Returns new list.
    """
```

Key rules:
- Only suppress WARNING/ERROR findings (INFO stays as-is)
- Never suppress findings with `source="llm"` (LLM findings are always relevant)
- Never suppress findings with initial profiler confidence >= 0.9 (e.g., nullability all-null at 0.99, type_inference mostly-numeric at 0.9). Note: this guard only applies to confidence values assigned directly by profilers, not boost-derived confidence, since suppression runs before corroboration boost.
- The suppressed finding keeps its original check type — just severity changes

## Scanner Integration

In `scanner.py`, the pipeline order is:

```python
# 1. All profilers run → collect all_findings
# 2. Classify columns (semantic types)
column_types = classify_columns(df)
# 3. Apply suppression FIRST (downgrades irrelevant findings to INFO)
all_findings = apply_suppression(all_findings, column_types, type_defs)
# 4. Apply corroboration boost SECOND (only boosts findings that survived suppression)
all_findings = apply_corroboration_boost(all_findings)
# 5. Confidence downgrade runs later in CLI (only if no --llm-boost)
```

**Why suppression before boost:** If boost ran first, a noisy pattern_consistency finding on a free_text column could get boosted to confidence 0.9+ (because multiple profilers fire on text columns), then bypass suppression's confidence guard. By suppressing first, only real findings get boosted.

**Both code paths covered:** Since `scan_file_with_llm` calls `scan_file(..., return_sample=True)`, and suppression is inside `scan_file`, it runs in both paths automatically.

**LLM modification of suppressed findings:** When `merge_llm_findings` upgrades or downgrades a previously-suppressed finding, the merger must strip the `(suppressed: X column)` suffix from the message before appending the LLM reason. Add a shared helper to `merger.py`:

```python
import re

def _strip_suppression_suffix(message: str) -> str:
    return re.sub(r'\s*\(suppressed:.*?\)\s*$', '', message)
```

Use this in both the upgrade and downgrade paths before constructing the new message.

## LLM Two-Stage Integration

### Stage 1: LLM Type Classification (pre-profiler)

When `--llm-boost` is enabled, add a lightweight LLM call before profilers run:

Prompt:
```
Classify each column's semantic type. Return JSON: {"column_name": "type_name"}.

Valid types: identifier, person_name, email, phone, address, free_text, datetime, boolean, currency, code_enum, geo

Columns and sample values:
{column_name: [5 sample values]}
```

This is ~200 tokens input, ~50 tokens output. Cost: ~$0.001.

The LLM classifications override based on provenance:
- `source="name"` → LLM can override (heuristics can be wrong)
- `source="value"` → LLM cannot override (value signals are ground truth)
- `source="none"` → LLM can set the type (nothing to override)

This catches columns with non-obvious names like "col_7" that the LLM recognizes as an email column from the values.

**Limitation:** The Stage 1 prompt's valid-types list is generated dynamically from the loaded type definitions (base + user-merged) at runtime. This ensures user-defined custom types are available for LLM classification.

### Stage 2: LLM Finding Review (post-profiler, existing)

Unchanged from current behavior, but now only receives findings that survived suppression → fewer findings → more focused review → less noise added.

## Config / CLI

No new CLI flags. Suppression is always active (it's a quality improvement, not an optional mode).

Custom types: place `goldencheck_types.yaml` in working directory. Merged with base types at runtime.

## Expected Impact

Suppression reduces false positives on clean columns and noise on planted columns:
- Tier 1: pattern_consistency suppressed on name, address, text columns → fewer FP
- Tier 2: free_text, address, description columns stop generating noise → precision doubles
- Tier 3: healthcare text fields (notes, descriptions) stop generating pattern noise

Conservative estimate: issue precision from 25-32% to 45-55% → DQBench Score from 41 to 50-55.

## Out of Scope

- MCP server for community type configs (v0.3.0)
- Community YAML marketplace (v0.3.0)
- Custom suppression rules per column (too granular for v1)
- Type classification in the TUI (display column types — nice to have, later)
