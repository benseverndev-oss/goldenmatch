# GoldenCheck Design Spec

**Date:** 2026-03-22
**Author:** Ben Severn
**Status:** Draft

## Overview

GoldenCheck is a Python data validation toolkit that discovers validation rules from your data so you don't have to write them. It profiles datasets, infers what "healthy" looks like, and presents findings in an interactive TUI where users pin the rules they care about.

**One-liner:** "Every competitor makes you write rules first. GoldenCheck flips it: validate first, keep the rules you care about."

## Target User

Primary: Solo data scientist/analyst working with CSV/Parquet/Excel files. Design upward to teams and pipelines, but optimize the solo experience first.

Adoption path: Solo user falls in love -> brings it to their team -> team integrates it into pipelines.

## Competitive Positioning

| Aspect | GoldenCheck | Great Expectations | Pandera | Pointblank |
|--------|-------------|-------------------|---------|------------|
| Rules | Discovered from data | Written by hand | Written by hand | Written by hand |
| Config | Zero to start | Heavy YAML/Python setup | Decorators/schemas | YAML or Python |
| Interface | CLI + interactive TUI | HTML reports | Exceptions | HTML/notebook |
| Pipeline-ready | Exit codes day one | Yes but complex setup | Yes | Limited |
| Learning curve | `goldencheck data.csv` | Hours/days | Moderate | Moderate |
| Fix suggestions | Yes, in TUI | No | No | No |

## CLI Interface

```bash
# Zero-config scan — discovers rules from data, launches TUI
goldencheck data.csv

# Explicit scan subcommand (same as above)
goldencheck scan data.csv

# Validate against saved rules (CLI output, exit codes)
goldencheck validate data.csv

# Interactive TUI review of existing rules + new findings
goldencheck review data.csv

# Pipeline mode
goldencheck validate data.csv --fail-on warning   # exit 1 on warnings or errors
goldencheck validate data.csv --fail-on error      # exit 1 only on errors (default)

# Multiple files
goldencheck validate *.csv

# No TUI mode
goldencheck data.csv --no-tui

# Verbose/debug
goldencheck data.csv --verbose
goldencheck data.csv --debug
```

### Command Behavior

**`goldencheck data.csv` (alias for `goldencheck scan`):**
- Always profiles the data fresh, regardless of whether `goldencheck.yml` exists.
- If `goldencheck.yml` exists, loads pinned rules and ignores — new findings appear alongside existing rules in the TUI, but pinned rules are preserved.
- Launches TUI for interactive review.

**`goldencheck validate data.csv`:**
- Requires `goldencheck.yml` to exist (exits with error and helpful message if not).
- Validates data against pinned rules only. No profiling, no discovery.
- Prints Rich CLI report. Returns exit code 0 (pass) or 1 (fail) based on `--fail-on` threshold.

**`goldencheck review data.csv`:**
- Runs both: profiles for new findings AND validates against existing rules.
- Launches TUI showing both pinned rule violations and newly discovered issues.

**Note:** `--strict` is intentionally omitted. Use `--fail-on warning` instead — one mechanism, not two.

**Database support is out of scope for v1.** File-based inputs only (CSV, Parquet, Excel).

## Profiling Engine

### Column-Level Profilers

| Profiler | What it detects | Example finding |
|----------|----------------|-----------------|
| Type inference | Actual types vs. declared types, mixed types | "Column `age` is string but 98% are integers" |
| Nullability | Null patterns, required vs. optional | "Column `email` has 0 nulls across 50k rows — likely required" |
| Uniqueness | ID columns, near-duplicates | "Column `order_id` is 100% unique — likely primary key" |
| Format detection | Emails, phones, URLs, dates, SSNs, zips | "Column `contact` is 94% email format, 6% are malformed" |
| Range & distribution | Min/max, stddev, outlier detection | "Column `price` ranges 1-500, but 3 rows have values >10,000" |
| Cardinality | Low-cardinality = likely enum/category | "Column `status` has 4 unique values — possible enum: active, inactive, pending, closed" |
| Pattern consistency | Regex patterns within string columns | "Column `phone` has 3 formats: (XXX) XXX-XXXX, XXX-XXX-XXXX, XXXXXXXXXX" |

### Cross-Column Profilers (v1 Scope)

v1 ships with two cross-column profilers. The others are deferred to v2.

