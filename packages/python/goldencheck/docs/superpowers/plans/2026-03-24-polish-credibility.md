# Polish & Credibility Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v0.4.0 with auto-fix, friendly errors, public API surface, and CI coverage.

**Architecture:** Four independent components. The fixer is a new module (`engine/fixer.py`) with per-fix functions that receive/return Polars Series. Error handling is a context manager wrapping all CLI commands. API surface is `__all__` + `py.typed` + top-level re-exports. CI adds coverage + smoke jobs.

**Tech Stack:** Python 3.11+, Polars, Typer, pytest, pytest-cov, Codecov, ruff

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `goldencheck/engine/fixer.py` | Fix functions + `apply_fixes()` + `FixReport` dataclass |
| Create | `goldencheck/py.typed` | PEP 561 marker (empty file) |
| Create | `tests/engine/test_fixer.py` | Unit + integration tests for fixer |
| Create | `tests/test_public_api.py` | Verify `__all__` exports and top-level imports |
| Modify | `goldencheck/__init__.py` | Add `__all__`, convenience re-exports |
| Modify | `goldencheck/cli/main.py` | Add `fix` command, `_cli_error_handler`, import `__version__` |
| Modify | `goldencheck/models/finding.py` | Add `__all__` |
| Modify | `goldencheck/models/profile.py` | Add `__all__` |
| Modify | `goldencheck/engine/scanner.py` | Add `__all__` |
| Modify | `goldencheck/engine/confidence.py` | Add `__all__` |
| Modify | `goldencheck/notebook.py` | Add `__all__` |
| Modify | `goldencheck/config/schema.py` | Add `__all__` |
| Modify | `.github/workflows/test.yml` | Add coverage + smoke jobs |
| Modify | `README.md` | Add codecov badge |
| Modify | `tests/cli/test_cli.py` | Add error message tests + fix command tests |
| Modify | `pyproject.toml` | Bump version to 0.4.0 |

---

### Task 1: Safe Fix Functions (fixer.py core)

**Files:**
- Create: `goldencheck/engine/fixer.py`
- Create: `tests/engine/test_fixer.py`

- [ ] **Step 1: Write failing tests for safe-mode fix functions**

```python
# tests/engine/test_fixer.py
"""Tests for the fixer module."""
import polars as pl
import pytest
from goldencheck.engine.fixer import (
    FixReport,
    FixEntry,
    apply_fixes,
    _trim_whitespace,
    _remove_invisible_chars,
    _normalize_unicode,
    _fix_smart_quotes,
)


def test_trim_whitespace():
    s = pl.Series("col", ["  hello ", "world  ", " foo "])
    result = _trim_whitespace(s)
    assert result.to_list() == ["hello", "world", "foo"]


def test_trim_whitespace_no_change():
    s = pl.Series("col", ["hello", "world"])
    result = _trim_whitespace(s)
    assert result.to_list() == ["hello", "world"]


def test_remove_invisible_chars():
    s = pl.Series("col", ["hel\u200blo", "wor\uFEFFld", "normal"])
    result = _remove_invisible_chars(s)
    assert result.to_list() == ["hello", "world", "normal"]


def test_normalize_unicode():
    # NFC normalization: combining accent → precomposed
    s = pl.Series("col", ["cafe\u0301", "normal"])
    result = _normalize_unicode(s)
    assert result.to_list() == ["caf\u00e9", "normal"]


def test_fix_smart_quotes():
    s = pl.Series("col", ["\u201chello\u201d", "\u2018world\u2019"])
    result = _fix_smart_quotes(s)
    assert result.to_list() == ['"hello"', "'world'"]


def test_apply_fixes_safe_mode():
    df = pl.DataFrame({"name": ["  Alice ", "Bob\u200b"], "age": [25, 30]})
    findings = []
    result_df, report = apply_fixes(df, findings, mode="safe")
    assert result_df["name"].to_list() == ["Alice", "Bob"]
    assert len(report.entries) > 0


def test_apply_fixes_no_changes():
    df = pl.DataFrame({"name": ["Alice", "Bob"], "age": [25, 30]})
    findings = []
    result_df, report = apply_fixes(df, findings, mode="safe")
    assert len(report.entries) == 0


def test_apply_fixes_aggressive_requires_force():
    df = pl.DataFrame({"name": ["Alice"]})
    with pytest.raises(ValueError, match="aggressive"):
        apply_fixes(df, [], mode="aggressive", force=False)


def test_apply_fixes_aggressive_with_force():
    df = pl.DataFrame({"name": ["Alice"]})
    result_df, report = apply_fixes(df, [], mode="aggressive", force=True)
    assert isinstance(report, FixReport)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_fixer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'goldencheck.engine.fixer'`

