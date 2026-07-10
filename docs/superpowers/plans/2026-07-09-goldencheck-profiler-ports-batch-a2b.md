# GoldenCheck Polars Eviction — Profiler Ports Batch A2b Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `pattern_consistency` (the last column profiler) off Polars onto the `Frame`/`Column` seam by adding 4 `Column` methods (`str_replace_all`, `value_counts_desc`, `eq`, `filter_by`) — byte-identical, no version bump.

**Architecture:** Add the string-generalization / histogram / cross-column-mask ops to the seam; `PolarsColumn` delegates each to the exact Polars call. Then rewrite `profile()` onto the seam, and make an annotation-only edit to the parity-gate-locked `_generalize_series` helper so the file goes grep-clean (its body + tests stay untouched).

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-profiler-ports-batch-a2b-design.md`

---

## Conventions (this plan runs in the `gc-batch-a2b` worktree, off fresh origin/main)

Branch `feat/goldencheck-profiler-ports-batch-a2b`, worktree `D:\show_case\gc-batch-a2b`, off fresh `origin/main` (P0 #1605 + A #1606 + A2 #1607 + B #1608 + C #1610 all merged). NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-batch-a2b`):
```bash
export PYTHONPATH="D:/show_case/gc-batch-a2b/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-batch-a2b
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The `pattern_consistency` existing test is the parity gate — it passes UNEDITED (both the profiler cases AND the three direct `_generalize`/`_generalize_series` tests). The import gate (`tests/test_import_no_polars.py`) + full suite stay green. No version bump. Do NOT add new test FILES (appending to the existing `test_frame.py` is fine — new files can shift pytest-split shard boundaries in CI). Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `Column` Protocol + `PolarsColumn` (wraps a `pl.Series` in `self._s`); has `dtype`/`cast`/`member_count`/`str_match_count`/`str_filter`/`min`/`max`/`mean`/`std`/`diff`/`is_sorted`/`count_gt`/`count_eq`/`filter_outside`/`slice`. Does NOT yet have `str_replace_all`/`value_counts_desc`/`eq`/`filter_by`.

---

## Task 1: Add `str_replace_all` + `value_counts_desc` + `eq` + `filter_by` to the seam

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_str_replace_all_chain():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", ["A1", "bc23", "Z9z"])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    # letters -> L, then digits -> D (letters first, matching _generalize_series)
    got = col.str_replace_all(r"\p{L}", "L").str_replace_all(r"\d", "D").to_list()
    assert got == s.str.replace_all(r"\p{L}", "L").str.replace_all(r"\d", "D").to_list()
    assert got == ["LD", "LLDD", "LDL"]

def test_column_value_counts_desc():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", ["a", "a", "a", "b", "b", "c"])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    got = col.value_counts_desc()
    raw = s.value_counts().sort("count", descending=True)
    assert got == list(zip(raw["x"].to_list(), raw["count"].to_list()))
    assert got[0] == ("a", 3)   # most frequent first
    assert all(isinstance(cnt, int) for _, cnt in got)

def test_column_eq_and_filter_by():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({"val": ["keep1", "drop", "keep2"], "pat": ["A", "B", "A"]}))
    val = frame.column("val")
    pat = frame.column("pat")
    # eq -> boolean mask; filter_by selects val rows where pat == "A"
    assert val.filter_by(pat.eq("A")).to_list() == ["keep1", "keep2"]
    # equivalence to the raw cross-column filter
    assert val.filter_by(pat.eq("A")).to_list() == val._s.filter(pat._s == "A").to_list()
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'str_replace_all'`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "str_replace_all_chain or value_counts_desc or eq_and_filter_by" -v
```

- [ ] **Step 3: Implement.** Add to the `Column` Protocol (after `slice`):
```python
    def str_replace_all(self, pattern: str, value: str) -> Column: ...
    def value_counts_desc(self) -> list[tuple[Any, int]]: ...
    def eq(self, value: Any) -> Column: ...
    def filter_by(self, mask: Column) -> Column: ...
```
Add to `PolarsColumn` (after `slice`):
```python
    def str_replace_all(self, pattern: str, value: str) -> PolarsColumn:
        return PolarsColumn(self._s.str.replace_all(pattern, value))

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        vc = self._s.value_counts().sort("count", descending=True)
        return list(zip(vc[self._s.name].to_list(), vc["count"].to_list()))

    def eq(self, value: Any) -> PolarsColumn:
        return PolarsColumn(self._s == value)

    def filter_by(self, mask: Column) -> PolarsColumn:
        return PolarsColumn(self._s.filter(mask._s))
```
(Every method delegates via `self._s.*` / `mask._s` — no `pl.` symbol, so the import gate stays green. `Any` + `Column` are already available in `frame.py`. `value_counts_desc` uses `self._s.name` — the original column name, preserved through `drop_nulls`/`str.replace_all` — so `vc[self._s.name]` selects the value column exactly as the profiler's `pattern_counts[column]` did.)

- [ ] **Step 4: Run → PASS**, import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-batch-a2b
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add str_replace_all/value_counts_desc/eq/filter_by to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port `pattern_consistency` onto the seam + final verification

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/pattern_consistency.py`

