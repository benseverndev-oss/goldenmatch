# GoldenCheck Polars Eviction — Profiler Ports Batch B Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `freshness`, `range_distribution`, and `sequence_detection` off Polars onto the `Frame`/`Column` seam by adding 9 scalar-reduction / comparison `Column` methods — byte-identical, no version bump.

**Architecture:** Add the reduction + comparison ops these three profilers need to the seam; `PolarsColumn` delegates each to the exact Polars call (byte-identical, Polars stays the fast path). Counts are bundled count-shaped (`count_gt`/`count_eq` = `int((s <op> x).sum())`, like `str_match_count`); the two-sided outlier filter returns a Column (`filter_outside`, like `str_filter`). Then rewrite the three profiler bodies onto the seam, removing their `pl` imports and `_*_dtypes()` helpers.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-profiler-ports-batch-b-design.md`

---

## Conventions (this plan runs in the `gc-batch-b` worktree, off fresh origin/main)

Branch `feat/goldencheck-profiler-ports-batch-b`, worktree `D:\show_case\gc-batch-b`, off fresh `origin/main` (P0 #1605 + Batch A #1606 + Batch A2 #1607 all merged — the seam has `dtype`/`cast`/`member_count`/`str_match_count`/`str_filter`). NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-batch-b`):
```bash
export PYTHONPATH="D:/show_case/gc-batch-b/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-batch-b
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The 3 profilers' existing tests are the parity gate — they pass UNEDITED. The import gate (`tests/test_import_no_polars.py`) + full suite stay green. No version bump. Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `Column` = `{__len__, null_count, n_unique, drop_nulls, unique, sort, to_list, dtype, cast, member_count, str_match_count, str_filter}`; `PolarsColumn` wraps a `pl.Series` in `self._s`; `_neutral_dtype` maps `pl.Datetime → "datetime"`, `pl.Date → "date"`, `pl.Int* → "int"`, `pl.UInt* → "uint"`, `pl.Float* → "float"`, `pl.Utf8`/`pl.String → "str"`, else `"other"`.

---

## Task 1: Add the 9 reduction/comparison methods to the seam

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_scalar_reductions():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [3, 1, 2, 5, 4]})).column("x")
    assert col.min() == 1
    assert col.max() == 5
    assert col.mean() == 3.0
    assert col.std() == pl.Series([3, 1, 2, 5, 4]).std()   # ddof=1 preserved

def test_column_diff_and_is_sorted():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, 2, 4]})).column("x")
    assert col.diff().drop_nulls().to_list() == [1, 2]     # leading null dropped
    assert col.is_sorted() is True
    unsorted = to_frame(pl.DataFrame({"x": [3, 1, 2]})).column("x")
    assert unsorted.is_sorted() is False

def test_column_count_gt_and_count_eq():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, 1, 2, 3, 0]})).column("x")
    assert col.count_gt(0) == 4
    assert col.count_eq(1) == 2
    assert isinstance(col.count_gt(0), int)

def test_column_count_gt_datetime_scalar():
    import datetime as dt
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [dt.date(2020, 1, 1), dt.date(2999, 1, 1)]})).column("x")
    assert col.count_gt(dt.date(2100, 1, 1)) == 1

def test_column_filter_outside():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", [1, 5, 10, 50, 100])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    # values < 5 or > 50 -> [1, 100], original order preserved
    assert col.filter_outside(5, 50).to_list() == s.filter((s < 5) | (s > 50)).to_list()
    assert col.filter_outside(5, 50).to_list() == [1, 100]
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'min'`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "scalar_reductions or diff_and_is_sorted or count_gt or count_eq or filter_outside" -v
```

- [ ] **Step 3: Implement.** Add to the `Column` Protocol (after `str_filter`, keeping `dtype` as the only `@property`):
```python
    def min(self) -> Any: ...
    def max(self) -> Any: ...
    def mean(self) -> Any: ...
    def std(self) -> Any: ...
    def diff(self) -> Column: ...
    def is_sorted(self) -> bool: ...
    def count_gt(self, value: Any) -> int: ...
    def count_eq(self, value: Any) -> int: ...
    def filter_outside(self, lower: Any, upper: Any) -> Column: ...
```
Add to `PolarsColumn` (after `str_filter`):
```python
    def min(self) -> Any:
        return self._s.min()

    def max(self) -> Any:
        return self._s.max()

    def mean(self) -> Any:
        return self._s.mean()

    def std(self) -> Any:
        return self._s.std()

    def diff(self) -> PolarsColumn:
        return PolarsColumn(self._s.diff())

    def is_sorted(self) -> bool:
        return bool(self._s.is_sorted())

    def count_gt(self, value: Any) -> int:
        return int((self._s > value).sum())

    def count_eq(self, value: Any) -> int:
        return int((self._s == value).sum())

    def filter_outside(self, lower: Any, upper: Any) -> PolarsColumn:
        return PolarsColumn(self._s.filter((self._s < lower) | (self._s > upper)))