| Profiler | What it detects | v1? |
|----------|----------------|-----|
| Temporal ordering | start_date < end_date (auto-detected date column pairs) | Yes |
| Completeness correlation | When column A is null, column B is always null too | Yes |
| Referential consistency | state matches zip, country matches currency | v2 (requires lookup tables) |
| Dependency detection | Column B is always derivable from column A | v2 (computationally expensive) |

### Severity Levels

- **Error** — almost certainly wrong (null in a never-null column, wrong type)
- **Warning** — suspicious, needs human judgment (outlier, format inconsistency)
- **Info** — interesting pattern, not necessarily a problem (detected enum, inferred type)

### Performance

All profilers are Polars-native. Smart sampling for files >1M rows: profile a statistically valid sample, validate against the full dataset.

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Empty file (0 rows) | Exit with message: "File has no data rows. Nothing to profile." |
| Headers only (0 data rows) | Report column names and types as info, no validation findings |
| 1000+ columns | Profile all columns but warn: "Wide dataset detected. Consider --columns flag to focus." |
| Encoding issues | Attempt UTF-8, fall back to Latin-1, then fail with clear error naming the encoding issue |
| Malformed CSV | Polars error surfaced with suggestion: "Try specifying --separator or --quote-char" |
| File not found | Clear error with path shown |
| Password-protected Excel | Error: "File appears to be password-protected. GoldenCheck cannot read encrypted files." |

## TUI Design

Built with Textual, gold-themed color scheme matching the Golden brand.

### Tabs

| Tab | Key | Purpose |
|-----|-----|---------|
| Overview | 1 | File stats, column count, row count, overall health score (A-F grade) |
| Findings | 2 | All discovered issues sorted by severity. Each has: description, affected rows, sample values, Pin as Rule toggle. Filter by severity. |
| Column Detail | 3 | Select a column to see full profile: type, nulls, distribution, top values, format, outliers. Drill-down from Findings. |
| Rules | 4 | All pinned rules. Edit thresholds. Export to goldencheck.yml. Remove unwanted rules. |

### Health Score Algorithm

The Overview tab displays an A-F health grade calculated as:

