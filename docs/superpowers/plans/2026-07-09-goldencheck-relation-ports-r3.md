# GoldenCheck Polars Eviction — Relation Ports R3 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `null_correlation` + `numeric_cross` + `temporal` off Polars onto the seam, adding 6 element-wise `Column` ops (`is_null`, `gt_mask`, `eq_mask`, `fill_null`, `sum`, `str_to_date`) — byte-identical, no version bump.

**Architecture:** These three "two-column element-wise" relation profilers fully seam-route (no native kernel, no parity-locked raw-df helpers). Add the six ops; the violation pattern becomes `a.gt_mask(b).fill_null(False)` → `sum()` + `filter_by`; null-correlation uses `is_null()` + `eq_mask()`; temporal uses `str_to_date`.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-relation-ports-r3-design.md`

---

## Conventions (this plan runs in the `gc-r3` worktree, off fresh origin/main through R2)

Branch `feat/goldencheck-relation-ports-r3`, worktree `D:\show_case\gc-r3`, off fresh `origin/main` (through R2 #1614 — the seam has `to_arrow`/`get`, `filter_by`, `eq`, `cast`, `dtype`, `drop_nulls`, `to_list`, `_neutral_dtype`). NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-r3`):
```bash
export PYTHONPATH="D:/show_case/gc-r3/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-r3
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. Parity gates pass UNEDITED: `tests/relations/test_{null_correlation,numeric_cross,temporal}.py` (import only the Profiler classes; numeric_cross also imports the pure-Python `_find_max_pairs`, which is NOT changed). The import gate + full suite stay green. No version bump. Do NOT add new test FILES (append to `test_frame.py`). Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `Column` has `filter_by(mask: Column)` (reaches `mask._s`), scalar `eq(value)`, `cast`, `dtype` (neutral string), `drop_nulls`, `to_list`, etc. — but NO `is_null`/`gt_mask`/`eq_mask`/`fill_null`/`sum`/`str_to_date`. `Frame` has `columns`/`height`/`native`/`column()`.

---

## Task 1: Add `is_null` + `gt_mask` + `eq_mask` + `fill_null` + `sum` + `str_to_date` to the seam

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_is_null_and_sum():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, None, 3, None]})).column("x")
    mask = col.is_null()
    assert mask.to_list() == [False, True, False, True]
    assert int(mask.sum()) == 2   # sum of a bool column == count of True

def test_column_gt_mask_and_eq_mask():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({"a": [5, 1, 9], "b": [3, 4, 9]}))
    a = frame.column("a"); b = frame.column("b")
    assert a.gt_mask(b).to_list() == (pl.Series([5, 1, 9]) > pl.Series([3, 4, 9])).to_list()
    assert a.gt_mask(b).to_list() == [True, False, False]
    assert a.eq_mask(b).to_list() == [False, False, True]

def test_column_fill_null():
    import polars as pl
    from goldencheck.core.frame import to_frame
    # a > comparison with a null operand yields null; fill_null(False) clears it
    frame = to_frame(pl.DataFrame({"a": [5, None, 9], "b": [3, 4, 9]}))
    a = frame.column("a"); b = frame.column("b")
    assert a.gt_mask(b).fill_null(False).to_list() == [True, False, False]

def test_column_str_to_date():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"d": ["2020-01-02", "not-a-date", "2021-12-31"]})).column("d")
    got = col.str_to_date("%Y-%m-%d", strict=False)
    assert got.to_list() == pl.Series(["2020-01-02", "not-a-date", "2021-12-31"]).str.to_date(
        format="%Y-%m-%d", strict=False
    ).to_list()
    # invalid parses -> null under strict=False
    assert got.to_list()[1] is None
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'is_null'`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "is_null_and_sum or gt_mask_and_eq_mask or fill_null or str_to_date" -v
```

- [ ] **Step 3: Implement.** Add to the `Column` Protocol (after the existing `get` line):
```python
    def is_null(self) -> Column: ...
    def gt_mask(self, other: Column) -> Column: ...
    def eq_mask(self, other: Column) -> Column: ...
    def fill_null(self, value: Any) -> Column: ...
    def sum(self) -> Any: ...
    def str_to_date(self, fmt: str, *, strict: bool) -> Column: ...