- [ ] **Step 3: Implement fixer.py with safe-mode functions**

```python
# goldencheck/engine/fixer.py
"""Auto-fix engine — applies automated data quality fixes."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import polars as pl

from goldencheck.models.finding import Finding


@dataclass
class FixEntry:
    column: str
    fix_type: str
    rows_affected: int
    sample_before: list[str] = field(default_factory=list)
    sample_after: list[str] = field(default_factory=list)


@dataclass
class FixReport:
    entries: list[FixEntry] = field(default_factory=list)

    @property
    def total_rows_fixed(self) -> int:
        return sum(e.rows_affected for e in self.entries)


# ---------------------------------------------------------------------------
# Individual fix functions — receive Series, return Series (immutable)
# ---------------------------------------------------------------------------

_INVISIBLE_CHARS = re.compile(r"[\u200b\u200c\u200d\uFEFF\u00AD\u2060]")

_SMART_QUOTES = {
    "\u201c": '"', "\u201d": '"',  # left/right double
    "\u2018": "'", "\u2019": "'",  # left/right single
    "\u2013": "-", "\u2014": "-",  # en-dash, em-dash
    "\u2026": "...",               # ellipsis
}


def _trim_whitespace(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.str.strip_chars()


def _remove_invisible_chars(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.map_elements(
        lambda v: _INVISIBLE_CHARS.sub("", v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _normalize_unicode(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    return s.map_elements(
        lambda v: unicodedata.normalize("NFC", v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _fix_smart_quotes(s: pl.Series) -> pl.Series:
    if s.dtype not in (pl.Utf8, pl.String):
        return s

    def _replace(v):
        if not isinstance(v, str):
            return v
        for old, new in _SMART_QUOTES.items():
            v = v.replace(old, new)
        return v

    return s.map_elements(_replace, return_dtype=pl.String)


def _standardize_case(s: pl.Series, findings: list[Finding], column: str) -> pl.Series:
    """Match dominant casing for low-cardinality columns."""
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    non_null = s.drop_nulls()
    if len(non_null) == 0 or non_null.n_unique() > 20:
        return s
    # Find dominant case for each lowered value
    lowered = non_null.str.to_lowercase()
    pairs = pl.DataFrame({"original": non_null, "lowered": lowered})
    dominant = (
        pairs.group_by("lowered")
        .agg(pl.col("original").mode().first().alias("dominant"))
    )
    mapping = dict(zip(dominant["lowered"].to_list(), dominant["dominant"].to_list()))
    return s.map_elements(
        lambda v: mapping.get(v.lower(), v) if isinstance(v, str) else v,
        return_dtype=pl.String,
    )


def _coerce_numeric(s: pl.Series) -> pl.Series:
    """Attempt to cast string column to numeric."""
    if s.dtype not in (pl.Utf8, pl.String):
        return s
    try:
        return s.cast(pl.Float64, strict=False)
    except Exception:
        return s


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_SAFE_FIXES = [
    ("trim_whitespace", _trim_whitespace),
    ("remove_invisible_chars", _remove_invisible_chars),
    ("normalize_unicode", _normalize_unicode),
    ("fix_smart_quotes", _fix_smart_quotes),
]


def apply_fixes(
    df: pl.DataFrame,
    findings: list[Finding],
    mode: str = "safe",
    *,
    force: bool = False,
) -> tuple[pl.DataFrame, FixReport]:
    """Apply fixes to a DataFrame. Returns (fixed_df, report)."""
    if mode == "aggressive" and not force:
        raise ValueError(
            "Aggressive mode modifies data (drops rows, coerces types). "
            "Pass force=True or use --force on the CLI to confirm."
        )

    report = FixReport()
    result = df.clone()

    for col_name in result.columns:
        col = result[col_name]

        # Safe fixes (always run)
        for fix_name, fix_fn in _SAFE_FIXES:
            fixed = fix_fn(col)
            changed = (col.cast(pl.String).fill_null("") != fixed.cast(pl.String).fill_null(""))
            n_changed = int(changed.sum())
            if n_changed > 0:
                before = col.filter(changed).head(3).cast(pl.String).to_list()
                after = fixed.filter(changed).head(3).cast(pl.String).to_list()
                report.entries.append(FixEntry(
                    column=col_name,
                    fix_type=fix_name,
                    rows_affected=n_changed,
                    sample_before=[str(v) for v in before],
                    sample_after=[str(v) for v in after],
                ))
                result = result.with_columns(fixed.alias(col_name))
                col = result[col_name]

        # Moderate fixes
        if mode in ("moderate", "aggressive"):
            fixed = _standardize_case(col, findings, col_name)
            changed = (col.cast(pl.String).fill_null("") != fixed.cast(pl.String).fill_null(""))
            n_changed = int(changed.sum())
            if n_changed > 0:
                before = col.filter(changed).head(3).cast(pl.String).to_list()
                after = fixed.filter(changed).head(3).cast(pl.String).to_list()
                report.entries.append(FixEntry(
                    column=col_name,
                    fix_type="standardize_case",
                    rows_affected=n_changed,
                    sample_before=[str(v) for v in before],
                    sample_after=[str(v) for v in after],
                ))
                result = result.with_columns(fixed.alias(col_name))
                col = result[col_name]

        # Aggressive fixes
        if mode == "aggressive":
            fixed = _coerce_numeric(col)
            if fixed.dtype != col.dtype:
                report.entries.append(FixEntry(
                    column=col_name,
                    fix_type="coerce_numeric",
                    rows_affected=len(col),
                ))
                result = result.with_columns(fixed.alias(col_name))

    return result, report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/engine/test_fixer.py -v`