```
(Every method delegates via `self._s.*` — no `pl.` symbol, so the import gate stays green. `Any` is already imported in `frame.py`.)

- [ ] **Step 4: Run → PASS**, and the import gate stays green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-batch-b
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add scalar-reduction + comparison ops to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port `freshness` onto the seam

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/freshness.py`

- [ ] **Step 1: Read the file first**, then rewrite:
  - Signature: `def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:` — **drop `frame: pl.DataFrame`**. Keep `frame = to_frame(frame)`.
  - Remove `df = frame.native` and `col = df[column]`; use `col = frame.column(column)`.
  - Dtype gate: `is_datetime = col.dtype == "datetime"`; `is_date = col.dtype == "date"` (was `col.dtype == pl.Datetime` / `pl.Date`). `if not (is_datetime or is_date): return []` UNCHANGED.
  - `non_null = col.drop_nulls()`; `if len(non_null) == 0: return []` (was `non_null.len() == 0`).
  - `now` line UNCHANGED (`_dt.date.today()` / `_dt.datetime.now()`; `import datetime as _dt` stays).
  - **Keep the `try/except` block VERBATIM in structure**, only swapping the two ops inside:
    `future_count = non_null.count_gt(now)` (was `int((non_null > now).sum())`);
    `newest = non_null.max()` (was `non_null.max()` — unchanged call, now via seam). The `except Exception: return []` UNCHANGED.
  - Future-dated Finding UNCHANGED.
  - Staleness block UNCHANGED except `affected_rows=non_null.len()` → `affected_rows=len(non_null)`. The date math on `newest` (`.date()`, `isinstance(newest, _dt.datetime)`, `(today - newest_date).days`) is pure Python — UNCHANGED.
  - Remove `from goldencheck._polars_lazy import pl`. Keep `to_frame`, `Finding`, `Severity`, `BaseProfiler`, `import datetime as _dt`.
  - **Module docstring touch-up:** the last docstring line reads `Pure-Polars: `dt.max()` + a vectorized future-count. ...`. After the port that's stale. Change it to `Routes through the Frame seam (`max()` + `count_gt()`); no native kernel -- date arithmetic is already vectorized and cheap.` (Keep it capital-P-free of a bare `polars`/`pl.` token so the grep gate stays clean.)

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_freshness.py -v
```
Expected: all pass, ZERO edits. If a test fails, fix the PORT (check the dtype-string gate and that `count_gt`/`max` stay inside the `try`), never the test; report the assertion diff.

- [ ] **Step 3: Confirm polars-free + import gate.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/freshness.py || echo "freshness polars-free"
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean on the file.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/freshness.py
git commit -m "refactor(goldencheck): port freshness onto the Frame seam (polars-free)"
```

---

## Task 3: Port `range_distribution` onto the seam

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/range_distribution.py`

- [ ] **Step 1: Read the file first**, then rewrite:
  - Signature: drop `frame: pl.DataFrame`. Keep `frame = to_frame(frame)`.
  - Remove `df = frame.native`; `col = frame.column(column)`.
  - **Snapshot the dtype string BEFORE the cast chain:** `dtype = col.dtype` (now a `str`). `is_numeric = dtype in ("int", "uint", "float")` (was `dtype in _numeric_dtypes()` — **includes uint**).
  - `mostly_numeric` chain UNCHANGED in structure: `col = col.cast("float", strict=False).drop_nulls()` (was `col.cast(pl.Float64, strict=False).drop_nulls()`); `is_numeric = True`; `elif not is_numeric: return findings`.
  - `non_null` re-check line — keep byte-identical: `non_null = col.drop_nulls() if is_numeric and dtype in ("int", "uint", "float") else col`.
  - `total = len(non_null)`; `if total < 2: return findings` UNCHANGED.
  - `mean = non_null.mean()`; `std = non_null.std()`; `col_min = non_null.min()`; `col_max = non_null.max()` (all now via seam). The INFO range Finding (`f"Range: min={col_min}, max={col_max}, mean={mean:.2f}"`) UNCHANGED.
  - Outlier block: `outliers = non_null.filter_outside(lower, upper)` (was `non_null.filter((non_null < lower) | (non_null > upper))`); `outlier_count = len(outliers)`; `sample = outliers.to_list()[:5]` (was `outliers.head(5).to_list()`). `max_dev` uses `float(non_null.max())` / `float(non_null.min())` — UNCHANGED (seam `max`/`min`). The WARNING outlier Finding UNCHANGED.
  - Remove `from goldencheck._polars_lazy import pl`, delete the `@lru_cache _numeric_dtypes()` helper AND `from functools import lru_cache` (now unused). Keep `to_frame`, `Finding`, `Severity`, `BaseProfiler`.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_range_distribution.py -v
```
Expected: all pass, ZERO edits (byte-identical — incl. the `mostly_numeric` cast branch and the outlier samples). If a test fails, fix the PORT (most likely the `dtype` snapshot ordering or the numeric-tuple membership), never the test.