- [ ] **Step 1: Read the file first.** Then rewrite `profile()` and make the two module edits below. Everything not called out stays byte-identical.

  **`profile()` body:**
  - Signature: `def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:` — **drop `frame: pl.DataFrame`**. Keep `frame = to_frame(frame)`.
  - Remove `df = frame.native`; `col = frame.column(column)`.
  - dtype gate: `if col.dtype != "str": return findings` (was `col.dtype not in (pl.Utf8, pl.String)`).
  - `non_null = col.drop_nulls()`; `total = len(non_null)`; `if total == 0: return findings` UNCHANGED.
  - Pattern generation via the seam: `patterns = non_null.str_replace_all(r"\p{L}", "L").str_replace_all(r"\d", "D")` (was `patterns = _generalize_series(non_null)`).
  - Replace the comment block above it (the "Build pattern counts via vectorised regex ... map_elements(_generalize) ... #3 self-time hotspot" comment, currently ~4 lines) with a single accurate line: `# Generalise each value to its digit/letter skeleton, then tally the skeletons.` (the old comment references `map_elements`/`_generalize_series`, which `profile()` no longer calls — leaving it would be stale).
  - Histogram: `pattern_counts = patterns.value_counts_desc()` (a `list[(pattern, count)]`). Remove the old `patterns.value_counts().sort(...)` expression. `n_patterns = len(pattern_counts)`; `if n_patterns <= 1: return findings` UNCHANGED.
  - `dominant_pattern, dominant_count = pattern_counts[0]` (was `dominant_count = pattern_counts["count"][0]`; `dominant_pattern = pattern_counts[column][0]`).
  - Minority loop: `for i in range(1, n_patterns):` → `minority_pattern, minority_count = pattern_counts[i]`; `minority_count = int(minority_count)`; `minority_pct = minority_count / total`; the `if minority_pct < MINORITY_THRESHOLD:` accumulation into `minority_candidates` UNCHANGED.
  - `minority_candidates.sort(...)`, `MAX_PATTERNS = 5`, `emitted = ...[:MAX_PATTERNS]`, and the emit loop are UNCHANGED **except** the sample line: `sample_vals = non_null.filter_by(patterns.eq(minority_pattern)).to_list()[:5]` (was `mask = patterns == minority_pattern; sample_vals = non_null.filter(mask).head(5).to_list()`). All Findings (per-pattern + `msg_extra` structural-shift + the summary Finding), thresholds, severities, confidences, metadata UNCHANGED. Keep the trailing `return findings`.

  **`_generalize_series` (annotation-only edit — body UNTOUCHED):**
  - Change `def _generalize_series(s: pl.Series) -> pl.Series:` to `def _generalize_series(s):`. Do NOT touch the docstring or the body (`return s.str.replace_all(r"\p{L}", "L").str.replace_all(r"\d", "D")`). This keeps all three direct tests passing (they assert `.to_list()` behavior, not the annotation) while removing the only remaining `pl.` token.

  **`_generalize` (pure-Python helper):** leave the FUNCTION untouched. Its docstring mentions `_generalize_series` — that's fine (no `pl.`/`polars` token). Optionally trim the "prefer `_generalize_series`" sentence for accuracy, but not required.

  **Imports:** remove `from goldencheck._polars_lazy import pl` (no longer referenced anywhere in the module). Keep `to_frame`, `Finding`, `Severity`, `BaseProfiler`, and the `MINORITY_THRESHOLD`/`WARNING_THRESHOLD` constants.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-a2b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_pattern_consistency.py -v
```
Expected: all pass, ZERO test edits — BOTH the `PatternConsistencyProfiler` cases AND `test_generalize_series_matches_python_loop` / `test_generalize_series_divergence_on_compat_digits` / `test_generalize_series_letters_before_digits_order`. If a test fails, fix the PORT (likely the `value_counts_desc` unpack or the `filter_by`/`eq` compose), never the test; report the assertion diff.

- [ ] **Step 3: Confirm this file polars-free.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/pattern_consistency.py || echo "pattern_consistency polars-free"
```
Expected: `pattern_consistency polars-free` (the capital-"Polars" mentions in the `_generalize_series` docstring do NOT match the lowercase pattern — that's fine).

- [ ] **Step 4: Final verification (whole batch).**
```bash
cd /d/show_case/gc-batch-a2b && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite byte-identical
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (report exact passed/skipped counts vs the fresh-main baseline + the 3 new seam tests); ruff clean. If the full suite has any FAILURES, investigate + report (do NOT edit tests to pass — a real failure means the port broke something).

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/pattern_consistency.py
git commit -m "refactor(goldencheck): port pattern_consistency onto the Frame seam (polars-free)"
```

---

## Done criteria (Batch A2b complete)
- [ ] `Column` seam gained `str_replace_all`/`value_counts_desc`/`eq`/`filter_by` (PolarsColumn delegates; import gate green — no `pl.` refs).
- [ ] `pattern_consistency` is polars-free (grep-clean) and routes `profile()` through the seam; `_generalize_series` kept (annotation-only edit) so its three direct tests pass unedited.
- [ ] `test_pattern_consistency.py` passes with ZERO edits (profiler cases + all three helper tests — byte-identical).
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] No scope creep: the 9 relation profilers, the substrate backend, reader, and deps flip untouched. **12 of ~13 column profilers now polars-free — the whole column-profiler front is done.**
