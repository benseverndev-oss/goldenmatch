# Polish & Credibility — Design Spec

## Goal

Ship a "production-grade polish" pass that adds auto-fix capability, friendly error messages, a defined public API surface, and CI coverage reporting. These changes make GoldenCheck look and feel like a mature, reliable tool.

## Scope

Four independent components, all shipped as v0.4.0.

**Version note:** v0.3.0 was released on 2026-03-24 with MCP server, LLM rules, Jupyter, and DQBench 87.71. The community type configs from the v0.3.0 roadmap are deferred to a future release. v0.4.0 is the correct next version.

1. `goldencheck fix` command
2. Friendly CLI error messages
3. Public API surface (`__all__` + `py.typed`)
4. Coverage badge + CI improvements

---

## 1. `goldencheck fix` Command

### Purpose

Reads a data file, applies automated fixes based on profiler findings, writes a cleaned version.

### Pipeline

The `fix` command internally runs `scan_file()` first to produce findings, then passes those findings to `apply_fixes()`. Some fixes (trim whitespace, remove invisible chars, normalize Unicode) are unconditional and don't require findings. Others (standardize enum case, coerce types) use findings to determine which columns to fix and how.

### CLI Interface

```bash
goldencheck fix data.csv                          # safe mode, writes data_fixed.csv
goldencheck fix data.csv --mode moderate          # moderate fixes
goldencheck fix data.csv --mode aggressive --force # aggressive, requires --force
goldencheck fix data.csv -o cleaned.csv           # custom output path
goldencheck fix data.csv --dry-run                # show what would change, don't write
```

**Note:** The `fix` subcommand is registered via `@app.command()` and does not need special handling in the hand-rolled fallback arg parser in `main()` — the fallback only routes to `scan`.

### Three Modes

| Mode | Fixes | Risk |
|------|-------|------|
| `safe` (default) | Trim whitespace, remove zero-width/invisible chars, normalize Unicode NFC, fix encoding (Latin-1→UTF-8, smart quotes→straight) | Zero data loss |
| `moderate` | Safe + standardize enum case (match dominant casing), standardize date formats (to ISO 8601), strip control chars | Minimal — only changes formatting |
| `aggressive` | Moderate + coerce types (string→numeric where detected), drop rows failing validation rules, fill nulls with mode/median. Requires `--force` flag | Data modification — must opt in |

### Architecture

- **New file:** `goldencheck/engine/fixer.py`
- **Entry point:** `apply_fixes(df: pl.DataFrame, findings: list[Finding], mode: str, *, force: bool = False) -> tuple[pl.DataFrame, FixReport]`
- `apply_fixes` raises `ValueError` if `mode="aggressive"` and `force=False` — the CLI maps `--force` to this parameter, but programmatic callers must also opt in explicitly
- **FixReport dataclass:** tracks per-column changes (column, fix_type, rows_affected, sample_before, sample_after)
- Each fix type is a small, testable function (e.g., `_trim_whitespace`, `_remove_invisible_chars`, `_standardize_case`)
- Fix functions receive a Polars Series and return a new Series (immutable pattern)

### Output

- Default output path: `{stem}_fixed{ext}` (e.g., `data.csv` → `data_fixed.csv`)
- Writes same format as input (CSV→CSV, Parquet→Parquet). For Excel input, writes CSV and prints a warning: "Note: Excel input converted to CSV output (single sheet)"
- `--dry-run` runs the same fix logic and prints the summary table but does not write any file
- If no fixes are applied, prints "No issues found — file is clean" and exits without writing an output file
- Never overwrites the input file

---

## 2. Friendly CLI Error Messages

### Problem

Raw Python tracebacks on bad input (missing files, wrong format, parse errors). Users see 20-line stacktraces instead of one-line guidance.

### Solution

A `_cli_error_handler` context manager in `cli/main.py` that wraps all command entry points:

```python
import polars as pl
from contextlib import contextmanager

@contextmanager
def _cli_error_handler():
    try:
        yield
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except PermissionError as e:
        typer.echo(f"Error: Permission denied: {e}", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except pl.exceptions.ComputeError as e:
        typer.echo(f"Error: Could not parse file. {e}", err=True)
        raise typer.Exit(code=1)
```

### Error Messages

| Error | Message |
|-------|---------|
| File not found | `Error: File not found: foo.csv` |
| Permission denied | `Error: Permission denied: foo.csv` |
| Unsupported format | `Error: Unsupported format '.txt'. Supported: .csv, .parquet, .xlsx, .xls` |
| CSV parse error | `Error: Could not parse file. <polars detail>` |
| Password-protected Excel | `Error: Cannot read password-protected Excel file` |

Applied to: `_do_scan`, `scan`, `validate`, `review`, `learn`, `fix`.

---

## 3. Public API Surface

### Purpose

Define what's public so users and type checkers know what to rely on. Groundwork for 1.0 stability freeze matching GoldenMatch's pattern.

### New Files

- `goldencheck/py.typed` — empty PEP 561 marker

### `__all__` Exports

| Module | Exports |
|--------|---------|
| `goldencheck/__init__.py` | `scan_file`, `scan_file_with_llm`, `Finding`, `Severity`, `DatasetProfile`, `ColumnProfile`, `ScanResult`, `__version__` |
| `goldencheck/models/finding.py` | `Finding`, `Severity` |
| `goldencheck/models/profile.py` | `ColumnProfile`, `DatasetProfile` |
| `goldencheck/engine/scanner.py` | `scan_file`, `scan_file_with_llm` |
| `goldencheck/engine/confidence.py` | `apply_confidence_downgrade`, `apply_corroboration_boost` |
| `goldencheck/notebook.py` | `ScanResult`, `findings_to_html`, `profile_to_html` |
| `goldencheck/config/schema.py` | `GoldenCheckConfig`, `ColumnRule`, `Settings` |

### Top-Level Convenience Imports

```python
# goldencheck/__init__.py
from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile
from goldencheck.notebook import ScanResult
```

Users can write `from goldencheck import scan_file, Finding` instead of deep imports.

### Version Consolidation

`cli/main.py` currently defines its own `__version__ = "0.3.0"`. Change it to import from `goldencheck.__init__` instead: `from goldencheck import __version__`. Single source of truth.

---

## 4. Coverage Badge + CI Improvements

### Changes to `.github/workflows/test.yml`

Add two new jobs alongside the existing test matrix:

**Coverage job:**
- Runs on Python 3.12 only
- `pytest --cov=goldencheck --cov-report=xml`
- Uploads to Codecov via `codecov/codecov-action@v4`

**Smoke test job:**
- Installs from built wheel (not editable)
- Runs `goldencheck tests/fixtures/simple.csv --no-tui`
- Verifies the installed package works end-to-end

### README Badge

Add Codecov badge after CI badge:
```
[![codecov](https://codecov.io/gh/benzsevern/goldencheck/graph/badge.svg)](https://codecov.io/gh/benzsevern/goldencheck)
```

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| `fix` command | Unit tests per fix function + integration tests for each mode, `--dry-run` output, `-o` custom path, `--force` gating, and messy.csv fixture |
| Error messages | CLI tests: invoke with bad paths/formats/permissions, assert exit code 1 and no traceback in output |
| `__all__` exports | Test that `from goldencheck import X` works for all public exports |
| Coverage | Verified by the CI job itself |

## Non-Goals

- No `docs/api-stability.md` yet — that comes at 1.0
- No progress bars — separate enhancement
- No `--separator` / `--quote-char` flags — just better error messages suggesting them
- No changes to TUI — separate workstream

## Version

Ship as v0.4.0. v0.3.0 is already released.
