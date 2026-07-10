# GoldenCheck Stage-2 S2.1 (pure-Python PyColumn/PyFrame backend + scan_columns) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first non-Polars backend — a pure-Python `PyColumn`/`PyFrame` — so nullability/cardinality/uniqueness run byte-identically without Polars, plus a public `scan_columns(dict)` entry + byte-parity/nopolars tests.

**Architecture:** `PyColumn` wraps a `list`, `PyFrame` a `dict[str,list]`, implementing the 7 mechanical ops the 3 covered profilers use. `to_frame` is reordered so a `PyFrame` never touches the `pl` symbol. `scan_columns` runs the 3 covered profilers over a `PyFrame`. A byte-parity test (PolarsFrame vs PyFrame) is the byte-identity gate; the nopolars lane runs `scan_columns` polars-free.

**Tech Stack:** Python 3.13 (stdlib only for the backend), pytest.

**Spec:** `docs/superpowers/specs/2026-07-10-goldencheck-stage2-s2.1-pycolumn-backend-design.md`

---

## PRE-FLIGHT (do this BEFORE Task 1 — the branch must be on main-with-S2.0)

The S2.1 code MODIFIES S2.0's `tests/nopolars/test_polars_absent.py` (Task 3), so the branch must sit on a `main` that already has S2.0 (#1618). At the start of execution:
```bash
cd /d/show_case/gc-s21
git fetch origin main -q
# Confirm S2.0 landed (tests/nopolars/ + the lane exist on main):
git show origin/main:packages/python/goldencheck/tests/nopolars/test_polars_absent.py >/dev/null 2>&1 && echo "S2.0 present on main" || echo "S2.0 NOT on main yet -- WAIT, do not proceed"
```
If S2.0 is present, rebase this branch onto fresh main (it currently carries only the spec + this plan, so the rebase is trivial):
```bash
git rebase origin/main
```
If S2.0 is NOT yet on main, STOP and wait — do not start Task 1 (Task 3 would create/conflict on `tests/nopolars/`).

## Conventions (this plan runs in the `gc-s21` worktree)

Branch `feat/goldencheck-stage2-s2.1-pycolumn-backend`, worktree `D:\show_case\gc-s21`.

**Test preamble** (run every test command from `/d/show_case/gc-s21`):
```bash
export PYTHONPATH="D:/show_case/gc-s21/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-s21
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical covered profilers (PyFrame == PolarsFrame). `scan_dataframe` (the polars path) is UNCHANGED. The full suite + import gate stay green; `import goldencheck` loads zero Polars. `Finding` is a plain `@dataclass` — compare `Finding`/`list[Finding]` with `==` directly (no normalized-tuple fallback needed). Commit per task; do NOT push.

**Current seam** (`core/frame.py`): `Column`/`Frame` Protocols + `PolarsColumn`/`PolarsFrame`; `to_frame` currently does `isinstance(native, PolarsFrame)` then `isinstance(native, pl.DataFrame)` (the latter TOUCHES `pl`). No `PyColumn`/`PyFrame` yet.

---

## Task 1: `PyColumn` / `PyFrame` backend + `to_frame` reorder

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_pycolumn_mechanical_ops():
    from goldencheck.core.frame import PyColumn
    c = PyColumn([3, 1, None, 1, 2])
    assert len(c) == 5
    assert c.null_count() == 1
    assert c.n_unique() == 4                       # {3,1,None,2}
    assert c.drop_nulls().to_list() == [3, 1, 1, 2]
    assert c.drop_nulls().unique().sort().to_list() == [1, 2, 3]
    assert c.to_list() == [3, 1, None, 1, 2]

def test_pyframe_surface_and_from_columns():
    from goldencheck.core.frame import PyFrame, to_frame
    f = PyFrame.from_columns({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    assert f.columns == ["a", "b"]
    assert f.height == 3
    assert f.native == {"a": [1, 2, 3], "b": ["x", "y", "z"]}
    assert f.column("a").to_list() == [1, 2, 3]
    # to_frame is idempotent on a PyFrame and does NOT require polars
    assert to_frame(f) is f
    # empty frame
    assert PyFrame.from_columns({}).height == 0
    assert PyFrame.from_columns({}).columns == []

def test_to_frame_pyframe_is_polars_free():
    # Building/using a PyFrame must not load polars.
    import subprocess, sys, os
    from pathlib import Path
    code = (
        "import sys, importlib.abc\n"
        "class _B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, n, path=None, target=None):\n"
        "        if n=='polars' or n.startswith('polars.'):\n"
        "            raise ModuleNotFoundError(n)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _B())\n"
        "from goldencheck.core.frame import PyFrame, to_frame\n"
        "f = to_frame(PyFrame.from_columns({'a':[1,None,2]}))\n"
        "assert f.column('a').null_count()==1\n"
        "assert 'polars' not in sys.modules\n"
    )
    pkg = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ); env["PYTHONPATH"] = pkg + os.pathsep + env.get("PYTHONPATH","")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2: Run → FAIL** (`ImportError: cannot import name 'PyColumn'`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "pycolumn or pyframe or to_frame_pyframe" -v
```