- [ ] **Step 3: Confirm polars-free + import gate.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\.|lru_cache" packages/python/goldencheck/goldencheck/profilers/range_distribution.py || echo "range_distribution polars-free"
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean on the file.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/range_distribution.py
git commit -m "refactor(goldencheck): port range_distribution onto the Frame seam (polars-free)"
```

---

## Task 4: Port `sequence_detection` onto the seam + final verification

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/sequence_detection.py`

- [ ] **Step 1: Read the file first**, then rewrite:
  - Signature: drop `frame: pl.DataFrame`. Keep `frame = to_frame(frame)`.
  - Remove `df = frame.native`; `col = frame.column(column)`.
  - Integer gate: `if col.dtype not in ("int", "uint"): return findings` (was `col.dtype not in _integer_dtypes()`).
  - `non_null = col.drop_nulls()`; `total = len(non_null)`; `if total < 2: return findings` UNCHANGED.
  - `diffs = non_null.diff().drop_nulls()`; `n_diffs = len(diffs)`; `if n_diffs == 0: return findings` (was `non_null.diff().drop_nulls()` on a Series — now via seam).
  - `unit_diffs = diffs.count_eq(1)` (was `int((diffs == 1).sum())`); `positive_diffs = diffs.count_gt(0)` (was `int((diffs > 0).sum())`). `sequential_ratio` / `positive_ratio` UNCHANGED.
  - `is_tight_sequential` UNCHANGED; `is_sorted_sequential = (positive_ratio >= SEQUENTIAL_THRESHOLD) and non_null.is_sorted()` (was `non_null.is_sorted()` — now via seam). The `if not (...): return findings` UNCHANGED.
  - `col_min = int(non_null.min())`; `col_max = int(non_null.max())` (now via seam). `expected_count = col_max - col_min + 1`; `if expected_count <= total: return findings` UNCHANGED.
  - **Gap-set to pure Python** (drop `pl.Series`): replace
    ```python
    full_range = pl.Series("expected", range(col_min, col_max + 1))
    present = non_null.unique().sort()
    gaps = full_range.filter(~full_range.is_in(present))
    gap_count = len(gaps)
    sample_gaps = gaps.head(10).to_list()
    ```
    with
    ```python
    present = set(non_null.unique().to_list())
    gaps = [v for v in range(col_min, col_max + 1) if v not in present]
    gap_count = len(gaps)
    sample_gaps = gaps[:10]
    ```
    (Ascending order + `[:10]` slice are byte-identical to the Polars filter + `.head(10)`.) The WARNING Finding (`sample_values=[str(v) for v in sample_gaps]`, message with `sample_gaps`) UNCHANGED.
  - Remove `from goldencheck._polars_lazy import pl`, delete the `@lru_cache _integer_dtypes()` helper AND `from functools import lru_cache`. Keep `to_frame`, `Finding`, `Severity`, `BaseProfiler`, `SEQUENTIAL_THRESHOLD`.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_sequence_detection.py -v
```
Expected: all pass, ZERO edits (byte-identical — the gap sample order + count match the Polars path). If a test fails, fix the PORT (most likely the gap list-comp order), never the test.

- [ ] **Step 3: Confirm polars-free + import gate.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\.|lru_cache" packages/python/goldencheck/goldencheck/profilers/sequence_detection.py || echo "sequence_detection polars-free"
```

- [ ] **Step 4: Final verification (whole batch).**
```bash
cd /d/show_case/gc-batch-b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite byte-identical
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (same pass/skip counts as the fresh-main baseline + the new seam tests); ruff clean. Confirm all three profilers grep-clean:
```bash
grep -REn "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/freshness.py packages/python/goldencheck/goldencheck/profilers/range_distribution.py packages/python/goldencheck/goldencheck/profilers/sequence_detection.py || echo "all 3 polars-free"
```

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/sequence_detection.py
git commit -m "refactor(goldencheck): port sequence_detection onto the Frame seam (polars-free)"
```

---

## Done criteria (Batch B complete)
- [ ] `Column` seam gained `min`/`max`/`mean`/`std`/`diff`/`is_sorted`/`count_gt`/`count_eq`/`filter_outside` (PolarsColumn delegates; import gate green — no `pl.` refs).
- [ ] `freshness` + `range_distribution` + `sequence_detection` are polars-free (grep-clean) and route through the seam.
- [ ] All three profilers' existing tests pass with ZERO edits (byte-identical — incl. freshness's tz `try/except`, range's `mostly_numeric` cast branch + outlier samples, sequence's gap sample order/count).
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] No scope creep: `drift_detection` (Batch C), `pattern_consistency` (A2b), the relation profilers, the substrate backend, reader, and deps flip untouched. 10 of ~13 column profilers now polars-free.
