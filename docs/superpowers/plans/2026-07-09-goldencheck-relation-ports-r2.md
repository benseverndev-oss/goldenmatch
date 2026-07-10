# GoldenCheck Polars Eviction — Relation Ports R2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `composite_key` + `functional_dependency` + `approx_fd` (+ the `functional_dependencies` bridge) off Polars onto the seam + `frame.native` escape hatch, adding `Column.to_arrow()` + `Column.get()` — byte-identical, no version bump.

**Architecture:** These three are native-kernel-first with parity-gate-locked raw-`pl.DataFrame` fallback helpers. Add `to_arrow`/`get` and route the native Arrow export + approx_fd samples through the seam; reach the parity-locked helpers via `frame.native`; swap each `_supported()` pl-tuple for a neutral-string frozenset via `_neutral_dtype(series.dtype)`. Make each file grep-clean.

**Tech Stack:** Python 3.13, Polars (still a hard dep), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-goldencheck-relation-ports-r2-design.md`

---

## Conventions (this plan runs in the `gc-r2` worktree, off fresh origin/main through R1)

Branch `feat/goldencheck-relation-ports-r2`, worktree `D:\show_case\gc-r2`, off fresh `origin/main` (through R1 #1612 — the seam has `_neutral_dtype` with `pl.Boolean → "bool"` + `dtype_repr`). NOT stacked.

**Test preamble** (run every test command from `/d/show_case/gc-r2`):
```bash
export PYTHONPATH="D:/show_case/gc-r2/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-r2
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/<path> -v`. Ruff (100-char): `$PY -m ruff check <paths>`.

**INVARIANT:** byte-identical. The parity gates pass UNEDITED: `tests/relations/test_{composite_key,functional_dependency,approx_fd}.py`, `tests/test_functional_dependencies.py`, AND `tests/core/test_native_parity.py` (which calls `_select_candidates(df,…)`/`_python_search(df,…)`/`_discover_python(df,…)`/`_intern(list)` directly with a raw `pl.DataFrame`/lists). The import gate + full suite stay green. No version bump. Do NOT edit `tests/core/test_native_parity.py` or `goldencheck/core/kernels.py` (they call the parity-locked helpers with a raw df — those helper signatures MUST stay raw-df). Do NOT add new test FILES (append to `test_frame.py`). Commit per task; do NOT push.

**Current seam** (`goldencheck/core/frame.py`): `_neutral_dtype(dt)` maps `pl.Utf8`/`pl.String → "str"`, `pl.Int* → "int"`, `pl.UInt* → "uint"`, `pl.Float32/Float64 → "float"`, `pl.Date → "date"`, `pl.Datetime → "datetime"`, `pl.Boolean → "bool"`, else `"other"`. `Column` has ~27 ops (incl. `dtype`, `dtype_repr`, `to_list`, `n_unique`, `null_count`) but NO `to_arrow`/`get`. `Frame` has `columns`/`height`/`native`/`column()`.

---

## Task 1: Add `to_arrow()` + `get()` to the seam

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Append failing seam tests** to `tests/core/test_frame.py`:
```python
def test_column_to_arrow():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", [1, 2, 3])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    got = col.to_arrow()
    assert got.to_pylist() == s.to_arrow().to_pylist() == [1, 2, 3]

def test_column_get():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({"n": [10, 20, 30], "s": ["a", "b", "c"]}))
    assert frame.column("n").get(0) == 10
    assert frame.column("n").get(2) == 30
    assert frame.column("s").get(1) == "b"
    # byte-identical to raw Series indexing (what df[c][r] does)
    assert frame.column("s").get(1) == pl.Series(["a", "b", "c"])[1]
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PolarsColumn' object has no attribute 'to_arrow'`).
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "to_arrow or column_get" -v
```

- [ ] **Step 3: Implement.** Add to the `Column` Protocol (after the existing `dtype_repr` line):
```python
    def to_arrow(self) -> Any: ...
    def get(self, index: int) -> Any: ...
```
Add to `PolarsColumn`:
```python
    def to_arrow(self) -> Any:
        return self._s.to_arrow()

    def get(self, index: int) -> Any:
        return self._s[index]
