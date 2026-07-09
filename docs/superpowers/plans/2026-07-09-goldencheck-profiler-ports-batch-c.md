# GoldenCheck Polars Eviction — Profiler Ports Batch C Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `drift_detection` off Polars onto the `Frame`/`Column` seam by adding 1 positional `slice` method and 1 `cast` kind (`"str"`) — byte-identical, no version bump.

**Architecture:** Add positional `slice` to the seam (`PolarsColumn` delegates to `self._s.slice`) and extend `_CAST_KIND` with `"str": "String"` so `cast("str")` works. Then rewrite `drift_detection`'s body onto the seam — the two halves become `slice(0, mid)`/`slice(mid)`, `mean`/`std` reuse Batch B's ops, and the categorical `is_in().sum()` reuses Batch A's `member_count`. Remove the `pl` import and `_numeric_dtypes()` helper.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-profiler-ports-batch-c-design.md`

---

## Conventions (this plan runs in the `gc-batch-c` worktree, off fresh origin/main)

Branch `feat/goldencheck-profiler-ports-batch-c`, worktree `D:\show_case\gc-batch-c`, off fresh `origin/main` (P0 #1605 + A #1606 + A2 #1607 + B #1608 all merged — the seam has `dtype`/`cast`/`member_count`/`min`/`max`/`mean`/`std`/`diff`/`is_sorted`/`count_gt`/`count_eq`/`filter_outside`). NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-batch-c`):
```bash
export PYTHONPATH="D:/show_case/gc-batch-c/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-batch-c
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The `drift_detection` existing test is the parity gate — it passes UNEDITED. The import gate (`tests/test_import_no_polars.py`) + full suite stay green. No version bump. Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `Column` Protocol + `PolarsColumn` (wraps a `pl.Series` in `self._s`); `_CAST_KIND = {"float": "Float64", "int": "Int64"}`; `cast(self, kind, *, strict=False)` does `getattr(pl, _CAST_KIND[kind])`; `member_count(values)` = `int(self._s.is_in(values).sum())`; `mean`/`std` present. There is NO `slice` yet.

---

## Task 1: Add positional `slice` + the `"str"` cast kind to the seam

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_slice_positional_halves():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", [10, 20, 30, 40, 50])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    mid = 5 // 2   # 2
    # first half == s[:mid], second half == s[mid:]
    assert col.slice(0, mid).to_list() == s.slice(0, mid).to_list() == [10, 20]
    assert col.slice(mid).to_list() == s.slice(mid).to_list() == [30, 40, 50]
    # slice(mid) with no length runs to the end (matches s[mid:])
    assert col.slice(mid).to_list() == s[mid:].to_list()

def test_column_cast_str():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, 2, 3]})).column("x")
    assert col.cast("str", strict=True).to_list() == pl.Series([1, 2, 3]).cast(pl.String).to_list()
    assert col.cast("str", strict=True).to_list() == ["1", "2", "3"]
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'slice'` and a `KeyError: 'str'` from `_CAST_KIND`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "slice_positional or cast_str" -v
```

- [ ] **Step 3: Implement.**
  - Add to the `Column` Protocol (after `filter_outside`):
```python
    def slice(self, offset: int, length: int | None = None) -> Column: ...
```
  - Extend the `_CAST_KIND` module constant:
```python
_CAST_KIND = {"float": "Float64", "int": "Int64", "str": "String"}   # strings only; resolved via getattr in cast()
```
  - Add to `PolarsColumn` (after `filter_outside`):
```python
    def slice(self, offset: int, length: int | None = None) -> PolarsColumn:
        return PolarsColumn(self._s.slice(offset, length))
```
  (`slice` delegates via `self._s.slice` — no `pl.` symbol; `cast("str")` resolves through the existing `getattr(pl, "String")` path. Import gate stays green.)

- [ ] **Step 4: Run → PASS**, and the import gate stays green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-batch-c
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add positional slice + str cast-kind to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port `drift_detection` onto the seam + final verification

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/drift_detection.py`

- [ ] **Step 1: Read the file first**, then rewrite:
  - Signature: `def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:` — **drop `frame: pl.DataFrame`**. Keep `frame = to_frame(frame)`.
  - Remove `df = frame.native`; use `col = frame.column(column)`.
  - `total = len(col)`; `if total < MIN_ROWS: return findings` UNCHANGED.
  - `mid = total // 2`; `first_half = col.slice(0, mid).drop_nulls()`; `second_half = col.slice(mid).drop_nulls()` (was `col[:mid].drop_nulls()` / `col[mid:].drop_nulls()`).
  - `if len(first_half) == 0 or len(second_half) == 0: return findings` UNCHANGED.
  - High-cardinality skip: `non_null = col.drop_nulls()`; `if len(non_null) > 0:` `unique_pct = non_null.n_unique() / len(non_null)`; `if unique_pct > 0.90 and col.dtype not in ("int", "uint", "float"): return findings` (was `col.dtype not in _numeric_dtypes()`).
  - `is_numeric = col.dtype in ("int", "uint", "float")` (was `col.dtype in _numeric_dtypes()`).
  - **Numeric path:** `mean1 = first_half.mean()`; `mean2 = second_half.mean()`; `std1 = first_half.std()` (via seam). The `if mean1 is None or mean2 is None or std1 is None or std1 == 0: return findings` guard, `deviation = abs(mean2 - mean1) / std1`, the `DRIFT_STDDEV_*` thresholds, severity, and the numeric-drift Finding are ALL UNCHANGED.
  - **Categorical path (else):** `cats_first = set(first_half.cast("str", strict=True).to_list())`; `cats_second = set(second_half.cast("str", strict=True).to_list())` (was `first_half.cast(pl.String).to_list()`). `new_cats = cats_second - cats_first`; the `if new_cats:` block, `new_cat_pct`, `CATEGORICAL_DRIFT_*` thresholds, `sample_new = sorted(new_cats)[:10]` are ALL UNCHANGED. Replace `new_cat_mask = second_half.cast(pl.String).is_in(list(new_cats)); affected = int(new_cat_mask.sum())` with a single line: `affected = second_half.cast("str", strict=True).member_count(list(new_cats))` (`member_count` delegates to exactly `int(self._s.is_in(values).sum())`). The categorical-drift Finding UNCHANGED.
  - Remove `from goldencheck._polars_lazy import pl`; DELETE the `@lru_cache _numeric_dtypes()` helper AND `from functools import lru_cache`. Keep `to_frame`, `Finding`, `Severity`, `BaseProfiler`, and the `MIN_ROWS`/`DRIFT_*`/`CATEGORICAL_*` module constants.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-c && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_drift_detection.py -v
```
Expected: all pass, ZERO edits (byte-identical — both numeric-drift and categorical-drift branches). If a test fails, fix the PORT (most likely a `slice` offset or the `cast("str")` compose), never the test; report the assertion diff.

- [ ] **Step 3: Confirm this file polars-free.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\.|lru_cache" packages/python/goldencheck/goldencheck/profilers/drift_detection.py || echo "drift_detection polars-free"
```

- [ ] **Step 4: Final verification (whole batch).**
```bash
cd /d/show_case/gc-batch-c && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite byte-identical
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (same pass/skip counts as the fresh-main baseline + the 2 new seam tests); ruff clean. If the full suite has any FAILURES, investigate + report (do NOT edit tests to pass — a real failure means the port broke something).

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/drift_detection.py
git commit -m "refactor(goldencheck): port drift_detection onto the Frame seam (polars-free)"
```

---

## Done criteria (Batch C complete)
- [ ] `Column` seam gained `slice` (Protocol stub + PolarsColumn impl) and `_CAST_KIND` gained `"str"` (import gate green — no `pl.` refs).
- [ ] `drift_detection` is polars-free (grep-clean) and routes through the seam.
- [ ] `drift_detection`'s existing test passes with ZERO edits (byte-identical — both numeric + categorical drift branches).
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] No scope creep: `pattern_consistency` (A2b), the relation profilers, the substrate backend, reader, and deps flip untouched. 11 of ~13 column profilers now polars-free.
