# GoldenCheck Polars Eviction — Relation Ports R1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `identity_safe_pk` (the first relation profiler) off Polars onto the `Frame`/`Column` seam by adding `Column.dtype_repr()` + a `pl.Boolean → "bool"` neutral-dtype-map entry — byte-identical, no version bump.

**Architecture:** Add a `dtype_repr()` display op (so the `unsuitable dtype (Float64)` reason renders byte-identically) and a `"bool"` category to the neutral dtype map (so the `== pl.Boolean` check becomes `dtype == "bool"`). Then rewrite `identity_safe_pk`'s `profile()` + its `_column_qualifies_as_pk` helper onto the seam, removing the `pl` import.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-relation-ports-r1-design.md`

---

## Conventions (this plan runs in the `gc-r1` worktree, off fresh origin/main)

Branch `feat/goldencheck-relation-ports-r1`, worktree `D:\show_case\gc-r1`, off fresh `origin/main` (P0 #1605 + A #1606 + A2 #1607 + B #1608 + C #1610 + A2b #1611 all merged — the whole column-profiler front is seam-routed). NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-r1`):
```bash
export PYTHONPATH="D:/show_case/gc-r1/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-r1
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The `identity_safe_pk` existing test is the parity gate — it passes UNEDITED. The import gate (`tests/test_import_no_polars.py`) + full suite stay green. No version bump. Do NOT add new test FILES (appending to the existing `test_frame.py` is fine — new files can shift pytest-split shard boundaries in CI). Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `_neutral_dtype(dt)` maps `pl.Utf8`/`pl.String → "str"`, `pl.Int* → "int"`, `pl.UInt* → "uint"`, `pl.Float32/Float64 → "float"`, `pl.Date → "date"`, `pl.Datetime → "datetime"`, else `"other"` (Boolean currently falls to `"other"`). `Column` has `dtype` (returns the neutral string) + ~26 ops but NO `dtype_repr`. `Frame` has `columns` (list[str]), `height` (int), `native`, `column(name)`.

---

## Task 1: Add `dtype_repr()` + the `"bool"` neutral-dtype-map entry

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_dtype_bool_and_repr():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({
        "b": [True, False, True],
        "f": [1.0, 2.0, 3.0],
        "i": [1, 2, 3],
        "s": ["a", "b", "c"],
    }))
    # neutral dtype: Boolean now maps to "bool" (was "other"); float unchanged
    assert frame.column("b").dtype == "bool"
    assert frame.column("f").dtype == "float"
    # dtype_repr renders the raw Polars dtype string, byte-identical to str(dtype)
    assert frame.column("f").dtype_repr() == str(pl.Series([1.0]).dtype)   # "Float64"
    assert frame.column("f").dtype_repr() == "Float64"
    assert frame.column("b").dtype_repr() == "Boolean"
    assert frame.column("i").dtype_repr() == "Int64"
    assert frame.column("s").dtype_repr() == "String"
```

(Note: the `dtype_repr()` string assertions `"String"`/`"Int64"` are Polars-version-sensitive — modern Polars renders the string dtype as `"String"`; if this suite's Polars renders `"Utf8"`, reconcile the TEST literal, not the port. This never touches the parity gate — `identity_safe_pk`'s tests only exercise the float/boolean disqualifier paths.)

- [ ] **Step 2: Run → FAIL** (`AssertionError: 'other' == 'bool'` and/or `AttributeError: ... no attribute 'dtype_repr'`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "dtype_bool_and_repr" -v
```

- [ ] **Step 3: Implement.**
  - In `_neutral_dtype`, add a `pl.Boolean` branch **before** the final `return "other"`:
```python
    if dt == pl.Boolean:
        return "bool"
    return "other"
```
  (Place it after the existing `pl.Datetime → "datetime"` branch; it uses the already-imported lazy `pl` proxy, so no new import-time Polars access.)
  - Add to the `Column` Protocol (after the existing `dtype` property — `dtype_repr` is a plain method, NOT a `@property`):
```python
    def dtype_repr(self) -> str: ...
```
  - Add to `PolarsColumn` (next to its `dtype` property):
```python
    def dtype_repr(self) -> str:
        return str(self._s.dtype)
```
  (`str(self._s.dtype)` — no `pl.` symbol; import gate stays green.)

- [ ] **Step 4: Run → PASS**, import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-r1
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add Column.dtype_repr() + map pl.Boolean -> 'bool' in the Frame seam"
```

---

## Task 2: Port `identity_safe_pk` onto the seam + final verification

**Files:** Modify `packages/python/goldencheck/goldencheck/relations/identity_safe_pk.py`

- [ ] **Step 1: Read the file first.** Then rewrite the two functions below; everything not called out (the module docstring, `_VALUE_COLUMN_PATTERNS`, `_PK_NAME_PATTERNS`, `_looks_like_value_column`, `_looks_like_pk_column`, both Finding bodies) stays byte-identical.

  **A) `_column_qualifies_as_pk` (module helper — the test does NOT import it, so the signature change is free):**
  - Signature: `def _column_qualifies_as_pk(frame, column: str) -> tuple[bool, str]:` (was `def _column_qualifies_as_pk(df: pl.DataFrame, column: str,) -> tuple[bool, str]:` — rename the first param `df` → `frame`, drop its `pl.DataFrame` annotation).
  - The `if _looks_like_value_column(column): return False, "value-shaped name (email/name/address/etc.)"` guard UNCHANGED (it comes first).
  - `col = frame.column(column)` (was `col = df[column]`).
  - **Remove** the `dtype = col.dtype` line. Dtype disqualifier: `if col.dtype in ("float", "bool"): return False, f"unsuitable dtype ({col.dtype_repr()})"` (was `dtype = col.dtype` then `if dtype.is_float() or dtype == pl.Boolean: return False, f"unsuitable dtype ({dtype})"`). Note `col.dtype` here is the neutral string ("float"/"bool"), and `col.dtype_repr()` renders the raw Polars dtype ("Float64"/"Boolean") — byte-identical to the original `{dtype}` interpolation.
  - `n_rows = len(col)`; `if n_rows == 0: return False, "empty sample"` UNCHANGED.
  - `n_nulls = col.null_count()`; `if n_nulls > 0: return False, f"{n_nulls} null value(s)"` UNCHANGED.
  - `if col.n_unique() != n_rows: return False, "non-unique values"` UNCHANGED.
  - `return True, "stable unique non-null"` UNCHANGED.

  **B) `IdentitySafePkProfiler.profile` (the param is ALREADY named `frame`):**
  - Signature: `def profile(self, frame) -> list[Finding]:` — **drop the `frame: pl.DataFrame` annotation**. Keep `frame = to_frame(frame)`.
  - **Remove** the local `df = frame.native`.
  - `if len(frame.columns) == 0: return []` (was `if df.width == 0: return []`).
  - Loop: `for column in frame.columns:` (was `for column in df.columns:`); call `_column_qualifies_as_pk(frame, column)` (was `(df, column)`). The candidate/disqualifier/named-PK-disqualifier accumulation UNCHANGED.
  - `if candidates: return []` UNCHANGED.
  - Named-PK-disqualifier Finding: `affected_rows=frame.height` (was `affected_rows=len(df)`). Everything else in that Finding UNCHANGED.
  - Generic `__dataset__` Finding: `sample_cols = ", ".join(frame.columns[:5])` (was `df.columns[:5]`); `if len(frame.columns) > 5: sample_cols += ", ..."` (was `if df.width > 5:`); `affected_rows=frame.height` (was `len(df)`). Everything else UNCHANGED.
  - Remove `from goldencheck._polars_lazy import pl`. Keep `to_frame`, `Finding`, `Severity`.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-r1 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/relations/test_identity_safe_pk.py -v
