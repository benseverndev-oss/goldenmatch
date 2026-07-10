# GoldenCheck Polars Eviction — Profiler Ports Batch A2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `format_detection` and `encoding_detection` off Polars onto the `Frame`/`Column` seam by adding 2 composable `Column` methods (`str_match_count`, `str_filter`) — byte-identical, no version bump.

**Architecture:** Add the string ops these two profilers need to the seam; `PolarsColumn` delegates each to the exact Polars call (byte-identical, Polars stays the fast path). `str_filter` returns a Column, so it composes: the cross-format count is `str_filter(A, matching=False).str_match_count(B)` and the samples are `str_filter(p, matching=...).to_list()[:5]`. Then rewrite the two profiler bodies onto the seam, removing their `pl` imports. Non-Polars impl of the seam ops comes with the Stage-2 substrate — not here.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-profiler-ports-batch-a2-design.md`

---

## Conventions (this plan runs in the `gc-batch-a2` worktree, off fresh origin/main)

Branch `feat/goldencheck-profiler-ports-batch-a2`, worktree `D:\show_case\gc-batch-a2`, off fresh `origin/main` (P0 #1605 + Batch A #1606 both merged — the seam has `dtype`/`cast`/`member_count`). NOT stacked.

**Test preamble** (from `/d/show_case/gc-batch-a2`):
```bash
export PYTHONPATH="D:/show_case/gc-batch-a2/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-batch-a2
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The 2 profilers' existing tests are the parity gate — they pass UNEDITED. The import gate (`tests/test_import_no_polars.py`) + full suite stay green. No version bump. Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `Column` = `{__len__, null_count, n_unique, drop_nulls, unique, sort, to_list, dtype, cast, member_count}`; `PolarsColumn` wraps a `pl.Series` in `self._s`. `PolarsFrame.column(name) -> PolarsColumn(self._df[name])`.

---

## Task 1: Add `str_match_count` + `str_filter` to the seam

**Files:** Modify `packages/python/goldencheck/goldencheck/core/frame.py`; Test `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_str_match_count():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["a@b.com", "nope", "c@d.org"]})).column("x")
    assert col.str_match_count(r"@") == 2
    assert col.str_match_count(r"^z") == 0

def test_column_str_filter_matching_and_complement():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["a@b", "nope", "c@d"]})).column("x")
    assert col.str_filter(r"@", matching=True).to_list() == ["a@b", "c@d"]      # matching rows
    assert col.str_filter(r"@", matching=False).to_list() == ["nope"]           # complement
    # composition: filter to non-matching-A, then count matching-B (cross-format shape)
    col2 = to_frame(pl.DataFrame({"x": ["http://x", "e@f.com", "plain"]})).column("x")
    assert col2.str_filter(r"^https?://", matching=False).str_match_count(r"@") == 1
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'str_match_count'`). `$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "str_match or str_filter" -v`

- [ ] **Step 3: Implement.** Add to the `Column` Protocol (after `member_count`):
```python
    def str_match_count(self, pattern: str) -> int: ...
    def str_filter(self, pattern: str, *, matching: bool) -> Column: ...
```
Add to `PolarsColumn`:
```python
    def str_match_count(self, pattern: str) -> int:
        return int(self._s.str.contains(pattern).sum())

    def str_filter(self, pattern: str, *, matching: bool) -> PolarsColumn:
        mask = self._s.str.contains(pattern)
        return PolarsColumn(self._s.filter(mask if matching else ~mask))
```
(Both reference `self._s.str.contains` / `self._s.filter` — no `pl.` symbol, so the import gate is trivially safe.)