- [ ] **Step 3: Implement in `core/frame.py`.** Add `PyColumn` + `PyFrame` (place them ABOVE `to_frame`, after `PolarsFrame`). Each op returns a `PyColumn` where the Protocol says `-> Column` (so chaining works):
```python
class PyColumn:
    __slots__ = ("_v",)

    def __init__(self, values: list) -> None:
        self._v = values

    def __len__(self) -> int:
        return len(self._v)

    def null_count(self) -> int:
        return sum(1 for v in self._v if v is None)

    def n_unique(self) -> int:
        return len(set(self._v))

    def drop_nulls(self) -> PyColumn:
        return PyColumn([v for v in self._v if v is not None])

    def unique(self) -> PyColumn:
        return PyColumn(list(set(self._v)))

    def sort(self) -> PyColumn:
        return PyColumn(sorted(self._v))

    def to_list(self) -> list:
        return list(self._v)


class PyFrame:
    __slots__ = ("_cols",)

    def __init__(self, cols: dict[str, list]) -> None:
        self._cols = cols

    @classmethod
    def from_columns(cls, cols: dict[str, list]) -> PyFrame:
        return cls(cols)

    @property
    def columns(self) -> list[str]:
        return list(self._cols.keys())

    @property
    def height(self) -> int:
        return len(next(iter(self._cols.values()))) if self._cols else 0

    @property
    def native(self) -> Any:
        return self._cols

    def column(self, name: str) -> PyColumn:
        return PyColumn(self._cols[name])
```
Then **reorder `to_frame`** so the frame fast-paths come first (NO `pl` access for a PyFrame):
```python
def to_frame(native: Any) -> Frame:
    if isinstance(native, (PolarsFrame, PyFrame)):
        return native
    if isinstance(native, pl.DataFrame):
        return PolarsFrame(native)
    raise TypeError(
        f"to_frame() expects a polars.DataFrame, PolarsFrame, or PyFrame; got {type(native)!r}"
    )
```
(`PyColumn` implements only the 7 mechanical ops — it deliberately does NOT satisfy the full `Column` Protocol; that's fine, the 3 covered profilers use only these 7. Do NOT add the other ops — YAGNI/out-of-scope.)

- [ ] **Step 4: Run → PASS**, import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-s21
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): S2.1 pure-Python PyColumn/PyFrame backend + polars-free to_frame reorder"
```

---

## Task 2: `scan_columns` + export + byte-parity test

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/engine/scanner.py`
- Modify: `packages/python/goldencheck/goldencheck/__init__.py`
- Test: `packages/python/goldencheck/tests/engine/test_scan_columns_parity.py` (new)