```
Expected: all 10 pass, ZERO test edits (clean-PK, uuid, no-PK, named-PK-nulls, named-PK-dups, value-column, float, boolean, empty-DF, multiple-candidates). If a test fails, fix the PORT (likely the dtype-string check or a `frame.columns`/`frame.height` substitution), never the test; report the assertion diff.

- [ ] **Step 3: Confirm this file polars-free.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/relations/identity_safe_pk.py || echo "identity_safe_pk polars-free"
```

- [ ] **Step 4: Final verification (whole batch).**
```bash
cd /d/show_case/gc-r1 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite byte-identical
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (report exact passed/skipped counts vs the fresh-main baseline + the new seam test); ruff clean. If the full suite has any FAILURES, investigate + report (do NOT edit tests to pass — a real failure means the port broke something, OR the `"bool"` map change affected another profiler — check which test failed).

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/relations/identity_safe_pk.py
git commit -m "refactor(goldencheck): port identity_safe_pk onto the Frame seam (polars-free)"
```

---

## Done criteria (R1 complete)
- [ ] `Column` seam gained `dtype_repr()`; `_neutral_dtype` maps `pl.Boolean → "bool"` (import gate green — no `pl.` refs at method scope).
- [ ] `identity_safe_pk` is polars-free (grep-clean) and routes `profile()` + `_column_qualifies_as_pk` through the seam.
- [ ] `test_identity_safe_pk.py` passes with ZERO edits (all 10 tests — byte-identical).
- [ ] Full suite green (the `"bool"` re-map did not disturb any other profiler); `import goldencheck` loads zero Polars.
- [ ] No scope creep: R2/R3/R4 profilers, the substrate backend, reader, and deps flip untouched. First relation profiler is now seam-routed; R2 (`Frame.select(cols).n_unique()`) is next.