1. Start at 100 points.
2. Deduct per finding: Error = -10, Warning = -3, Info = -0 (info findings don't penalize).
3. Cap deductions per column at -20 (one bad column doesn't tank the whole score).
4. Grade mapping: A = 90-100, B = 80-89, C = 70-79, D = 60-69, F = below 60.
5. Display: letter grade + point total + breakdown (e.g., "B (84) — 2 errors, 5 warnings across 3 columns").

### Key Interactions

- `1-4` — tab navigation
- `Enter` on finding — drill into Column Detail
- `Space` on finding — toggle Pin as Rule
- `F5` — re-run profiling (shows progress overlay for large files)
- `F2` — save/export rules to YAML
- `e` on finding — see offending rows
- `?` — help

### Fix Suggestions

When a format inconsistency is detected (e.g., mixed date formats), the Findings tab shows a suggestion line:

```
WARNING: Column `date` has 2 formats: MM/DD/YYYY (94%), DD-MM-YYYY (6%)
  Suggestion: Standardize to MM/DD/YYYY (majority format)
  [Space] Pin rule  |  [e] View 127 affected rows
```

Suggestions are read-only hints. GoldenCheck never modifies source data.

### CLI-Only Mode

`goldencheck data.csv --no-tui` prints a Rich-formatted table to stdout. Same findings, no interactivity. Good for piping, CI, quick checks.

## Rule Configuration (goldencheck.yml)

```yaml
version: 1

settings:
  sample_size: 100000
  severity_threshold: warning
  fail_on: error

columns:
  email:
    type: string
    required: true
    format: email
    unique: true

  age:
    type: integer
    range: [0, 120]
    required: true

  status:
    type: string
    enum: [active, inactive, pending, closed]

  price:
    type: float
    range: [0.01, 10000]
    outlier_stddev: 3

  phone:
    type: string
    format: phone
    nullable: true

relations:
  - type: temporal_order
    columns: [start_date, end_date]
  - type: null_correlation
    columns: [shipping_address, shipping_city, shipping_zip]

ignore:
  - column: notes
    check: nullability
```

### Relations Syntax

Relations use structured YAML, not a free-text DSL. Each relation has a `type` and `columns` field.

**v1 relation types:**

| Type | Meaning | Example |
|------|---------|---------|
| `temporal_order` | First column's date < second column's date | `{type: temporal_order, columns: [start_date, end_date]}` |
| `null_correlation` | All listed columns are null together or non-null together | `{type: null_correlation, columns: [addr, city, zip]}` |

Additional types (v2): `zip_matches_state`, `functional_dependency`, `foreign_key`.

### Config Layering Strategy

The `goldencheck.yml` file contains **only user-pinned rules and ignores**. Auto-discovered findings are never written to the file.

**Merge behavior on re-scan:**
- Pinned rules are preserved. If the underlying data changes and a pinned rule's column no longer exists, the rule is kept in the file but flagged as "stale" in the TUI with a warning.
- Ignores are preserved. Dismissed findings stay dismissed.
- New findings from re-profiling appear in the TUI as unpinned. The user decides whether to pin or ignore them.

**Config migration:** v1 files that cannot be parsed by a future version will produce a clear error: "goldencheck.yml uses version 1 format. Run `goldencheck migrate` to upgrade." Migration logic deferred to when v2 config schema is defined.

### Design Decisions

- **Human-readable** — an analyst can read and edit without docs
- **Pinned-only** — only user-pinned rules appear in the file, not every discovered finding
- **Ignore list** — prevents alert fatigue. "I've seen this, it's fine" is a first-class concept.
- **Structured relations** — typed YAML objects, not a custom DSL. Extensible without parser changes.

## Output & Action Model

- **Report** — findings displayed in TUI or CLI Rich output
- **Fix suggestions** — in TUI, suggest standardization for format inconsistencies (does not auto-modify data)
- **Exit codes** — 0 for clean, 1 for failures. Configurable via `--fail-on` flag.
- **Machine-readable output** — `--format json` for CI integration

### JSON Output Schema

```json
{
  "file": "data.csv",
  "rows": 50000,
  "columns": 12,
  "health_score": {"grade": "B", "points": 84},
  "summary": {"errors": 2, "warnings": 5, "info": 8},
  "findings": [
    {
      "severity": "error",
      "column": "email",
      "check": "format",
      "message": "6% of values are not valid email format",
      "affected_rows": 3000,
      "sample_values": ["not-an-email", "also bad", "@missing-local"]
    }
  ],
  "rules_applied": 7,
  "rules_passed": 5,
  "rules_failed": 2
}
```

## Architecture

```
goldencheck/
├── cli/              # Typer CLI entry points
│   └── main.py
├── profilers/        # Column-level profilers
│   ├── type_inference.py
│   ├── nullability.py
│   ├── uniqueness.py
│   ├── format_detection.py
│   ├── range_distribution.py
│   ├── cardinality.py
│   └── pattern_consistency.py
├── relations/        # Cross-column profilers
│   ├── temporal.py
│   └── null_correlation.py
├── engine/           # Orchestration
│   ├── scanner.py    # Runs all profilers, collects findings
│   ├── validator.py  # Validates data against saved rules
│   └── sampler.py    # Smart sampling for large files
├── config/           # YAML rule management
│   ├── loader.py
│   ├── writer.py
│   └── schema.py     # Pydantic models
├── tui/              # Textual TUI
│   ├── app.py
│   ├── overview.py
│   ├── findings.py
│   ├── column_detail.py
│   ├── rules.py
│   └── progress_overlay.py
├── reporters/        # Output formats
│   ├── rich_console.py
│   ├── json_reporter.py
│   └── ci_reporter.py
└── models/           # Shared data models
    ├── finding.py
    └── profile.py
```

## Tech Stack

| Dependency | Purpose |
|-----------|---------|
| Polars | All data operations |
| Typer | CLI framework |
| Textual | TUI |
| Rich | CLI output formatting |
| Pydantic 2 | Config validation (intentional upgrade from GoldenMatch's dataclasses — stronger YAML schema enforcement) |
| PyYAML | Config read/write |
| openpyxl | Excel file support |

No optional dependencies for v1. Everything works out of the box.

**Python 3.11+**

**Logging:** Uses Python `logging` module. `--verbose` shows info-level logs. `--debug` shows debug-level logs including profiler timing and sampling decisions.

## Out of Scope (v1)

- LLM-assisted validation
- Semantic/embedding-based checks
- Database connectors (file-based inputs only)
- Auto-fix / data modification
- Web UI / HTML reports
- Multi-file referential integrity
- Streaming / real-time validation
- MCP server / REST API
- Cross-column referential consistency (requires lookup tables)
- Cross-column dependency detection (computationally expensive)