- [ ] **Step 1: Add `scan_columns` to `scanner.py`.** Near `scan_dataframe`, add a covered-profiler list + the entry (import `PyFrame` from `core.frame`; the 3 profiler classes are already imported in scanner.py):
```python
from goldencheck.core.frame import PyFrame  # add to the existing core.frame import if present

_COVERED_COLUMN_PROFILERS = [NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()]


def scan_columns(columns: dict[str, list]) -> list[Finding]:
    """Polars-free reduced scan of the covered STRUCTURAL checks (nullability,
    uniqueness, cardinality) over in-memory columns. The regex/format/encoding/
    date/value-count checks need Polars -- use scan_dataframe for a full scan."""
    frame = PyFrame.from_columns(columns)
    findings: list[Finding] = []
    for name in columns:
        for profiler in _COVERED_COLUMN_PROFILERS:
            findings.extend(profiler.profile(frame, name))
    return findings
```
(If scanner.py already `from goldencheck.core.frame import to_frame`, extend that line to also import `PyFrame`. `Finding` is already imported there.)

- [ ] **Step 2: Export from `__init__.py`.** Add `scan_columns` to the `from goldencheck.engine.scanner import ...` line and to `__all__`.

- [ ] **Step 3: Write the byte-parity test** `tests/engine/test_scan_columns_parity.py`:
```python
"""S2.1 byte-identity gate: the covered profilers produce identical Findings on the
pure-Python PyFrame backend and the Polars PolarsFrame backend (run with polars
present). This proves scan_columns(dict) == the Polars covered-check output, so the
polars-absent nopolars-lane literals are trustworthy."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck import scan_columns
from goldencheck.core.frame import PolarsFrame, PyFrame
from goldencheck.engine.scanner import _COVERED_COLUMN_PROFILERS
from goldencheck.profilers.cardinality import CardinalityProfiler
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.profilers.uniqueness import UniquenessProfiler

# Data exercising each covered finding branch. NOTE: floats are NaN-FREE on purpose --
# PyColumn.sort/n_unique assume no NaN (Polars sorts NaN last; Python does not). Do NOT
# add NaN to any column here.
def _datasets():
    return [
        {"pk": list(range(120)),                                   # 100% unique -> PK finding
         "grade": ["A", "B", "C"] * 40,                            # low cardinality enum
         "note": [None] * 120,                                     # entirely null
         "score": [float(i % 7) for i in range(120)]},             # clean floats, low card
        {"user_id": [1, 1, 2, 3] * 30,                             # identifier w/ dups (near-unique? no)
         "email": [f"u{i}" for i in range(120)],                   # 100% unique non-id
         "opt": ([1] * 114) + [None] * 6},                         # ~5% nulls in sizeable col
        {"x": [1, 2, 3]},                                          # tiny frame (<10, <50) -> few/no findings
    ]


@pytest.mark.parametrize("data", _datasets())
def test_covered_profilers_backend_parity(data):
    pol = PolarsFrame(pl.DataFrame(data))
    pyf = PyFrame.from_columns(data)
    for profiler in (NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()):
        for col in data:
            assert profiler.profile(pol, col) == profiler.profile(pyf, col), (profiler, col)


@pytest.mark.parametrize("data", _datasets())
def test_scan_columns_matches_polars_covered_output(data):
    pol = PolarsFrame(pl.DataFrame(data))
    expected = []
    for name in data:
        for profiler in _COVERED_COLUMN_PROFILERS:
            expected.extend(profiler.profile(pol, name))
    assert scan_columns(data) == expected
```