```
(Both delegate via `self._s.*` — no `pl.` symbol; import gate stays green. `Any` is already imported.)

- [ ] **Step 4: Run → PASS**, import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean: `$PY -m ruff check packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-r2
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): add Column.to_arrow() + Column.get() to the Frame seam (PolarsColumn delegates)"
```

---

## Task 2: Port composite_key + functional_dependency + approx_fd + the bridge, then verify

**Files:** Modify (in this order):
- `packages/python/goldencheck/goldencheck/relations/composite_key.py`
- `packages/python/goldencheck/goldencheck/relations/functional_dependency.py`
- `packages/python/goldencheck/goldencheck/relations/approx_fd.py`
- `packages/python/goldencheck/goldencheck/functional_dependencies.py`

**Shared edits for ALL FOUR files** (read each file first):
- Remove `from goldencheck._polars_lazy import pl`.
- Add `_neutral_dtype` to the `from goldencheck.core.frame import ...` line (the three profilers already import `to_frame`; the **bridge must add `from goldencheck.core.frame import to_frame`** — it doesn't currently import it).
- Drop EVERY `pl.DataFrame` / `pl.Series` annotation (on `profile`, `_select_candidates`, `_python_search`, `_discover_python`, `_has_single_column_key`, `_python`, `_strict_pairs`, `_approx_triples`, `functional_dependencies`).
- The parity-locked helpers (`_select_candidates`, `_python_search`, `_discover_python`, `_intern`, `_has_single_column_key`, `_python`) KEEP their raw-df/list signatures — only the annotation drop + the `_select_candidates` dtype-swap below change them.

- [ ] **Step 1: Port `composite_key.py`.**
  - Delete the `@lru_cache _supported()` helper AND `from functools import lru_cache`. Add module constant: `_SUPPORTED = frozenset({"str", "int", "uint", "float", "bool"})`.
  - `_select_candidates(df, n_rows)` (keep raw-df sig): `series = df[col]`; `if _neutral_dtype(series.dtype) not in _SUPPORTED: continue` (was `if series.dtype not in _supported():`); `series.n_unique()` unchanged.
  - `_has_single_column_key(df, n_rows)` (keep raw-df sig): body unchanged (drop annotation only).
  - `_python_search(df, candidates, n_rows, max_size)`: body unchanged (drop annotation only).
  - `profile(self, frame)`: drop annotation; keep `frame = to_frame(frame)` + `df = frame.native`. `n_rows = df.height`; `if n_rows < 2 or len(frame.columns) < 2: return []` (was `df.width < 2`). `_has_single_column_key(df, n_rows)`, `_select_candidates(df, n_rows)` UNCHANGED calls. Native path: `arrays = [frame.column(c).to_arrow() for c in candidates]` (was `[df[c].to_arrow() for c in candidates]`). `except:` → `_python_search(df, candidates, n_rows, MAX_KEY_SIZE)` UNCHANGED. Finding-building loop UNCHANGED.

- [ ] **Step 2: Port `functional_dependency.py`.**
  - Delete `@lru_cache _supported()` + `from functools import lru_cache`. Add: `_SUPPORTED = frozenset({"str", "int", "uint", "bool"})` (**no float**).
  - `_select_candidates(df, n_rows)`: same dtype-swap as composite_key (`_neutral_dtype(series.dtype) not in _SUPPORTED`).
  - `_discover_python(df, cols, n_rows)`: body unchanged (drop annotation only).
  - `profile(self, frame)`: drop annotation; `frame = to_frame(frame)` + `df = frame.native`. `n_rows = df.height`; `if n_rows < _MIN_ROWS or len(frame.columns) < 2: return []`. Native: `arrays = [frame.column(c).to_arrow() for c in cols]`; `except:` → `_discover_python(df, cols, n_rows)`. Finding loop UNCHANGED.

- [ ] **Step 3: Port `approx_fd.py`.**
  - Delete `@lru_cache _supported()` + `from functools import lru_cache`. Add: `_SUPPORTED = frozenset({"str", "int", "uint", "bool"})` (**no float**).
  - `_select_candidates(df)` (note: takes only `df`): same dtype-swap.
  - `_intern`, `_group_modes`, `_violation_rows`, `_discover_python` (all list-based): UNCHANGED.
  - `_python(self, df, cols, n_rows)`: body `cols_ids = [_intern(df[c].to_list()) for c in cols]` unchanged (drop annotation only).
  - `profile(self, frame)`: drop annotation; `frame = to_frame(frame)` + `df = frame.native`. `n_rows = df.height`; `if n_rows < _MIN_ROWS or len(frame.columns) < 2: return []`. Native: `arrays = [frame.column(c).to_arrow() for c in cols]`; `fd_violation_rows(arrays[i], arrays[j])` uses those arrays (unchanged); `except:` → `self._python(df, cols, n_rows)`. **Samples through the seam:** before the finding loop's sample line, the loop body currently does `samples = [f"{det}={df[det][r]!r} has {dep}={df[dep][r]!r}" for r in rows]`. Change to pull the two columns once inside the loop iteration: `det_col = frame.column(det); dep_col = frame.column(dep)` then `samples = [f"{det}={det_col.get(r)!r} has {dep}={dep_col.get(r)!r}" for r in rows]`. Everything else in the Finding UNCHANGED.

- [ ] **Step 4: Port `functional_dependencies.py` (the bridge).**
  - Remove `from goldencheck._polars_lazy import pl`; ADD `from goldencheck.core.frame import to_frame`.
  - `functional_dependencies(df, *, min_confidence=0.95)`: keep the public param `df`; add `frame = to_frame(df)` as the first line; `n = frame.height`; `if n < 2 or len(frame.columns) < 2: return []` (was `df.height`/`df.width`). Call `_strict_pairs(frame, n)` and `_approx_triples(frame, n, min_confidence)`. The det-grouping / confidence-merge / sort / output logic UNCHANGED.
  - `_strict_pairs(frame, n)` (was `(df, n)`): `cols = _fd._select_candidates(frame.native, n)`; native `native_module().discover_functional_dependencies([frame.column(c).to_arrow() for c in cols])`; `except:` → `_fd._discover_python(frame.native, cols, n)`. Return `[(cols[i], cols[j]) for i, j in pairs]` UNCHANGED.
  - `_approx_triples(frame, n, min_conf)` (was `(df, n, min_conf)`): `cols = _afd._select_candidates(frame.native)`; native `native_module().discover_approximate_fds([frame.column(c).to_arrow() for c in cols], min_conf)`; `except:`/else → `_afd._discover_python([_afd._intern(frame.column(c).to_list()) for c in cols], n, min_conf)`. Return `[(cols[i], cols[j], 1.0 - viol / n) for i, j, viol in triples]` UNCHANGED.

- [ ] **Step 5: Run the parity gates UNEDITED.**
```bash
cd /d/show_case/gc-r2 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/relations/test_composite_key.py packages/python/goldencheck/tests/relations/test_functional_dependency.py packages/python/goldencheck/tests/relations/test_approx_fd.py packages/python/goldencheck/tests/test_functional_dependencies.py packages/python/goldencheck/tests/core/test_native_parity.py -v
```
Expected: all pass, ZERO test edits. `test_native_parity` is critical — it calls the raw-df helpers directly; if it fails, the `_select_candidates` neutral-dtype swap returned different candidates (fix the swap, never the test) or a helper signature drifted. Report the assertion diff.

- [ ] **Step 6: Confirm all four files polars-free.**
```bash
grep -REn "polars|_polars_lazy|[^a-z]pl\." packages/python/goldencheck/goldencheck/relations/composite_key.py packages/python/goldencheck/goldencheck/relations/functional_dependency.py packages/python/goldencheck/goldencheck/relations/approx_fd.py packages/python/goldencheck/goldencheck/functional_dependencies.py || echo "all 4 polars-free"
```

- [ ] **Step 7: Final verification (whole batch).**
```bash
cd /d/show_case/gc-r2 && <preamble>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v      # import gate green
$PY -m pytest packages/python/goldencheck/tests -q                               # full suite
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Expected: import gate green; full suite green (report exact passed/skipped counts). If any FAILURE, investigate + report which test (do NOT edit tests to pass).