```
Add to `PolarsColumn` (after its existing `get` method):
```python
    def is_null(self) -> PolarsColumn:
        return PolarsColumn(self._s.is_null())

    def gt_mask(self, other: Column) -> PolarsColumn:
        return PolarsColumn(self._s > other._s)

    def eq_mask(self, other: Column) -> PolarsColumn:
        return PolarsColumn(self._s == other._s)

    def fill_null(self, value: Any) -> PolarsColumn:
        return PolarsColumn(self._s.fill_null(value))

    def sum(self) -> Any:
        return self._s.sum()

    def str_to_date(self, fmt: str, *, strict: bool) -> PolarsColumn:
        return PolarsColumn(self._s.str.to_date(format=fmt, strict=strict))
```
(`gt_mask`/`eq_mask` reach `other._s` — same pattern as `filter_by`. Everything delegates via `self._s.*`/`other._s` — no `pl.` symbol; import gate stays green. `Any` + `Column` already imported.)

- [ ] **Step 4: Run → PASS**, import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-r3
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add is_null/gt_mask/eq_mask/fill_null/sum/str_to_date to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port null_correlation + numeric_cross + temporal, then verify

**Files:** Modify (read EACH first):
- `packages/python/goldencheck/goldencheck/relations/null_correlation.py`
- `packages/python/goldencheck/goldencheck/relations/numeric_cross.py`
- `packages/python/goldencheck/goldencheck/relations/temporal.py`

**Shared for all 3:** remove `from goldencheck._polars_lazy import pl`; drop EVERY `pl.DataFrame`/`pl.Series` annotation (on `profile`, `_check_exceeds`, `_check_pair`, `_try_cast_to_date`, and the local `null_masks: dict[str, pl.Series]` annotation). Keep `to_frame`, `Finding`, `Severity`, and all pure-Python module bits. **Preserve every existing `try/except` block VERBATIM in structure** — only swap the op inside.

- [ ] **Step 1: Port `null_correlation.py`.**
  - `profile(self, frame)`: drop annotation; keep `frame = to_frame(frame)`; remove `df = frame.native`. `columns = frame.columns` (was `df.columns`); `n_rows = frame.height` (was `len(df)`). `if n_rows == 0 or len(columns) < 2: return findings` UNCHANGED.
  - `null_masks = {col: frame.column(col).is_null() for col in columns}` (was `df[col].is_null()`; DROP the `: dict[str, pl.Series]` annotation on this local).
  - `null_counts = {col: int(null_masks[col].sum()) for col in columns}` (DROP the `: dict[str, int]` annotation if present — harmless either way, but `pl` is gone so keep it clean). `null_masks[col].sum()` is now `Column.sum()`.
  - In the `combinations` pair loop: `mask_a = null_masks[col_a]`; `mask_b = null_masks[col_b]`; `agreement = int(mask_a.eq_mask(mask_b).sum())` (was `int((mask_a == mask_b).sum())`). All thresholds, the `_UnionFind` grouping, `high_pairs`/`low_pairs` tiers, and both Finding bodies UNCHANGED.

- [ ] **Step 2: Port `numeric_cross.py`.**
  - Delete the `@lru_cache _numeric_dtypes()` helper + `from functools import lru_cache`. Add module constant near `_MAX_PAIRS`: `_NUMERIC = frozenset({"int", "uint", "float"})`.
  - `profile(self, frame)`: drop annotation; `frame = to_frame(frame)`; remove `df = frame.native`. `max_pairs = _find_max_pairs(frame.columns)` (was `df.columns`). Loop: `result = self._check_exceeds(frame, value_col, max_col)` (was `(df, …)`).
  - `_check_exceeds(self, frame, value_col, max_col)`: drop annotation.
    - **Keep the `try/except Exception: return None`** around the two column pulls: `try: val_series = frame.column(value_col); max_series = frame.column(max_col) except Exception: return None` (was `df[value_col]`/`df[max_col]`).
    - Numeric gate: `if val_series.dtype not in _NUMERIC or max_series.dtype not in _NUMERIC:` (was `not in _numeric_dtypes()`).
    - **Keep the cast `try/except Exception: return None`** verbatim; inside it: `if val_series.dtype == "str": val_series = val_series.cast("float", strict=False)` (was `in (pl.Utf8, pl.String)` + `cast(pl.Float64, strict=False)`); same for `max_series`; then `if val_series.dtype not in _NUMERIC: return None` and `if max_series.dtype not in _NUMERIC: return None` (the original `_numeric_dtypes() + (pl.Float64,)` is redundant — Float64 ∈ `"float"` — so `_NUMERIC` is exact).
    - **OUTSIDE the cast try/except:** `violation_mask = val_series.gt_mask(max_series).fill_null(False)` (was `(val_series > max_series).fill_null(False)`); `violation_count = int(violation_mask.sum())`.
    - `if violation_count > 0:` block — `val_filtered = val_series.filter_by(violation_mask).to_list()[:3]` (was `.filter(violation_mask).head(3).to_list()`); `max_filtered = max_series.filter_by(violation_mask).to_list()[:3]`. The `zip`/`f"{v} exceeds {m}"` sample building + the ERROR Finding UNCHANGED.

- [ ] **Step 3: Port `temporal.py`.**
  - `_try_cast_to_date(col)`: drop `pl.Series` annotation. `if col.dtype == "str": return col.str_to_date("%Y-%m-%d", strict=False)` (was `if series.dtype == pl.Utf8 or series.dtype == pl.String: return series.str.to_date(format="%Y-%m-%d", strict=False)`); `return col` UNCHANGED.
  - `profile(self, frame)`: drop annotation; `frame = to_frame(frame)`; remove `df = frame.native`. `kw_pairs = _find_date_pairs(frame.columns)`. Date-col detection loop: `for col_name in frame.columns: col = frame.column(col_name); if col.dtype in ("date", "datetime"): date_cols.append(col_name) elif col.dtype == "str":` then **keep the existing `try/except`**: `try: casted = col.str_to_date("%Y-%m-%d", strict=False); if len(casted.drop_nulls()) > 0: date_cols.append(col_name) except Exception: pass` (was `s.str.to_date(...)` + `casted.drop_nulls().len()`). The `<= 6` guard + `from itertools import combinations` + the `_check_pair(frame, col_a, col_b, confidence=0.4)` calls UNCHANGED. The keyword-pair loop calls `self._check_pair(frame, start_col, end_col, confidence=0.9)`.
  - `_check_pair(self, frame, start_col, end_col, confidence)`: drop annotation. `start_series = frame.column(start_col)`; `end_series = frame.column(end_col)`. **Keep the `try: start_series = _try_cast_to_date(start_series); end_series = _try_cast_to_date(end_series) except Exception: return None`** (verbatim structure). `if start_series.dtype not in ("date", "datetime") or end_series.dtype not in ("date", "datetime"): return None`. `violation_mask = start_series.gt_mask(end_series).fill_null(False)`; `violation_count = violation_mask.sum()` (**RAW — no `int()`**, matching the original). `if violation_count > 0:` — `sample_starts = start_series.filter_by(violation_mask).cast("str").to_list()[:3]` (was `.filter(violation_mask).head(3).cast(pl.String).to_list()`); same for `sample_ends`. The `zip`/`f"{s} > {e}"` + the ERROR Finding (incl. `affected_rows=violation_count`) UNCHANGED.

- [ ] **Step 4: Run the parity gates UNEDITED.**
```bash
cd /d/show_case/gc-r3 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/relations/test_null_correlation.py packages/python/goldencheck/tests/relations/test_numeric_cross.py packages/python/goldencheck/tests/relations/test_temporal.py -v
```
Expected: all pass, ZERO test edits. If a test fails, fix the PORT (likely a `gt_mask`/`fill_null` order or a dtype-string), never the test; report the assertion diff.

- [ ] **Step 5: Confirm all 3 files polars-free.**
```bash
grep -REn "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/relations/null_correlation.py packages/python/goldencheck/goldencheck/relations/numeric_cross.py packages/python/goldencheck/goldencheck/relations/temporal.py || echo "all 3 polars-free"
```

- [ ] **Step 6: Final verification (whole batch).**
```bash
cd /d/show_case/gc-r3 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (report exact passed/skipped counts). If any FAILURE, investigate + report which test (do NOT edit tests to pass).

- [ ] **Step 7: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/relations/null_correlation.py packages/python/goldencheck/goldencheck/relations/numeric_cross.py packages/python/goldencheck/goldencheck/relations/temporal.py
git commit -m "refactor(goldencheck): port null_correlation/numeric_cross/temporal onto the Frame seam (polars-free)"
```

---

## Done criteria (R3 complete)
- [ ] `Column` seam gained `is_null`/`gt_mask`/`eq_mask`/`fill_null`/`sum`/`str_to_date` (import gate green — no `pl.` refs).
- [ ] `null_correlation`, `numeric_cross`, `temporal` are grep-clean of `polars`/`pl.` and route through the seam; every `try/except` preserved; `numeric_cross` wraps `int(sum())`, `temporal` keeps the raw scalar.
- [ ] All 3 relation tests pass with ZERO edits.
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] No scope creep: `_find_max_pairs`/`_find_date_pairs` untouched; R4 + the substrate backend + reader + deps flip untouched. Relation front is now R1–R3 seam-routed; only R4 (the decline pass) remains.