Expected: All PASS

- [ ] **Step 5: Lint**

Run: `ruff check goldencheck/engine/fixer.py tests/engine/test_fixer.py`
Expected: All checks passed

- [ ] **Step 6: Commit**

```bash
git add goldencheck/engine/fixer.py tests/engine/test_fixer.py
git commit -m "feat: add fixer module with safe/moderate/aggressive fix functions"
```

---

### Task 2: Friendly Error Handler

> **Note:** This must come before the `fix` CLI command (Task 3) because `fix` uses `_cli_error_handler`.

**Files:**
- Modify: `goldencheck/cli/main.py`
- Modify: `tests/cli/test_cli.py`

- [ ] **Step 1: Write failing tests for error handling**

```python
# Add to tests/cli/test_cli.py

def test_error_file_not_found():
    result = runner.invoke(app, ["scan", "nonexistent_file.csv", "--no-tui"])
    assert result.exit_code == 1
    assert "Error:" in result.stdout or "Error:" in (result.stderr or "")
    assert "Traceback" not in result.stdout


def test_error_unsupported_format(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("hello")
    result = runner.invoke(app, ["scan", str(f), "--no-tui"])
    assert result.exit_code == 1
    assert "Unsupported" in result.stdout or "Unsupported" in (result.stderr or "")
    assert "Traceback" not in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_cli.py::test_error_file_not_found -v`
Expected: FAIL — Traceback in output

