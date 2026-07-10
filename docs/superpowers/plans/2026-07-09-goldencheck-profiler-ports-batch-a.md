# GoldenCheck Polars Eviction — Profiler Ports Batch A Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `type_inference` and `fuzzy_values` off Polars onto the P0 `Frame`/`Column` seam by adding 3 delegating `Column` methods (`dtype`, `cast`, `member_count`) — byte-identical, no version bump.

**Architecture:** Continue P0's eviction. Add the ops these two profilers need to the `Column` seam; `PolarsColumn` delegates each to the exact Polars call the profiler uses today (so results are byte-identical and Polars stays the fast path). Then rewrite the two profiler bodies to use `frame.column(col)` + the new seam methods, removing their `pl` imports. The non-Polars implementation of the seam ops arrives with the Stage-2 substrate backend — not here.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-profiler-ports-batch-a-design.md`

---

## Conventions (this plan runs in the `gc-batch-a` worktree, STACKED on P0)

Branch `feat/goldencheck-profiler-ports-batch-a`, worktree `D:\show_case\gc-batch-a`, stacked on `feat/goldencheck-polars-eviction-p0` (the P0 seam is present). Rebase onto fresh `origin/main` once P0 (#1605) merges (`git rebase --onto origin/main <P0-tip>`).

**Test preamble** (from `/d/show_case/gc-batch-a`):
```bash
export PYTHONPATH="D:/show_case/gc-batch-a/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-batch-a
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The 2 profilers' existing tests are the parity gate — they pass UNEDITED. The import-graph gate (`tests/test_import_no_polars.py`) and full suite stay green. No version bump. Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `Column` = `{__len__, null_count, n_unique, drop_nulls, unique, sort, to_list}`; `Frame` = `{columns, height, native, column(name)}`; `PolarsColumn`/`PolarsFrame` backends; idempotent `to_frame()`.

---

## Task 1: Add `dtype`, `cast`, `member_count` to the seam

**Files:** Modify `packages/python/goldencheck/goldencheck/core/frame.py`; Test `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Write failing seam unit tests** (append to `tests/core/test_frame.py`):
```python
def test_column_dtype_neutral_mapping():
    import polars as pl
    from goldencheck.core.frame import to_frame
    f = to_frame(pl.DataFrame({
        "s": ["a"], "i": pl.Series([1], dtype=pl.Int64), "u": pl.Series([1], dtype=pl.UInt32),
        "f": [1.5], "b": [True],
    }))
    assert f.column("s").dtype == "str"
    assert f.column("i").dtype == "int"
    assert f.column("u").dtype == "uint"      # DISTINCT from int (byte-identity for type_inference)
    assert f.column("f").dtype == "float"
    assert f.column("b").dtype == "other"

def test_column_cast_uncastable_to_null():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["1", "2", "oops"]})).column("x")
    casted = col.cast("float", strict=False)
    assert casted.null_count() == 1            # "oops" -> null
    assert len(casted) - casted.null_count() == 2
    assert to_frame(pl.DataFrame({"x": ["1", "2"]})).column("x").cast("int", strict=False).to_list() == [1, 2]

def test_column_member_count():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["a", "b", "a", "c", None]})).column("x")
    assert col.member_count(["a", "c"]) == 3   # a,a,c ; matches int(s.is_in(v).sum())
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'dtype'`). `$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "dtype or cast or member" -v`

- [ ] **Step 3: Implement.** Add to the `Column` Protocol (after `to_list`):
```python
    @property
    def dtype(self) -> str: ...
    def cast(self, kind: str, *, strict: bool = False) -> Column: ...
    def member_count(self, values: list) -> int: ...
```
Add a module-level dtype map + a cast-kind map (near the top, after imports — inside a function or as a lazily-built helper so no module-level `pl.` executes at import; the seam file itself is import-gate-sensitive):
```python
def _neutral_dtype(dt: Any) -> str:
    if dt in (pl.Utf8, pl.String):
        return "str"
    if dt in (pl.Int8, pl.Int16, pl.Int32, pl.Int64):
        return "int"
    if dt in (pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
        return "uint"
    if dt in (pl.Float32, pl.Float64):
        return "float"
    if dt == pl.Date:
        return "date"
    if dt == pl.Datetime:
        return "datetime"
    return "other"

_CAST_KIND = {"float": "Float64", "int": "Int64"}  # resolved to pl types lazily in cast()
```
(Both `_neutral_dtype` and the `cast` body reference `pl.` only INSIDE function bodies — never at module scope — so the import gate stays green.)
Add to `PolarsColumn`:
```python
    @property
    def dtype(self) -> str:
        return _neutral_dtype(self._s.dtype)

    def cast(self, kind: str, *, strict: bool = False) -> PolarsColumn:
        pl_type = getattr(pl, _CAST_KIND[kind])
        return PolarsColumn(self._s.cast(pl_type, strict=strict))

    def member_count(self, values: list) -> int:
        return int(self._s.is_in(values).sum())
```
(`__slots__` on PolarsColumn is `("_s",)` — a `@property` needs no slot, fine.)