- [ ] **Step 4: Run → PASS**, and the import gate stays green: `$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v`. Ruff clean.

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-batch-a2
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add str_match_count/str_filter to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port `format_detection` onto the seam

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/format_detection.py`

- [ ] **Step 1: Port the body.** Read the file first. Rewrite:
  - Signature: `def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:` — **drop the `frame: pl.DataFrame` annotation** (keep `frame = to_frame(frame)`).
  - Remove `df = frame.native` (no longer referenced); `col = frame.column(column)`.
  - `if col.dtype != "str": return findings` (was `col.dtype not in (pl.Utf8, pl.String)`).
  - `non_null = col.drop_nulls()`; `total = len(non_null)`; `if total == 0: return findings`.
  - Per format loop: `match_count = non_null.str_match_count(pattern)`; `match_pct = match_count / total` (UNCHANGED).
  - Detection Finding UNCHANGED. `non_match_count = total - match_count`.
  - `if non_match_count > 0:` block — `sample = non_null.str_filter(pattern, matching=False).to_list()[:5]` (was `non_null.filter(~matches).head(5).to_list()`); the non-matching Finding UNCHANGED.
  - Cross-format loop — KEEP its inner `if non_match_count > 0:` guard VERBATIM (control-flow byte-identity); inside:
    `wrong_fmt_count = non_null.str_filter(pattern, matching=False).str_match_count(other_pattern)`
    (was `non_null.filter(~matches).str.contains(other_pattern).sum()`). The ERROR Finding UNCHANGED.
  - Remove `from goldencheck._polars_lazy import pl`. Keep `to_frame`. The `FORMATS`/`EMAIL_REGEX`/etc. module constants are plain strings — leave them.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-a2 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_format_detection.py -v
```
Expected: all pass, ZERO edits (byte-identical Findings, including the cross-format ERROR + the non-matching samples). If a test fails, fix the PORT (check `str_filter` direction + the cross-format compose), never the test; report the assertion diff.

- [ ] **Step 3: Confirm polars-free + import gate.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/format_detection.py || echo "format_detection polars-free"
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/format_detection.py
git commit -m "refactor(goldencheck): port format_detection onto the Frame seam (polars-free)"
```

---

## Task 3: Port `encoding_detection` onto the seam + final verification

**Files:** Modify `packages/python/goldencheck/goldencheck/profilers/encoding_detection.py`

- [ ] **Step 1: Port the body.** Read the file first. Rewrite:
  - Signature: `def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:` — drop `frame: pl.DataFrame`.
  - Remove `df = frame.native`; `col = frame.column(column)`.
  - `if col.dtype != "str": return findings`; `non_null = col.drop_nulls()`; `total = len(non_null)`; `if total == 0: return findings`.
  - For EACH of the 4 checks (zero-width, smart-quotes, non-ASCII, control): `count = non_null.str_match_count(PATTERN)` (was `mask = non_null.str.contains(PATTERN); count = mask.sum()`); `if count > 0: sample = non_null.str_filter(PATTERN, matching=True).to_list()[:5]` (was `non_null.filter(mask).head(5).to_list()` — **matching**, positive). Each Finding UNCHANGED (incl. the `repr(v)` sample rendering).
  - Remove `from goldencheck._polars_lazy import pl`. The `*_PATTERN` module constants are plain strings — leave them.

- [ ] **Step 2: Run the existing test UNEDITED (parity gate).**
```bash
cd /d/show_case/gc-batch-a2 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/profilers/test_encoding_detection.py -v
```
Expected: all pass, ZERO edits (byte-identical — the samples are the MATCHING rows via `matching=True`). If a test fails, fix the PORT (most likely `matching` direction), never the test.

- [ ] **Step 3: Final verification.**
```bash
grep -nE "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/profilers/encoding_detection.py || echo "encoding_detection polars-free"
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite byte-identical
```
Expected: encoding_detection polars-free; import gate green; full suite green (same pass/skip counts as the fresh-main baseline + the 2 new seam tests). Ruff clean.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/profilers/encoding_detection.py
git commit -m "refactor(goldencheck): port encoding_detection onto the Frame seam (polars-free)"
```

---

## Done criteria (Batch A2 complete)
- [ ] `Column` seam gained `str_match_count`/`str_filter` (PolarsColumn delegates; import gate green — no `pl.` refs).
- [ ] `format_detection` + `encoding_detection` are polars-free (grep-clean) and route through the seam.
- [ ] Both profilers' existing tests pass with ZERO edits (byte-identical — incl. format's cross-format ERROR + non-matching samples, and encoding's MATCHING samples).
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] No scope creep: `pattern_consistency`, Batches B/C, the relation profilers, the substrate backend, reader, and deps flip untouched. 7 of ~13 column profilers now polars-free.