- [ ] **Step 3: Add `_cli_error_handler` to cli/main.py**

Add after the imports, before the `_DefaultCommandGroup` class:

```python
import polars as pl
from contextlib import contextmanager


@contextmanager
def _cli_error_handler():
    """Catch common errors and print friendly messages instead of tracebacks."""
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

Then wrap `_do_scan`, `validate`, `review`, and `learn` bodies with `with _cli_error_handler():`.

> **Note on reader.py:** `read_file()` raises `ValueError` for unsupported formats, `FileNotFoundError` for missing files, and `ValueError` for password-protected Excel. All are caught by `_cli_error_handler`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cli/test_cli.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add goldencheck/cli/main.py tests/cli/test_cli.py
git commit -m "feat: add friendly CLI error messages — no more raw tracebacks"
```

---

### Task 3: `fix` CLI Command

> **Depends on:** Task 2 (`_cli_error_handler` must exist)

**Files:**
- Modify: `goldencheck/cli/main.py`
- Modify: `tests/cli/test_cli.py`

- [ ] **Step 1: Write failing test for the fix CLI command**

```python
# Add to tests/cli/test_cli.py

def test_fix_safe_mode(tmp_path):
    """Test fix command in safe mode with a file that has whitespace issues."""
    csv = tmp_path / "dirty.csv"
    csv.write_text("name,age\n  Alice ,25\nBob  ,30\n")
    result = runner.invoke(app, ["fix", str(csv), "--dry-run"])
    assert result.exit_code == 0
    assert "trim_whitespace" in result.stdout or "clean" in result.stdout


def test_fix_aggressive_requires_force(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\n")
    result = runner.invoke(app, ["fix", str(csv), "--mode", "aggressive"])
    assert result.exit_code == 1
    assert "force" in result.stdout.lower() or "aggressive" in result.stdout.lower()


def test_fix_output_path(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\n  Alice ,25\n")
    out = tmp_path / "cleaned.csv"
    result = runner.invoke(app, ["fix", str(csv), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_fix_no_changes(tmp_path):
    csv = tmp_path / "clean.csv"
    csv.write_text("name,age\nAlice,25\nBob,30\n")
    result = runner.invoke(app, ["fix", str(csv)])
    assert result.exit_code == 0
    assert "clean" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_cli.py::test_fix_safe_mode -v`
Expected: FAIL — no `fix` command registered

- [ ] **Step 3: Add fix command to cli/main.py**

Add after the `learn` command, before `mcp_serve`:

```python
@app.command()
def fix(
    file: Path = typer.Argument(..., help="Data file to fix."),
    mode: str = typer.Option("safe", "--mode", "-m", help="Fix mode: safe, moderate, or aggressive."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show fixes without writing."),
    force: bool = typer.Option(False, "--force", help="Required for aggressive mode."),
) -> None:
    """Apply automated fixes to a data file.

    Modes:
      safe       — trim whitespace, remove invisible chars, normalize Unicode (default)
      moderate   — safe + standardize enum case, fix smart quotes
      aggressive — moderate + coerce types, drop bad rows (requires --force)
    """
    from goldencheck.engine.fixer import apply_fixes
    from goldencheck.engine.reader import read_file
    from goldencheck.engine.scanner import scan_file as _scan

    with _cli_error_handler():
        df = read_file(file)
        findings, _ = _scan(file)

        try:
            fixed_df, report = apply_fixes(df, findings, mode=mode, force=force)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

        if not report.entries:
            typer.echo("No issues found — file is clean.")
            raise typer.Exit(code=0)

        # Print summary
        typer.echo(f"\nFixes applied ({mode} mode):")
        for entry in report.entries:
            typer.echo(
                f"  {entry.column}: {entry.fix_type} ({entry.rows_affected} rows)"
            )
        typer.echo(f"\nTotal: {report.total_rows_fixed} row-fixes across {len(report.entries)} operations")

        if dry_run:
            typer.echo("\n--dry-run: No file written.")
            raise typer.Exit(code=0)

        # Determine output path — never overwrite input
        out_path = output or Path(f"{file.stem}_fixed{file.suffix}")
        if out_path.resolve() == Path(file).resolve():
            typer.echo("Error: Output path is the same as input. Use -o to specify a different path.", err=True)
            raise typer.Exit(code=1)

        ext = file.suffix.lower()
        if ext == ".parquet":
            fixed_df.write_parquet(out_path)
        elif ext in (".xlsx", ".xls"):
            csv_out = out_path.with_suffix(".csv")
            fixed_df.write_csv(csv_out)
            typer.echo("Note: Excel input converted to CSV output (single sheet)")
            out_path = csv_out
        else:
            fixed_df.write_csv(out_path)

        typer.echo(f"Written to: {out_path}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cli/test_cli.py -v`