- [ ] **Step 4: Run → PASS.** Then the import gate must stay green (the new `pl.` refs are all in-function): `$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v`. Ruff clean.

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-batch-a
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add dtype/cast/member_count to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port `type_inference` onto the seam

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/type_inference.py`

- [ ] **Step 1: Port the body.** Current state: `frame = to_frame(frame)`; `df = frame.native`; `col = df[column]`; `dtype = col.dtype`. Rewrite to use the seam:
  - Remove `df = frame.native`; `col = frame.column(column)`; `dt = col.dtype`.
  - `if dt == "str":` (was `dtype == pl.Utf8 or dtype == pl.String` — `"str"` covers both).
  - `non_null = col.drop_nulls()`; `if len(non_null) == 0: return findings`.
  - `cast_result = non_null.cast("float", strict=False)`; `numeric_count = len(non_null) - cast_result.null_count()` (byte-identical to `non_null.cast(pl.Float64, strict=False).is_not_null().sum()` — cast preserves length; both count non-nulls; both `int`).
  - `int_cast = non_null.cast("int", strict=False)`; `int_count = len(non_null) - int_cast.null_count()`.
  - The SHOULD_BE_STRING gate: `if dt in ("int", "float"):` (was signed-ints+floats only — `"int"` is signed, `"uint"` is EXCLUDED, so byte-identical; a `UInt` column is not flagged, same as today).
  - Keep all thresholds/messages/Finding construction UNCHANGED.
  - **Drop BOTH the `pl` import AND the `frame: pl.DataFrame` annotation** → `def profile(self, frame, column: str, *, context: dict | None = None)` (matching the P0-ported `nullability`/`cardinality`/`uniqueness`). Leaving `: pl.DataFrame` keeps a `pl` reference that the Step-3 polars-free grep flags.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-a && <preamble>
ls packages/python/goldencheck/tests/profilers | grep -i "type_infer"
$PY -m pytest packages/python/goldencheck/tests/profilers/test_type_inference.py -v
```
Expected: all pass, ZERO test edits (byte-identical Findings). If a test fails, the port diverged — fix the PORT (likely the numeric-count or the dtype gate), never the test.

- [ ] **Step 3: Confirm polars-free + import gate.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/type_inference.py || echo "type_inference polars-free"
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/type_inference.py
git commit -m "refactor(goldencheck): port type_inference onto the Frame seam (polars-free)"
```

---

## Task 3: Port `fuzzy_values` onto the seam + final verification

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/fuzzy_values.py`

- [ ] **Step 1: Port the body.** Current: `frame = to_frame(frame)`; `df = frame.native`; `df.height`; `col = df[column]`; `col.dtype != pl.Utf8`; `distinct = col.drop_nulls().unique()`; `distinct.len()`; `distinct.to_list()`; `col.is_in(variants).sum()`. Rewrite:
  - Remove `df = frame.native`.
  - `if frame.height < _MIN_ROWS: return []` (`Frame.height` exists).
  - `col = frame.column(column)`; `if col.dtype != "str": return []` (was `!= pl.Utf8`; `"str"` covers Utf8/String, and `pl.Categorical → "other"` stays excluded, matching today).
  - `distinct = col.drop_nulls().unique()`; `n_distinct = len(distinct)` (was `distinct.len()` — `__len__`); `values = distinct.to_list()`.
  - Clustering block UNCHANGED (`values` is `list[str]`; native/`_python_clusters` untouched).
  - `affected_rows=col.member_count(variants)` (was `int(col.is_in(variants).sum())`).
  - Keep everything else (messages, metadata, `_python_clusters`, rapidfuzz import) UNCHANGED.
  - **Drop BOTH the `pl` import AND the `frame: pl.DataFrame` annotation** → `def profile(self, frame, column: str, *, context: dict | None = None)` (matching the P0 profilers). Leaving `: pl.DataFrame` keeps a `pl` reference the Step-3 grep flags.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-a && <preamble>
ls packages/python/goldencheck/tests/profilers | grep -i "fuzzy"
$PY -m pytest packages/python/goldencheck/tests/profilers/test_fuzzy_values.py -v
```
Expected: all pass, ZERO edits. Run BOTH lanes (native + fallback) since fuzzy has a native path:
```bash
GOLDENCHECK_NATIVE=1 $PY -m pytest packages/python/goldencheck/tests/profilers/test_fuzzy_values.py -q
GOLDENCHECK_NATIVE=0 $PY -m pytest packages/python/goldencheck/tests/profilers/test_fuzzy_values.py -q
```
(If the native ext isn't built in this worktree, the native lane simply falls back — that's fine; report it.)

- [ ] **Step 3: Final verification.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/fuzzy_values.py || echo "fuzzy_values polars-free"
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite byte-identical
```
Expected: fuzzy_values polars-free; import gate green; full suite same pass/skip counts as the P0 baseline. Ruff clean.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/fuzzy_values.py
git commit -m "refactor(goldencheck): port fuzzy_values onto the Frame seam (polars-free)"
```

---

## Done criteria (Batch A complete)
- [ ] `Column` seam gained `dtype`/`cast`/`member_count` (PolarsColumn delegates; import gate still green — all new `pl.` refs are in-function).
- [ ] `type_inference` + `fuzzy_values` are polars-free (grep-clean) and route through the seam.
- [ ] Both profilers' existing tests pass with ZERO edits (byte-identical Findings — the parity gate).
- [ ] Full suite green (same counts); `import goldencheck` loads zero Polars.
- [ ] No scope creep: `format_detection`/`encoding_detection` (Batch A2), `pattern_consistency`, Batches B/C, the substrate backend, reader, and deps flip are all untouched.
