# GoldenCheck Polars Eviction — P0 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `import goldencheck` load zero Polars (lazy-import linchpin) and introduce a `Frame`/`Column` seam that `scanner.py` + the profilers route through, with 3 profilers ported onto it — all byte-identical, no behavior change.

**Architecture:** Copy goldenflow's proven eviction P0: a `_LazyPolars` proxy so `import polars as pl` becomes lazy everywhere, plus deferring the 7 module-level dtype-tuple constants that would trigger an eager import. Then a minimal `goldencheck/core/frame.py` seam (`Frame`/`Column` Protocols + a single `PolarsColumn` backend) that the scan path uses instead of raw `pl.DataFrame`; the 3 simplest profilers migrate onto it while the other ~19 `profile()` methods reach through `frame.native` unchanged.

**Tech Stack:** Python 3.13, Polars (still a hard dep in P0 — the *runtime* eviction is later stages), pytest.

**Spec:** `docs/superpowers/specs/2026-07-08-goldencheck-polars-eviction-p0-design.md`

---

## Conventions (this plan runs in the `gc-polars-evict` worktree)

Branch `feat/goldencheck-polars-eviction-p0`, worktree `D:\show_case\gc-polars-evict`, off fresh `origin/main`. The repo-root `.venv` has `goldencheck` installed from the **main** tree, so tests MUST prepend the worktree package to `PYTHONPATH` (per `reference_py_worktree_test_native_skew`):

**Test preamble** (from `/d/show_case/gc-polars-evict`):
```bash
export PYTHONPATH="D:/show_case/gc-polars-evict/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck, pathlib; print(goldencheck.__file__)"   # MUST be under gc-polars-evict
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**Reference to mirror (read it):** `packages/python/goldenflow/goldenflow/_polars_lazy.py` (the proxy to copy) and `packages/python/goldenflow/goldenflow/engine/frame.py` (the Frame Protocol shape).

**Commit discipline:** conventional commits, one per task's final step. Do NOT push (a PR is a separate step after all tasks). All commits on `feat/goldencheck-polars-eviction-p0`.

**INVARIANT:** every change in P0 is byte-identical. The existing goldencheck test suite is the regression gate — it must stay green at every task boundary. No version bump.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `goldencheck/_polars_lazy.py` | Create | `_LazyPolars` proxy (copy goldenflow's) |
| ~49 modules `import polars as pl` | Modify | → `from goldencheck._polars_lazy import pl` |
| 7 modules with module-level dtype tuples | Modify | defer the constant so it's not evaluated at import |
| `goldencheck/core/frame.py` | Create | `Frame`/`Column` Protocols + `PolarsFrame`/`PolarsColumn` + `to_frame()` |
| `goldencheck/profilers/base.py` | Modify | `BaseProfiler.profile(df: pl.DataFrame …)` → `(frame: Frame …)` |
| `goldencheck/engine/scanner.py` | Modify | wrap sample in `to_frame()`, pass `Frame` to profiler fan-outs |
| 10 unported column profilers + 9 relation profilers | Modify | one-line `df = frame.native` shim |
| `profilers/{nullability,cardinality,uniqueness}.py` | Modify | port bodies onto the `Column` seam |
| `tests/core/test_frame.py`, `tests/test_import_no_polars.py` | Create | seam unit tests + import-graph gate |

---

## Task 1: The `_LazyPolars` proxy

**Files:** Create `packages/python/goldencheck/goldencheck/_polars_lazy.py`; Test `packages/python/goldencheck/tests/core/test_polars_lazy.py`

- [ ] **Step 1: Write the failing test.**
```python
# tests/core/test_polars_lazy.py
def test_lazy_proxy_returns_real_polars_objects():
    from goldencheck._polars_lazy import pl
    import polars as real_pl
    assert pl.DataFrame is real_pl.DataFrame          # attribute access returns the REAL class
    df = pl.DataFrame({"a": [1, 2]})
    assert isinstance(df, real_pl.DataFrame)          # isinstance works
    assert pl.Utf8 is real_pl.Utf8                    # dtype access works
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError: goldencheck._polars_lazy`).

- [ ] **Step 3: Create `_polars_lazy.py`** — copy `packages/python/goldenflow/goldenflow/_polars_lazy.py` VERBATIM, changing only the module docstring's `goldenflow`→`goldencheck` references. The class body (`_LazyPolars`, `__slots__=("_mod",)`, `__getattr__` importing polars on first access, `pl = _LazyPolars()`) is unchanged.

- [ ] **Step 4: Run → PASS.** Ruff clean.

- [ ] **Step 5: Commit.** `git add packages/python/goldencheck/goldencheck/_polars_lazy.py packages/python/goldencheck/tests/core/test_polars_lazy.py && git commit -m "feat(goldencheck): _LazyPolars proxy (lazy-import linchpin, copied from goldenflow)"`

---

## Task 2: Sweep the imports + defer the 7 module-level dtype constants

**Files:** Modify ~49 files (`import polars as pl` → `from goldencheck._polars_lazy import pl`); Modify the 7 dtype-constant modules; Create `tests/test_import_no_polars.py`

- [ ] **Step 1: Write the failing import-graph gate test.**
```python
# tests/test_import_no_polars.py  (the linchpin proof)
import subprocess, sys