Expected: All PASS (including new fix tests)

- [ ] **Step 5: Run full test suite**

Run: `pytest --tb=short -v`
Expected: All 166+ tests PASS

- [ ] **Step 6: Commit**

```bash
git add goldencheck/cli/main.py tests/cli/test_cli.py
git commit -m "feat: add goldencheck fix command with safe/moderate/aggressive modes"
```

---

### Task 4: Public API Surface (`__all__` + `py.typed`)

**Files:**
- Create: `goldencheck/py.typed`
- Create: `tests/test_public_api.py`
- Modify: `goldencheck/__init__.py`
- Modify: `goldencheck/models/finding.py`
- Modify: `goldencheck/models/profile.py`
- Modify: `goldencheck/engine/scanner.py`
- Modify: `goldencheck/engine/confidence.py`
- Modify: `goldencheck/notebook.py`
- Modify: `goldencheck/config/schema.py`
- Modify: `goldencheck/cli/main.py`

- [ ] **Step 1: Write failing test for public API imports**

```python
# tests/test_public_api.py
"""Test that the public API surface works as documented."""


def test_top_level_imports():
    from goldencheck import (
        scan_file,
        scan_file_with_llm,
        Finding,
        Severity,
        DatasetProfile,
        ColumnProfile,
        ScanResult,
        __version__,
    )
    assert callable(scan_file)
    assert callable(scan_file_with_llm)
    assert __version__


def test_finding_all():
    from goldencheck.models.finding import __all__ as finding_all
    assert "Finding" in finding_all
    assert "Severity" in finding_all


def test_profile_all():
    from goldencheck.models.profile import __all__ as profile_all
    assert "ColumnProfile" in profile_all
    assert "DatasetProfile" in profile_all


def test_scanner_all():
    from goldencheck.engine.scanner import __all__ as scanner_all
    assert "scan_file" in scanner_all
    assert "scan_file_with_llm" in scanner_all


def test_config_all():
    from goldencheck.config.schema import __all__ as config_all
    assert "GoldenCheckConfig" in config_all
    assert "ColumnRule" in config_all
    assert "Settings" in config_all
    assert "RelationRule" in config_all
    assert "IgnoreEntry" in config_all
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_public_api.py -v`
Expected: FAIL — `ImportError` for top-level imports and missing `__all__`

- [ ] **Step 3: Add `__all__` to all public modules**

```python
# goldencheck/models/finding.py — add after imports
__all__ = ["Finding", "Severity"]

# goldencheck/models/profile.py — add after imports
__all__ = ["ColumnProfile", "DatasetProfile"]

# goldencheck/engine/scanner.py — add after imports
__all__ = ["scan_file", "scan_file_with_llm"]

# goldencheck/engine/confidence.py — add after imports
__all__ = ["apply_confidence_downgrade", "apply_corroboration_boost"]

# goldencheck/notebook.py — add after imports
__all__ = ["ScanResult", "findings_to_html", "profile_to_html"]

# goldencheck/config/schema.py — add after imports
__all__ = ["GoldenCheckConfig", "ColumnRule", "Settings", "RelationRule", "IgnoreEntry"]
```