- [ ] **Step 8: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/relations/composite_key.py packages/python/goldencheck/goldencheck/relations/functional_dependency.py packages/python/goldencheck/goldencheck/relations/approx_fd.py packages/python/goldencheck/goldencheck/functional_dependencies.py
git commit -m "refactor(goldencheck): port composite_key/functional_dependency/approx_fd + bridge onto the Frame seam (polars-free)"
```

---

## Done criteria (R2 complete)
- [ ] `Column` seam gained `to_arrow()` + `get()` (import gate green — no `pl.` refs).
- [ ] `composite_key`, `functional_dependency`, `approx_fd`, and the `functional_dependencies` bridge are grep-clean of `polars`/`pl.`; native fast path + approx_fd samples route through the seam; parity-locked helpers reached via `frame.native`; `_supported()` pl-tuples replaced by neutral-string frozensets via `_neutral_dtype`.
- [ ] All parity gates (the 3 relations tests + the bridge test + `test_native_parity`) pass with ZERO edits.
- [ ] Full suite green; `import goldencheck` loads zero Polars.
- [ ] No scope creep: `test_native_parity.py`/`kernels.py` untouched; R3/R4, the substrate backend, reader, and deps flip untouched. 4 of 4 R2 profilers + bridge seam-routed; R3 (two-column element-wise) is next.