def test_import_goldencheck_does_not_load_polars():
    # Fresh interpreter: import goldencheck, assert polars was never imported.
    code = "import goldencheck, sys; assert 'polars' not in sys.modules, sorted(m for m in sys.modules if 'polars' in m)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
```
(Run it via the worktree PY with PYTHONPATH set so the subprocess also sees the worktree — pass `env` with the same PYTHONPATH, or run from a shell that has it exported.)

- [ ] **Step 2: Run → FAIL** (polars IS loaded — 49 eager imports + 7 module-level dtype tuples).

- [ ] **Step 3a: Rewrite the import lines.** For every module under `goldencheck/goldencheck/**` that has `import polars as pl`, change it to `from goldencheck._polars_lazy import pl`. (Grep first: `grep -rl "^import polars as pl" packages/python/goldencheck/goldencheck`.) Confirm each has `from __future__ import annotations` at the top (the precondition — all do; the audit found zero default-arg dtypes). Do NOT change `tests/` files or the `_polars_lazy.py` proxy itself.

- [ ] **Step 3b: Defer the 7 module-level dtype-tuple constants.** These are evaluated at import time and would trigger the eager import. For EACH, convert the module-level `X = (pl.Utf8, …)` into a lazily-evaluated form and update its in-module references. The 7:
  - `profilers/sequence_detection.py:9` `INTEGER_DTYPES`
  - `profilers/range_distribution.py:9-12`
  - `profilers/drift_detection.py:9-12`
  - `relations/numeric_cross.py:8-11` `NUMERIC_DTYPES`
  - `relations/composite_key.py:36-41`
  - `relations/approx_fd.py:33-37`
  - `relations/functional_dependency.py:34-39` `_SUPPORTED`

  Pattern per constant (module-level tuple → module-level function + `@lru_cache`, references become a call):
  ```python
  # before:
  _SUPPORTED = (pl.Utf8, pl.Int8, pl.Int16, ...)
  # ... later ... if series.dtype in _SUPPORTED:
  # after:
  from functools import lru_cache
  @lru_cache(maxsize=1)
  def _supported() -> tuple:
      return (pl.Utf8, pl.Int8, pl.Int16, ...)
  # ... later ... if series.dtype in _supported():
  ```
  (Read each file to get the exact tuple + all reference sites; update every reference in that module. `lru_cache` keeps it a one-time cost, matching the constant's semantics.)

- [ ] **Step 4: Run the gate → PASS.** `'polars' not in sys.modules` after `import goldencheck`. Then run the FULL existing suite to confirm byte-identical behavior:
```bash
cd /d/show_case/gc-polars-evict && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -q    # full regression (may skip baseline/llm extras)
```
Expected: gate passes; full suite green (same pass/skip counts as before the sweep). Ruff clean.

- [ ] **Step 5: Commit.** `git add -A packages/python/goldencheck && git commit -m "refactor(goldencheck): lazy Polars imports + defer 7 module-level dtype constants (import loads no polars)"`
(Verify no stray artifact staged: `git status --short | grep -vE "\.py$"` should be empty-ish.)

---

## Task 3: The `Frame`/`Column` seam

**Files:** Create `packages/python/goldencheck/goldencheck/core/frame.py`; Test `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Read `scanner.py`** to confirm exactly which `Frame`-level ops the scan path needs (`columns`, `height`, indexing) so the `Frame` Protocol covers them. Also read `goldenflow/engine/frame.py` for the Protocol style.

- [ ] **Step 2: Write failing seam unit tests `tests/core/test_frame.py`.**
```python
import polars as pl
from goldencheck.core.frame import to_frame, Frame, Column

def _f():
    return to_frame(pl.DataFrame({"a": [1, 1, 2, None], "b": ["x", "y", "x", "z"]}))

def test_frame_basics():
    f = _f()
    assert set(f.columns) == {"a", "b"}
    assert f.height == 4
    assert f.native.shape == (4, 2)          # escape hatch = the pl.DataFrame

def test_column_reductions_match_polars():
    f = _f()
    a = f.column("a")
    assert len(a) == 4                        # __len__
    assert a.null_count() == 1
    assert a.drop_nulls().n_unique() == 2
    assert a.drop_nulls().unique().sort().to_list() == [1, 2]
    assert f.column("b").n_unique() == 3
```

- [ ] **Step 3: Run → FAIL.**

- [ ] **Step 4: Implement `core/frame.py`.** Minimal Protocols + one Polars backend. Note: profilers use Python `len(col)`, so `Column` implements `__len__` (NOT a `.len()` method). No `dtype` in P0 (no ported profiler uses it — add in a later stage). Chained methods return wrappers that delegate, so parity is by construction.
```python
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable
from goldencheck._polars_lazy import pl


@runtime_checkable
class Column(Protocol):
    def __len__(self) -> int: ...
    def null_count(self) -> int: ...
    def n_unique(self) -> int: ...
    def drop_nulls(self) -> "Column": ...
    def unique(self) -> "Column": ...
    def sort(self) -> "Column": ...
    def to_list(self) -> list: ...


@runtime_checkable
class Frame(Protocol):
    @property
    def columns(self) -> list[str]: ...
    @property
    def height(self) -> int: ...
    @property
    def native(self) -> Any: ...
    def column(self, name: str) -> Column: ...


class PolarsColumn:
    __slots__ = ("_s",)
    def __init__(self, s) -> None: self._s = s
    def __len__(self) -> int: return len(self._s)
    def null_count(self) -> int: return self._s.null_count()
    def n_unique(self) -> int: return self._s.n_unique()
    def drop_nulls(self) -> "PolarsColumn": return PolarsColumn(self._s.drop_nulls())
    def unique(self) -> "PolarsColumn": return PolarsColumn(self._s.unique())
    def sort(self) -> "PolarsColumn": return PolarsColumn(self._s.sort())
    def to_list(self) -> list: return self._s.to_list()


class PolarsFrame:
    __slots__ = ("_df",)
    def __init__(self, df) -> None: self._df = df
    @property
    def columns(self) -> list[str]: return self._df.columns
    @property
    def height(self) -> int: return self._df.height
    @property
    def native(self): return self._df
    def column(self, name: str) -> PolarsColumn: return PolarsColumn(self._df[name])


def to_frame(native) -> Frame:
    if isinstance(native, PolarsFrame):
        return native
    if isinstance(native, pl.DataFrame):
        return PolarsFrame(native)
    raise TypeError(f"to_frame() expects a polars.DataFrame (or PolarsFrame); got {type(native)!r}")
```
(Adjust `Frame` if Step 1 found scanner needs more, e.g. `dtype(name)` or row access — add only what's actually used.)

- [ ] **Step 5: Run → PASS.** Ruff clean.

- [ ] **Step 6: Commit.** `git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py && git commit -m "feat(goldencheck): Frame/Column seam with PolarsColumn backend (P0)"`

---

## Task 4: Route `scanner.py` + all `profile()` methods through `Frame`

**Files:** Modify `goldencheck/profilers/base.py`; Modify `goldencheck/engine/scanner.py`; Modify the 10 unported column profilers + 9 relation profilers (`df = frame.native` shim)

- [ ] **Step 1: Change the signatures (two passes).**
  - **Pass A — 13 column profilers (`BaseProfiler`):** in `profilers/base.py`, change `profile(self, df: pl.DataFrame, column, *, context=None)` → `profile(self, frame: Frame, column, *, context=None)` (import `Frame` from `goldencheck.core.frame`). In each of the 13 column profiler subclasses, change the signature the same way; for the 10 you are NOT porting in Task 5, add `df = frame.native` as the first body line (body unchanged). The 3 ported ones (nullability/cardinality/uniqueness) get their `frame:` signature here but are fully ported in Task 5 — for now give them `df = frame.native` too so this task stays byte-identical and self-contained.
  - **Pass B — 9 relation profilers (`relations/*.py`, NOT `BaseProfiler`):** signature is `profile(self, df: pl.DataFrame)` (no column/context). Change `df` → `frame`, add `df = frame.native`. All 9 stay on `.native` in P0.

- [ ] **Step 2: Wrap the sample in `scanner.py`.** Find where `scanner.py` holds the sampled `pl.DataFrame` (`sample: pl.DataFrame`, ~line 84) and where it calls `profiler.profile(sample, col, ...)` / `relation.profile(sample)`. Wrap once: `frame = to_frame(sample)` (import `to_frame`), and pass `frame` to both fan-outs. (If other call sites construct profilers — `engine/scanner.py` `scan_dataframe`, the LLM path — thread `to_frame` there too. Grep `.profile(` across `goldencheck/` to find every call site.)

- [ ] **Step 3: Run the FULL suite → byte-identical green.**
```bash
cd /d/show_case/gc-polars-evict && <preamble>
$PY -m pytest packages/python/goldencheck/tests -q
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v   # still green
```
Expected: same pass/skip counts as before (every profiler now takes a `Frame` but behaves identically via `.native`). Ruff clean.

- [ ] **Step 4: Commit.** `git add -A packages/python/goldencheck && git commit -m "refactor(goldencheck): route scanner + all profile() methods through the Frame seam (via .native)"`

---

## Task 5: Port `nullability`, `cardinality`, `uniqueness` onto the seam

**Files:** Modify `profilers/nullability.py`, `cardinality.py`, `uniqueness.py`

- [ ] **Step 1:** Port each body from `df[column]` → `frame.column(column)`, removing the `df = frame.native` shim (Task 4) and the now-unused `from goldencheck._polars_lazy import pl` import. The ops map 1:1:
  - `nullability.py`: `col = frame.column(column)`; `total = len(col)`; `null_count = col.null_count()` — rest unchanged.
  - `uniqueness.py`: `col = frame.column(column)`; `len(col)`; `non_null = col.drop_nulls()`; `len(non_null)`; `non_null.n_unique()` — rest unchanged.
  - `cardinality.py`: `col = frame.column(column)`; `len(col)`; `col.n_unique()`; `col.drop_nulls().unique().sort().to_list()` — rest unchanged.
  These profilers do NO dtype checks and had NO module-level `pl.` refs, so after porting they import no polars at all.

- [ ] **Step 2: Run the 3 profilers' existing tests UNCHANGED → PASS (the parity gate).**
```bash
cd /d/show_case/gc-polars-evict && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_nullability.py packages/python/goldencheck/tests/profilers/test_cardinality.py packages/python/goldencheck/tests/profilers/test_uniqueness.py -v
```
Expected: all pass with ZERO test edits (byte-identical Findings). If a test file name differs, find it (`ls tests/profilers | grep -E "null|cardinal|uniq"`). Then the full suite + import gate:
```bash
$PY -m pytest packages/python/goldencheck/tests -q
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
```

- [ ] **Step 3: Confirm the 3 ported files are polars-free.** `grep -l "polars\|_polars_lazy\| pl\." packages/python/goldencheck/goldencheck/profilers/{nullability,cardinality,uniqueness}.py` → no matches (they now go entirely through the seam). Ruff clean.

- [ ] **Step 4: Commit.** `git add packages/python/goldencheck/goldencheck/profilers/{nullability,cardinality,uniqueness}.py && git commit -m "refactor(goldencheck): port nullability/cardinality/uniqueness onto the Frame seam (polars-free)"`

---

## Done criteria (P0 complete)

- [ ] `import goldencheck` loads zero Polars (`tests/test_import_no_polars.py` green).
- [ ] `Frame`/`Column` seam exists (`core/frame.py`) with a `PolarsColumn` backend; `scanner.py` + all 22 `profile()` methods route through it.
- [ ] `nullability`/`cardinality`/`uniqueness` are ported onto the seam and import no polars; their existing tests pass with zero edits.
- [ ] Full existing goldencheck suite green (same pass/skip counts) — byte-identical, no version bump.
- [ ] No P1+ scope crept in (no reader eviction, no other profiler ports, no substrate/backend beyond PolarsColumn, no nopolars CI lane, no deps flip).
