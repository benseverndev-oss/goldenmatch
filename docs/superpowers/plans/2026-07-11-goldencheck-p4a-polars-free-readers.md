# GoldenCheck P4a (polars-free `read_columns` readers) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a polars-free `read_columns(path) -> dict[str, list]` (Parquet via pyarrow, Excel via openpyxl, CSV via Polars-or-clean-decline) + a `scan_file_columns(path)` entry, so a polars-absent install can scan Parquet/Excel through the existing `scan_columns`. Additive, non-breaking (P4b does the dep flip).

**Architecture:** `read_columns` reads typed columns without Polars for Parquet/Excel; the byte-identity gate is `scan_columns(read_columns(f))` == the covered profilers re-run over `PolarsFrame(read_file(f))`. Excel must reproduce `pl.read_excel`'s per-column dtype coercion (empirically pinned).

**Tech Stack:** Python 3.11+, pyarrow (`[parquet]` extra), openpyxl (base dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-p4a-polars-free-readers-design.md`

---

## Conventions (worktree `gc-p4a`, branch `feat/goldencheck-p4a-polars-free-readers`, off fresh main-with-S2)

**Python test preamble** (run from `/d/show_case/gc-p4a`):
```bash
export PYTHONPATH="D:/show_case/gc-p4a/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENCHECK_NATIVE=auto
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # under gc-p4a
$PY -c "import pyarrow, openpyxl, polars; print('deps ok')"  # all present in the dev venv
```
The dev venv has polars+pyarrow+openpyxl, so parity tests (which compare against `pl.read_*`) RUN. Ruff is 100-char.

**Native note:** the covered scan's hard profilers (`_HARD_PROFILERS`, temporal) need `goldencheck._native`. If it's built in the shared venv the parity covers them; if not, the mechanical-3 parity still holds and the hard-3/temporal are `native_enabled`-gated (same skip discipline as S2.2/S2.3). Build it if needed per the S2.2 plan's native-build steps (toolchain `PATH=/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin`, `.dll`->`.pyd` copy).

**INVARIANTS:**
- Byte-identical: `scan_columns(read_columns(f))` == covered profilers over `PolarsFrame(read_file(f))`, for Parquet + XLSX (incl. a mixed-type Excel column). `Finding` is a `@dataclass` (`==`).
- `read_columns` for Parquet + Excel imports ZERO polars (subprocess-verified). CSV needs polars (clean `ImportError` when absent).
- `read_file`, `scan_file`, `scan_dataframe` UNCHANGED. Existing tests pass unedited. `import goldencheck` loads zero polars.
- Commit per task; do NOT push (PR at the end).

**Base facts:** `engine/reader.py` has `read_file(path)->pl.DataFrame` (lazy-polars) + `SUPPORTED_EXTENSIONS={.csv,.parquet,.xlsx,.xls}`. `scan_columns(dict)->list[Finding]` in `engine/scanner.py` (exported). `PyColumn.dtype` infers from first non-null Python value. pyarrow is only in `[native]` extra today.

---

## Task 1: `_read_parquet_columns` + `[parquet]` extra

**Files:** Modify `engine/reader.py`, `packages/python/goldencheck/pyproject.toml`; Test `tests/engine/test_read_columns.py` (new).

- [ ] **Step 1: Failing test** (new `tests/engine/test_read_columns.py`). Build a parquet fixture in a tmp dir with pyarrow and assert `_read_parquet_columns` == `pl.read_parquet(path).to_dict(as_series=False)`:
```python
from __future__ import annotations
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from datetime import date, datetime
from goldencheck.engine.reader import _read_parquet_columns


def _write_parquet(tmp_path):
    import pyarrow as pa
    tbl = pa.table({
        "i": [1, 2, None, 4],
        "f": [1.5, 2.5, 3.5, None],
        "s": ["a", "b", None, "d"],
        "b": [True, False, True, None],
        "d": [date(2021, 1, 5), date(2021, 2, 6), None, date(2021, 3, 7)],
    })
    p = tmp_path / "f.parquet"
    pq.write_table(tbl, p)
    return p


def test_read_parquet_columns_matches_polars(tmp_path):
    p = _write_parquet(tmp_path)
    got = _read_parquet_columns(p)
    exp = pl.read_parquet(p).to_dict(as_series=False)
    assert got == exp
```

- [ ] **Step 2: Run → FAIL** (`ImportError: cannot import name '_read_parquet_columns'`):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_read_columns.py -k parquet -v
```

- [ ] **Step 3: Implement `_read_parquet_columns` in `engine/reader.py`** (add near `read_file`):
```python
def _read_parquet_columns(path: Path) -> dict[str, list]:
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError(
            "Reading Parquet without Polars needs pyarrow: pip install goldencheck[parquet]"
        ) from e
    return pq.read_table(str(path)).to_pydict()
```
IMPORTANT: verify `to_pydict()` == `pl.read_parquet().to_dict(as_series=False)` for the fixture. If a type differs (e.g. pyarrow returns a different scalar for date/datetime), report it — do NOT loosen the test; narrow the fixture to goldencheck's scanned scalar types (int/float/str/bool/date/datetime/None) and document the excluded type as an edge.

- [ ] **Step 4: Add the `[parquet]` extra** to `pyproject.toml` `[project.optional-dependencies]`:
```toml
parquet = ["pyarrow>=14"]
```
(Same pin as `[native]`. Do NOT touch the base `polars>=1.0` dep — that's P4b.)

- [ ] **Step 5: Run → PASS** + import gate:
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_read_columns.py -k parquet packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean on reader.py + the test.

- [ ] **Step 6: Commit.**
```bash
cd /d/show_case/gc-p4a
git add packages/python/goldencheck/goldencheck/engine/reader.py packages/python/goldencheck/pyproject.toml packages/python/goldencheck/tests/engine/test_read_columns.py
git commit -m "feat(goldencheck): P4a _read_parquet_columns (pyarrow, polars-free) + [parquet] extra"
```

---

## Task 2: `_read_excel_columns` (openpyxl + pl.read_excel column coercion, empirically pinned)

This is the hard task — reproduce `pl.read_excel(engine="openpyxl")`'s per-column dtype coercion.

**Files:** Modify `engine/reader.py`; Test `tests/engine/test_read_columns.py`.

- [ ] **Step 1: EMPIRICALLY pin `pl.read_excel` behavior first (investigation, no commit).** Write a throwaway script that builds an xlsx (openpyxl) with these columns and prints `pl.read_excel(path, engine="openpyxl")`'s dtype + `.to_dict(as_series=False)` for each:
  - `homog_int`: `[1, 2, None, 4]`
  - `homog_float`: `[1.5, 2.5, None]`
  - `homog_str`: `["a", "b", None]`
  - `int_float_mix`: `[1, 2.5, 3]`
  - `str_num_mix`: `["N/A", 1, 2]`
  - `homog_date`: `[date(2021,1,5), None]`
  - `all_none`: `[None, None]`
```bash
$PY - <<'PYEOF'
# build xlsx with openpyxl, read with pl.read_excel(engine="openpyxl"), print dtype+values per column
PYEOF
```
Record the EXACT dtype + values Polars produces for `int_float_mix`, `str_num_mix`, `all_none` (the coercion cases). Expected (VERIFY, don't assume): `int_float_mix` → Float64 `[1.0, 2.5, 3.0]`; `str_num_mix` → String `["N/A", "1", "2"]`; `all_none` → Null. **Use the observed reality**, not this guess, to write `_coerce_column` in Step 3.

- [ ] **Step 2: Write the failing parity test** for Excel (append to `test_read_columns.py`). Build an xlsx fixture (openpyxl) covering homogeneous + the two mixed cases, and assert `_read_excel_columns(path)` == `pl.read_excel(path, engine="openpyxl").to_dict(as_series=False)`:
```python
def _write_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["homog_int", "homog_str", "int_float_mix", "str_num_mix"])
    ws.append([1, "a", 1, "N/A"])
    ws.append([2, "b", 2.5, 1])
    ws.append([None, None, 3, 2])
    p = tmp_path / "f.xlsx"; wb.save(p); return p


def test_read_excel_columns_matches_polars(tmp_path):
    p = _write_xlsx(tmp_path)
    got = _read_excel_columns(p)
    exp = pl.read_excel(p, engine="openpyxl").to_dict(as_series=False)
    assert got == exp
```
Run → FAIL (no `_read_excel_columns`).

- [ ] **Step 3: Implement `_read_excel_columns` + `_coerce_column` in `engine/reader.py`** to match the Step-1 observations. Skeleton (fill coercion from the EMPIRICAL results):
```python
def _read_excel_columns(path: Path) -> dict[str, list]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]                       # pl.read_excel reads the FIRST sheet, not wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header = list(next(rows))
    except StopIteration:
        return {}
    raw: dict[str, list] = {h: [] for h in header}
    for row in rows:
        for i, h in enumerate(header):
            raw[h].append(row[i] if i < len(row) else None)
    wb.close()
    return {h: _coerce_column(vals) for h, vals in raw.items()}


def _coerce_column(vals: list) -> list:
    """Reproduce pl.read_excel(engine='openpyxl')'s per-column dtype coercion from the
    raw openpyxl cell values (empirically pinned in the test, Step 1). Non-null value
    types decide the column type: any str -> stringify all non-null; else int+float mix
    -> float; homogeneous -> unchanged; all-null -> unchanged (Polars Null == PyColumn
    'other'). None passes through as None."""
    non_null = [v for v in vals if v is not None]
    if not non_null:
        return list(vals)
    types = {type(v) for v in non_null}
    # bool is a subtype of int -- keep bools as-is (Polars Boolean)
    if str in types:
        return [None if v is None else str(v) for v in vals]
    if float in types and int in (t for t in types if t is not bool):
        return [None if v is None else float(v) for v in vals]
    return list(vals)
```
ADJUST this to EXACTLY match the Step-1 observations — especially the `str(v)` formatting of numbers (does Polars give `"1"` or `"1.0"` for an int in a str column? does it give `"2.5"` for a float? confirm from Step 1) and the int+float→float rule. If Polars' stringification of a specific value can't be reproduced by `str(v)`, that value is a documented edge — decline (raise a clear `ValueError` naming the column) rather than silently diverge; do NOT loosen the test.

- [ ] **Step 4: Run → PASS** (Excel parity, incl. mixed columns) + import gate:
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_read_columns.py -k excel packages/python/goldencheck/tests/test_import_no_polars.py -v
```
If a mixed-column value diverges, fix `_coerce_column` to match Polars (or clean-decline that case) — never loosen. Ruff clean.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/engine/reader.py packages/python/goldencheck/tests/engine/test_read_columns.py
git commit -m "feat(goldencheck): P4a _read_excel_columns (openpyxl, polars-free) with pl.read_excel column coercion"
```

---

## Task 3: `_read_csv_columns` + `read_columns` dispatcher + guards

**Files:** Modify `engine/reader.py`; Test `tests/engine/test_read_columns.py`.

- [ ] **Step 1: Failing tests** (append). CSV reads via polars when present; `read_columns` dispatches + guards:
```python
def test_read_csv_columns_matches_read_file(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("i,s\n1,a\n2,b\n,c\n", encoding="utf-8")
    from goldencheck.engine.reader import _read_csv_columns, read_file
    got = _read_csv_columns(p)
    exp = read_file(p).to_dict(as_series=False)   # read_file is the pl.read_csv path
    assert got == exp


def test_read_columns_dispatch_and_guards(tmp_path):
    from goldencheck.engine.reader import read_columns
    with pytest.raises(ValueError, match="Unsupported"):
        read_columns(tmp_path / "x.json")
    with pytest.raises(FileNotFoundError):
        read_columns(tmp_path / "missing.csv")
    empty = tmp_path / "e.csv"; empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no data"):
        read_columns(empty)
```
Run → FAIL.

- [ ] **Step 2: Implement `_read_csv_columns` + `read_columns`** in `engine/reader.py`:
```python
def _read_csv_columns(path: Path) -> dict[str, list]:
    try:
        import polars  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Reading CSV requires Polars (its dtype inference is not reproducible without "
            "it). Install goldencheck[polars], or use a Parquet/Excel source."
        ) from e
    from goldencheck._polars_lazy import pl
    try:
        df = pl.read_csv(path, infer_schema_length=10000)
    except Exception:
        df = pl.read_csv(path, infer_schema_length=10000, encoding="latin-1")
    return df.to_dict(as_series=False)


def read_columns(path: Path) -> dict[str, list]:
    """Polars-free typed read into columns for scan_columns(). Parquet (pyarrow) and
    Excel (openpyxl) read without Polars; CSV needs Polars. See P4a spec."""
    path = Path(path).resolve()
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path.name}")
    logger.info("Reading %s (%s) [columns]", path.name, ext)
    if path.stat().st_size == 0:
        raise ValueError("File has no data rows. Nothing to profile.")
    if ext == ".parquet":
        return _read_parquet_columns(path)
    if ext in (".xlsx", ".xls"):
        return _read_excel_columns(path)
    if ext == ".csv":
        return _read_csv_columns(path)
    raise ValueError(f"Unsupported file format: {ext}")
```
Note the empty-CSV guard: `read_file` treats size 0 as "no data" — a header-only CSV (size>0) is NOT empty; keep parity with `read_file`'s guard (size==0 only).

- [ ] **Step 3: Run → PASS** + import gate + existing reader tests unedited:
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_read_columns.py packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -k "reader" -v   # existing reader tests UNEDITED
```
Ruff clean.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/engine/reader.py packages/python/goldencheck/tests/engine/test_read_columns.py
git commit -m "feat(goldencheck): P4a _read_csv_columns (polars-or-decline) + read_columns dispatcher"
```

---

## Task 4: `scan_file_columns` + exports + byte-parity gate + polars-free proof + final verification

**Files:** Modify `engine/scanner.py`, `goldencheck/__init__.py`; Test `tests/engine/test_scan_file_columns_parity.py` (new), `tests/engine/test_read_columns.py`.

- [ ] **Step 1: Write the byte-parity gate** `tests/engine/test_scan_file_columns_parity.py`:
```python
"""P4a byte-identity gate: scan_columns(read_columns(f)) == the covered profilers re-run
over PolarsFrame(read_file(f)), for Parquet + XLSX. Proves the polars-free read+scan
matches the Polars read path for the checks scan_columns runs."""
from __future__ import annotations
import polars as pl
import pytest
from goldencheck import scan_file_columns
from goldencheck.core._native_loader import native_enabled
from goldencheck.core.frame import PolarsFrame
from goldencheck.engine.reader import read_file
from goldencheck.engine.scanner import _HARD_PROFILERS, _MECHANICAL_PROFILERS
from goldencheck.relations.temporal import TemporalOrderProfiler
# reuse the fixtures from test_read_columns
from goldencheck.tests.engine.test_read_columns import _write_parquet, _write_xlsx  # or inline copies


def _expected_covered(path):
    pol = PolarsFrame(read_file(path))
    cols = read_file(path).columns
    expected = []
    for name in cols:
        for profiler in _MECHANICAL_PROFILERS:
            expected.extend(profiler.profile(pol, name))
        if native_enabled("regex"):
            for profiler in _HARD_PROFILERS:
                expected.extend(profiler.profile(pol, name))
    if native_enabled("str_to_date"):
        expected.extend(TemporalOrderProfiler().profile(pol))
    return expected


def test_scan_file_columns_parquet_parity(tmp_path):
    p = _write_parquet(tmp_path)
    assert scan_file_columns(p) == _expected_covered(p)


def test_scan_file_columns_xlsx_parity(tmp_path):
    p = _write_xlsx(tmp_path)
    assert scan_file_columns(p) == _expected_covered(p)
```
(If importing fixtures from the test module is awkward, inline `_write_parquet`/`_write_xlsx` here. NOTE the profiler-order in `_expected_covered` MUST match `scan_columns`' order: per-column mechanical then hard, then temporal once at the end — mirror `scan_columns` in scanner.py exactly.)

- [ ] **Step 2: Run → FAIL** (`ImportError: cannot import name 'scan_file_columns'`).

- [ ] **Step 3: Add `scan_file_columns` to `scanner.py`** (near `scan_columns`; `read_columns` import from reader):
```python
from goldencheck.engine.reader import read_columns  # add to existing reader import if present

def scan_file_columns(path: Path) -> list[Finding]:
    """Polars-free file scan: read a file into columns (Parquet/Excel without Polars;
    CSV needs Polars) and run the covered structural checks via scan_columns(). For the
    full scan (classification, sampling, denial, Polars-only relation checks) use
    scan_file()."""
    return scan_columns(read_columns(path))
```
Add `"scan_file_columns"` to scanner `__all__`. Export `scan_file_columns` + `read_columns` from `goldencheck/__init__.py` (import line + `__all__`).

- [ ] **Step 4: Run → PASS** (parity for parquet + xlsx) + import gate:
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_scan_file_columns_parity.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
If parity fails on xlsx, the Excel coercion (Task 2) diverges on a covered finding — fix `_coerce_column`, don't loosen. Ruff clean.

- [ ] **Step 5: Add the polars-free proof** (append to `test_read_columns.py`): a subprocess that blocks `polars` via a `sys.meta_path` finder and asserts `read_columns(parquet)` + `read_columns(xlsx)` succeed with `"polars" not in sys.modules`, and `read_columns(csv)` raises ImportError:
```python
def test_read_columns_parquet_excel_are_polars_free(tmp_path):
    import subprocess, sys, os, textwrap
    p_pq = _write_parquet(tmp_path); p_xl = _write_xlsx(tmp_path)
    csv = tmp_path / "c.csv"; csv.write_text("a\n1\n", encoding="utf-8")
    code = textwrap.dedent(f"""
        import sys, importlib.abc
        class _B(importlib.abc.MetaPathFinder):
            def find_spec(self, n, path=None, target=None):
                if n=='polars' or n.startswith('polars.'):
                    raise ModuleNotFoundError(n)
                return None
        sys.meta_path.insert(0, _B())
        from goldencheck.engine.reader import read_columns
        assert read_columns(r{str(p_pq)!r})
        assert read_columns(r{str(p_xl)!r})
        assert 'polars' not in sys.modules, sorted(m for m in sys.modules if 'polar' in m)
        try:
            read_columns(r{str(csv)!r}); raise SystemExit('csv should have raised')
        except ImportError:
            pass
    """)
    pkg = str(__import__('pathlib').Path(__file__).resolve().parents[1])
    env = dict(os.environ); env['PYTHONPATH'] = pkg + os.pathsep + env.get('PYTHONPATH','')
    env['POLARS_SKIP_CPU_CHECK'] = '1'
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 6: Final verification.**
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_read_columns.py packages/python/goldencheck/tests/engine/test_scan_file_columns_parity.py packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -k "reader or scanner" -v    # existing tests UNEDITED
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Report exact pass/skip counts; confirm the polars-free subprocess proof passed (Parquet+Excel read with polars blocked); confirm existing reader/scanner tests green unedited. Do NOT run the full suite (OOM).

- [ ] **Step 7: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/engine/scanner.py packages/python/goldencheck/goldencheck/__init__.py packages/python/goldencheck/tests/engine/
git commit -m "feat(goldencheck): P4a scan_file_columns + byte-parity gate + polars-free read proof"
```

---

## Done criteria (P4a complete)
- [ ] `read_columns(path)` reads Parquet (pyarrow) + Excel (openpyxl) into `dict[str,list]` with ZERO polars (subprocess-proven); CSV cleanly declines when polars absent, matches `read_file` when present.
- [ ] `_read_excel_columns` reproduces `pl.read_excel`'s per-column coercion (empirically pinned; mixed str/number + int/float columns match or cleanly decline).
- [ ] `scan_file_columns(path) = scan_columns(read_columns(path))`, exported; byte-parity vs the covered profilers over `read_file(f)` for Parquet + XLSX.
- [ ] `[parquet]` extra added; base `polars` dep UNCHANGED (P4b flips it); `read_file`/`scan_file` unchanged; existing tests unedited; `import goldencheck` loads zero polars.
- [ ] NO dep flip, NO version bump, NO nopolars-required lane (all P4b).