- [ ] **Step 4: Run → PASS** + import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_scan_columns_parity.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
If a parity assertion FAILS, a PyColumn op diverges from Polars on that data — fix the PORT/backend (or narrow the data if it's a genuine unsupported edge like NaN), never loosen the assertion. Ruff clean on the 3 files.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/engine/scanner.py packages/python/goldencheck/goldencheck/__init__.py packages/python/goldencheck/tests/engine/test_scan_columns_parity.py
git commit -m "feat(goldencheck): S2.1 public scan_columns() -- polars-free covered structural scan"
```

---

## Task 3: nopolars covered-scan assertions + final verification

**Files:**
- Modify: `packages/python/goldencheck/tests/nopolars/test_polars_absent.py` (S2.0's module)
- Modify: `packages/python/goldencheck/tests/test_import_no_polars.py` (extend the import-blocker)

- [ ] **Step 1: Add a covered-scan test to `tests/nopolars/test_polars_absent.py`** (append; the module is `skipif`'d when polars is present, so this runs only in the `goldencheck_nopolars` lane):
```python
def test_covered_scan_columns_without_polars() -> None:
    from goldencheck import scan_columns

    findings = scan_columns({
        "pk": list(range(120)),
        "grade": ["A", "B", "C"] * 40,
        "note": [None] * 120,
    })
    checks = sorted({f.check for f in findings})
    # covered structural checks fire; nothing polars-only ran
    assert "uniqueness" in checks      # pk is 100% unique
    assert "cardinality" in checks     # grade is low-cardinality
    assert "nullability" in checks     # note is entirely null
    assert "polars" not in sys.modules
```
(Use the exact literal checks the S2.1 byte-parity test validated. If you want value-level literal asserts, copy expected Finding fields from a parity-test run — but the `check`-set + polars-not-loaded assertions are sufficient and robust.)

- [ ] **Step 2: Extend the import-blocker** in `tests/test_import_no_polars.py` — add a test proving `scan_columns` runs end-to-end with polars unimportable (REQUIRED suite):
```python
def test_scan_columns_runs_with_polars_unimportable():
    code = (
        "import sys, importlib.abc\n"
        "class _B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, n, path=None, target=None):\n"
        "        if n=='polars' or n.startswith('polars.'):\n"
        "            raise ModuleNotFoundError(n)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _B())\n"
        "from goldencheck import scan_columns\n"
        "fs = scan_columns({'pk': list(range(120)), 'note': [None]*120})\n"
        "checks = {f.check for f in fs}\n"
        "assert 'uniqueness' in checks and 'nullability' in checks, checks\n"
        "assert 'polars' not in sys.modules\n"
    )
    pkg_dir = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 3: Run the affected tests + verify polars-free:**
```bash
cd /d/show_case/gc-s21 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py packages/python/goldencheck/tests/nopolars -v
```
Expected: import gate tests pass (incl. the new scan_columns-blocked test); nopolars 3 skipped (polars present locally). Confirm `scan_columns`'s covered path is polars-free:
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/core/frame.py | grep -iE "PyColumn|PyFrame" || echo "(PyColumn/PyFrame reference no pl -- check by reading: their bodies use only stdlib)"
```

- [ ] **Step 4: Final verification (whole batch).**
```bash
cd /d/show_case/gc-s21 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate + 2 new blocker tests green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (report exact passed/skipped counts vs baseline + the new tests); ruff clean. `scan_dataframe` behavior unchanged (no test regressions).

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/tests/nopolars/test_polars_absent.py packages/python/goldencheck/tests/test_import_no_polars.py
git commit -m "test(goldencheck): S2.1 covered-scan proof -- scan_columns runs polars-free (lane + blocker)"
```

---

## Done criteria (S2.1 complete)
- [ ] `PyColumn`/`PyFrame` (7 mechanical ops) in `core/frame.py`; `to_frame` reordered so a `PyFrame` never touches `pl` (import gate green).
- [ ] Public `scan_columns(dict) -> list[Finding]` runs the 3 covered profilers on a `PyFrame`; exported from `__init__.py`.
- [ ] Byte-parity test proves the 3 covered profilers produce identical Findings on `PyFrame` vs `PolarsFrame`, and `scan_columns(d)` == the Polars covered output.
- [ ] The nopolars lane runs a REAL covered scan (`scan_columns`) polars-free; the import-blocker proves it in the required suite.
- [ ] Full suite green; `scan_dataframe` unchanged; `import goldencheck` loads zero Polars.
- [ ] No scope creep: no dtype/stats/regex/date ops on PyColumn, no `scan_dataframe` wiring, no reader, no deps-flip. S2.2 (new Rust kernels for the hard ops) is next.