- [ ] **Step 4: Update `goldencheck/__init__.py` with re-exports and `__all__`**

```python
"""GoldenCheck — data validation that discovers rules from your data."""

__version__ = "0.3.0"

from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile
from goldencheck.notebook import ScanResult

__all__ = [
    "scan_file",
    "scan_file_with_llm",
    "Finding",
    "Severity",
    "DatasetProfile",
    "ColumnProfile",
    "ScanResult",
    "__version__",
]
```

- [ ] **Step 5: Consolidate `__version__` in cli/main.py**

Change line 16 of `goldencheck/cli/main.py` from:
```python
__version__ = "0.3.0"
```
to:
```python
from goldencheck import __version__
```

- [ ] **Step 6: Create `goldencheck/py.typed`**

Create an empty file at `goldencheck/py.typed` (PEP 561 marker).

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_public_api.py -v`
Expected: All PASS

- [ ] **Step 8: Run full test suite**

Run: `pytest --tb=short -q`
Expected: All pass

- [ ] **Step 9: Lint**

Run: `ruff check goldencheck/ --exclude "*.yaml"`
Expected: All checks passed

- [ ] **Step 10: Commit**

```bash
git add goldencheck/py.typed goldencheck/__init__.py goldencheck/cli/main.py \
  goldencheck/models/finding.py goldencheck/models/profile.py \
  goldencheck/engine/scanner.py goldencheck/engine/confidence.py \
  goldencheck/notebook.py goldencheck/config/schema.py \
  tests/test_public_api.py
git commit -m "feat: define public API surface — __all__, py.typed, top-level re-exports"
```

---

### Task 5: CI Coverage + Smoke Test

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `README.md`

- [ ] **Step 1: Update test.yml with coverage and smoke jobs**

Replace the entire file with:

```yaml
name: Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short -v
      - run: ruff check .

  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest --cov=goldencheck --cov-report=xml --tb=short
      - uses: codecov/codecov-action@v4
        with:
          file: coverage.xml
          fail_ci_if_error: false

  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install .
      - run: goldencheck tests/fixtures/simple.csv --no-tui
      - run: goldencheck --version
```

- [ ] **Step 2: Add codecov badge to README.md**

After the CI badge line, add:
```
[![codecov](https://codecov.io/gh/benzsevern/goldencheck/graph/badge.svg)](https://codecov.io/gh/benzsevern/goldencheck)
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml README.md
git commit -m "ci: add coverage reporting (Codecov) and smoke test job"
```

---

### Task 6: Version Bump + Final Validation

**Files:**
- Modify: `pyproject.toml`
- Modify: `goldencheck/__init__.py`
- Modify: `tests/cli/test_cli.py`

- [ ] **Step 1: Bump version to 0.4.0**

In `pyproject.toml`: change `version = "0.3.0"` → `version = "0.4.0"`
In `goldencheck/__init__.py`: change `__version__ = "0.3.0"` → `__version__ = "0.4.0"`
In `tests/cli/test_cli.py`: change `assert "0.3.0"` → `assert "0.4.0"`

- [ ] **Step 2: Run full test suite**

Run: `pytest --tb=short -v`
Expected: All pass

- [ ] **Step 3: Lint entire project**

Run: `ruff check goldencheck/ --exclude "*.yaml"`
Expected: All checks passed

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml goldencheck/__init__.py tests/cli/test_cli.py
git commit -m "chore: bump version to 0.4.0"
```

- [ ] **Step 5: Push**

```bash
gh auth switch --user benzsevern
git push
```

---

## Deferred Items

The following spec items are implemented as stubs in v0.4.0 and will be fully built out in a future release:

- **Moderate mode:** Date standardization to ISO 8601, control character stripping (currently only case standardization is implemented)
- **Aggressive mode:** Drop rows failing validation, fill nulls with mode/median (currently only type coercion is implemented)

These are noted here to maintain spec traceability. The core safe-mode fixes and the mode framework are complete.
