# GoldenCheck Stage-2 S2.2 (native regex kernel + pure-Python value_counts + PyColumn.dtype) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `encoding_detection`, `format_detection`, and `pattern_consistency` run polars-free byte-identically via `scan_columns` — by adding a native Rust `regex` kernel (same engine as Polars), a pure-Python `value_counts_desc` with a shared deterministic total order, and a `PyColumn.dtype` that matches Polars' inference.

**Architecture:** Three regex ops (`str_match_count`/`str_filter`/`str_replace_all`) get pyo3-free kernels in `goldencheck-core` (the `regex` crate — Polars uses the same crate, so byte-identical), wrapped by thin `#[pyfunction]` shims in `goldencheck-native`, gated through the existing `_native_loader` as a new `regex` component. `value_counts_desc` becomes pure Python (Counter + a shared null-safe `(count DESC, nulls-last, value ASC)` key applied to BOTH backends, de-flaking pattern_consistency). `PyColumn` gains `dtype` (str-gate-sufficient inference, byte-identical to `_neutral_dtype(pl.DataFrame(d)[c].dtype)`) + the 4 hard ops (regex ops native-guarded). `scan_columns` appends the 3 hard profilers when `native_enabled("regex")`, else skips-with-a-log.

**Tech Stack:** Rust (`regex` crate, pyo3 abi3, `goldencheck-core`/`goldencheck-native`), Python 3.11+, pytest, maturin/cargo.

**Spec:** `docs/superpowers/specs/2026-07-10-goldencheck-stage2-s2.2-native-hardops-design.md` (read the PLANNING AMENDMENT at top — temporal + str_to_date are S2.3, NOT this plan).

---

## PRE-FLIGHT (before Task 1 — the branch must be on main-with-S2.1)

S2.2 EXTENDS S2.1's `PyColumn`/`PyFrame`/`scan_columns` (`core/frame.py`, `engine/scanner.py`). The branch currently carries only the spec + this plan, off pre-S2.1 `origin/main`. Rebase onto main-with-S2.1 first:
```bash
cd /d/show_case/gc-s22
git fetch origin main -q
# Confirm S2.1 (#1630) landed — PyColumn + scan_columns must be on main:
git show origin/main:packages/python/goldencheck/goldencheck/core/frame.py | grep -q "class PyColumn" \
  && echo "S2.1 present on main" || echo "S2.1 NOT on main yet -- WAIT, do not proceed"
git rebase origin/main
```
If S2.1 is NOT yet on main, STOP and wait — Tasks 3-6 edit files S2.1 created (`PyColumn`, `scan_columns`); a pre-S2.1 base cannot apply them.

## Conventions (this plan runs in the `gc-s22` worktree)

Branch `feat/goldencheck-stage2-s2.2-native-hardops`, worktree `D:\show_case\gc-s22`.

**Python test preamble** (run test commands from `/d/show_case/gc-s22`):
```bash
export PYTHONPATH="D:/show_case/gc-s22/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-s22
```
Ruff (100-char): `$PY -m ruff check <paths>`.

**Native build** (Task 2 onward — the parity tests need `goldencheck._native` importable in THIS worktree):
```bash
cd /d/show_case/gc-s22
$PY scripts/build_goldencheck_native.py       # cargo build --release + drop the artifact
# The script targets lib_native.so/.dylib. On WINDOWS cargo emits _native.dll and the
# script may not place it — if `import goldencheck._native` still fails after the script,
# copy the artifact manually:
cp packages/rust/extensions/goldencheck-native/target/release/_native.dll \
   packages/python/goldencheck/goldencheck/_native.pyd
$PY -c "import goldencheck._native as n; print(n.__version__, [s for s in dir(n) if not s.startswith('_')])"
```
Verify each new symbol appears in that `dir()` list before relying on it. If the native build cannot be made to work locally on Windows, mark the affected Task's native-dependent test steps as CI-verified (the CI parity lane with `GOLDENCHECK_NATIVE=1` builds on Linux) and say so explicitly in the task report — do NOT claim a native path is verified when it was not built.

**Rust build/lint discipline** (see @reference_rust_fmt_box_and_nonrequired_gate, @feedback_verify_rust_builds_explicitly): after Rust edits, `cargo build --release` from the crate dir and grep the output for `^error`; run `rustfmt` on the touched `.rs` files BY NAME (not `cargo fmt` — avoids whole-crate churn); `cargo clippy -p goldencheck-core` for the pyo3-free crate.

**INVARIANTS:**
- Byte-identical: each hard profiler produces IDENTICAL `Finding`s via the native-backed `PyColumn` path vs the `PolarsFrame` path (the parity gate). `Finding` is a plain `@dataclass` — compare with `==`.
- Existing tests pass UNEDITED (regression gate): `encoding_detection`, `format_detection`, `pattern_consistency` test files + `tests/core/test_native_parity.py` + the S2.1 tests. If `value_counts_desc`'s new order changes a `pattern_consistency` assertion, that test relied on nondeterministic tie-order → investigate, do NOT loosen.
- `import goldencheck` loads ZERO polars (the S2.1 import gate stays green). `scan_dataframe` (Polars path) unchanged except `value_counts_desc`'s deterministic secondary sort (guarded by the unedited-tests check).
- Commit per task; do NOT push.

**Seam facts (S1.1 backend, verified):** `PyColumn` implements only the 7 mechanical ops + NO dtype. `PolarsColumn.str_match_count` = `int(s.str.contains(pattern).sum())`; `.str_filter` = `s.filter(mask if matching else ~mask)`, `mask=s.str.contains(pattern)`; `.str_replace_all` = `s.str.replace_all(pattern, value)`; `.value_counts_desc` = `s.value_counts().sort("count", descending=True)` then `zip(vc[name].to_list(), vc["count"].to_list())`; `.dtype` = `_neutral_dtype(s.dtype)`. `_neutral_dtype` maps `pl.Utf8/String→"str"`, `Int*→"int"`, `UInt*→"uint"`, `Float*→"float"`, `Date→"date"`, `Datetime→"datetime"`, `Boolean→"bool"`, else `"other"`.

**Loader facts:** `native_module()` returns the `_native` module or None; `native_enabled(component)` returns True iff not disabled AND all `_COMPONENT_SYMBOLS[component]` symbols are present. Add a `"regex"` entry to `_COMPONENT_SYMBOLS`. Env `GOLDENCHECK_NATIVE`: `0`=off, `1`=require, `auto`/unset=use-if-present.

---

## Task 1: `PyColumn.dtype` (str-gate-sufficient, byte-identical to Polars inference)

The 3 hard profilers early-return on `col.dtype != "str"`. `PyColumn` has no `dtype`. This is FIRST because every later parity test depends on it.

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py` (add `dtype` property to `PyColumn`)
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Write the failing dtype-parity test** (append to `tests/core/test_frame.py`):
```python
def test_pycolumn_dtype_matches_polars_inference():
    import polars as pl
    from goldencheck.core.frame import PolarsFrame, PyFrame
    datasets = [
        {"s": ["a", "b", None, "c"]},         # str + null   -> "str"
        {"s": ["a", "b", "c"]},               # str          -> "str"
        {"i": [1, 2, None, 3]},               # int + null   -> "int"
        {"f": [1.0, 2.5, 3.0]},               # float        -> "float"
        {"b": [True, False, True]},           # bool         -> "bool"
        {"n": [None, None]},                  # all-null      -> "other" (pl.Null)
    ]
    for d in datasets:
        col = next(iter(d))
        pol = PolarsFrame(pl.DataFrame(d)).column(col).dtype
        pyf = PyFrame.from_columns(d).column(col).dtype
        assert pyf == pol, (d, pyf, pol)
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PyColumn' object has no attribute 'dtype'`):
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k dtype_matches_polars -v
```

- [ ] **Step 3: Add the `dtype` property to `PyColumn`** in `core/frame.py` (place after `to_list`). Bool MUST be checked before int (`isinstance(True, int)` is True in Python):
```python
@property
def dtype(self) -> str:
    non_null = [v for v in self._v if v is not None]
    if not non_null:
        return "other"                      # Polars infers pl.Null -> _neutral_dtype -> "other"
    first = non_null[0]
    if isinstance(first, bool):
        return "bool"
    if isinstance(first, int):
        return "int"
    if isinstance(first, float):
        return "float"
    if isinstance(first, str):
        return "str"
    return "other"
```
Scope note (YAGNI): this classifies by the first non-null value — sufficient because `scan_columns` columns are homogeneous (like `pl.DataFrame(dict)`). Do NOT build a full type-inference engine; the parity test above is the contract on the covered corpus.

- [ ] **Step 4: Run → PASS** + S1.1 import gate green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean on `core/frame.py` + `tests/core/test_frame.py`.

- [ ] **Step 5: Commit.**
```bash
cd /d/show_case/gc-s22
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): S2.2 PyColumn.dtype -- str-gate inference byte-identical to Polars"
```

---

## Task 2: native `regex` kernel (goldencheck-core pyo3-free + goldencheck-native shim + loader)

**Files:**
- Create: `packages/rust/extensions/goldencheck-core/src/regex.rs`
- Modify: `packages/rust/extensions/goldencheck-core/src/lib.rs` (mod + re-export)
- Modify: `packages/rust/extensions/goldencheck-core/Cargo.toml` (add `regex`)
- Create: `packages/rust/extensions/goldencheck-native/src/regex.rs`
- Modify: `packages/rust/extensions/goldencheck-native/src/lib.rs` (mod + register 3 fns)
- Modify: `packages/python/goldencheck/goldencheck/core/_native_loader.py` (`_COMPONENT_SYMBOLS["regex"]`)
- Test: `packages/rust/extensions/goldencheck-core/src/regex.rs` (inline `#[cfg(test)]`)

- [ ] **Step 1: `goldencheck-core/Cargo.toml`** — add to `[dependencies]`:
```toml
# Same engine Polars uses for str.contains/replace_all, so regex ops are
# byte-identical. Already resolved transitively (1.12.x) in the native lock.
regex = "1"
```

- [ ] **Step 2: Write `goldencheck-core/src/regex.rs`** (pyo3-free; null = `None`, nulls never match, pass through on replace):
```rust
//! Pyo3-free regex kernels mirroring Polars' `str.contains` / `str.replace_all`
//! (both back onto the `regex` crate, so these are byte-identical). Nulls (`None`)
//! never match and pass through unchanged on replace.
use regex::Regex;

/// Count of non-null values matching `pattern` (mirrors `s.str.contains(p).sum()`).
pub fn str_contains_count(values: &[Option<String>], pattern: &str) -> Result<usize, regex::Error> {
    let re = Regex::new(pattern)?;
    Ok(values.iter().filter(|v| v.as_deref().is_some_and(|s| re.is_match(s))).count())
}

/// Three-valued match mask: `None` for a null element, else `Some(is_match)`.
/// The Python seam excludes `None` unconditionally (Polars' `filter` drops null-mask rows).
pub fn str_filter_mask(values: &[Option<String>], pattern: &str) -> Result<Vec<Option<bool>>, regex::Error> {
    let re = Regex::new(pattern)?;
    Ok(values.iter().map(|v| v.as_deref().map(|s| re.is_match(s))).collect())
}

/// Element-wise `regex::replace_all` (mirrors `s.str.replace_all`); nulls pass through.
pub fn str_replace_all(values: &[Option<String>], pattern: &str, replacement: &str) -> Result<Vec<Option<String>>, regex::Error> {
    let re = Regex::new(pattern)?;
    Ok(values.iter().map(|v| v.as_deref().map(|s| re.replace_all(s, replacement).into_owned())).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn v(xs: &[Option<&str>]) -> Vec<Option<String>> { xs.iter().map(|x| x.map(String::from)).collect() }

    #[test]
    fn counts_non_null_matches() {
        let data = v(&[Some("aXb"), Some("cd"), None, Some("X")]);
        assert_eq!(str_contains_count(&data, "X").unwrap(), 2);
    }
    #[test]
    fn mask_is_three_valued() {
        let data = v(&[Some("X"), Some("y"), None]);
        assert_eq!(str_filter_mask(&data, "X").unwrap(), vec![Some(true), Some(false), None]);
    }
    #[test]
    fn replace_passes_nulls_through() {
        let data = v(&[Some("a1b2"), None]);
        assert_eq!(str_replace_all(&data, r"\d", "D").unwrap(), v(&[Some("aDbD"), None]));
    }
    #[test]
    fn unicode_letter_class_matches_polars_semantics() {
        // \p{L} is the class Polars uses; ASCII letters -> L, digits untouched.
        let data = v(&[Some("Ab12")]);
        assert_eq!(str_replace_all(&data, r"\p{L}", "L").unwrap(), v(&[Some("LL12")]));
    }
}
```

- [ ] **Step 3: `goldencheck-core/src/lib.rs`** — add `mod regex;` and `pub use regex::{str_contains_count, str_filter_mask, str_replace_all};` (mirror the existing `mod`/`pub use` block).

- [ ] **Step 4: Build + test the core crate:**
```bash
cd /d/show_case/gc-s22/packages/rust/extensions/goldencheck-core
cargo test --release 2>&1 | tee /tmp/gc_core_test.log; grep -E "^error|test result:" /tmp/gc_core_test.log
```
Expected: the 4 new tests pass, `test result: ok`. Then `rustfmt src/regex.rs` and `cargo clippy` (grep `^error`/`^warning: `).

- [ ] **Step 5: Write `goldencheck-native/src/regex.rs`** (thin pyo3 shim; `Vec<Option<String>>` auto-converts from a Python `list[str|None]`):
```rust
//! PyO3 shims over `goldencheck_core::regex`. Input arrives as a Python
//! `list[str | None]` (auto -> `Vec<Option<String>>`); a bad pattern -> ValueError.
use goldencheck_core::{str_contains_count, str_filter_mask, str_replace_all};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn str_contains_count(values: Vec<Option<String>>, pattern: &str) -> PyResult<usize> {
    goldencheck_core::str_contains_count(&values, pattern).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn str_filter_mask(values: Vec<Option<String>>, pattern: &str) -> PyResult<Vec<Option<bool>>> {
    goldencheck_core::str_filter_mask(&values, pattern).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn str_replace_all(values: Vec<Option<String>>, pattern: &str, replacement: &str) -> PyResult<Vec<Option<String>>> {
    goldencheck_core::str_replace_all(&values, pattern, replacement).map_err(|e| PyValueError::new_err(e.to_string()))
}
```
(If Rust name-collision between the `use goldencheck_core::str_contains_count` import and the local `#[pyfunction] str_contains_count` is a problem, drop the `use` and fully-qualify as shown in the bodies. Prefer fully-qualified calls — remove the `use goldencheck_core::{...}` line.)

- [ ] **Step 6: `goldencheck-native/src/lib.rs`** — add `mod regex;` (with the other `mod` lines) and inside `#[pymodule] fn _native`, register:
```rust
m.add_function(wrap_pyfunction!(regex::str_contains_count, m)?)?;
m.add_function(wrap_pyfunction!(regex::str_filter_mask, m)?)?;
m.add_function(wrap_pyfunction!(regex::str_replace_all, m)?)?;
```

- [ ] **Step 7: `_native_loader.py`** — add to `_COMPONENT_SYMBOLS`:
```python
    "regex": ("str_contains_count", "str_filter_mask", "str_replace_all"),
```

- [ ] **Step 8: Build the native ext + verify symbols:**
```bash
cd /d/show_case/gc-s22 && $PY scripts/build_goldencheck_native.py    # + Windows .dll->.pyd copy if needed (see preamble)
$PY -c "import goldencheck._native as n; assert all(hasattr(n,s) for s in ('str_contains_count','str_filter_mask','str_replace_all')), dir(n); print('regex symbols present')"
$PY -c "from goldencheck.core._native_loader import native_enabled; import os; os.environ['GOLDENCHECK_NATIVE']='auto'; print('regex enabled:', native_enabled('regex'))"
```
Expected: symbols present; `regex enabled: True`. (If the local Windows build fails, report it — this task's Python-facing verification then happens in the CI parity lane. Do not fake it.)

- [ ] **Step 9: Commit.**
```bash
cd /d/show_case/gc-s22
git add packages/rust/extensions/goldencheck-core packages/rust/extensions/goldencheck-native packages/python/goldencheck/goldencheck/core/_native_loader.py
git commit -m "feat(goldencheck-native): S2.2 regex kernel (str_contains_count/filter_mask/replace_all) + loader gate"
```

---

## Task 3: `PyColumn` hard ops (regex native-guarded) + `value_counts_desc` (pure-Python, both backends)

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py` (PyColumn ops, `_VC_KEY`, rewrite `PolarsColumn.value_counts_desc`, `NativeRequiredError`)
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Write failing backend tests** (append to `tests/core/test_frame.py`). These need native built (Task 2):
```python
def test_pycolumn_regex_ops_match_polars():
    import polars as pl
    from goldencheck.core.frame import PolarsFrame, PyFrame
    d = {"s": ["aX1", "bY2", None, "Z", "cc"]}
    pol = PolarsFrame(pl.DataFrame(d)).column("s")
    pyf = PyFrame.from_columns(d).column("s")
    assert pyf.str_match_count(r"\d") == pol.str_match_count(r"\d")
    assert pyf.str_filter(r"\d", matching=True).to_list() == pol.str_filter(r"\d", matching=True).to_list()
    # matching=False MUST drop nulls (three-valued filter) -- the parity trap
    assert pyf.str_filter(r"\d", matching=False).to_list() == pol.str_filter(r"\d", matching=False).to_list()
    assert pyf.str_replace_all(r"\p{L}", "L").to_list() == pol.str_replace_all(r"\p{L}", "L").to_list()

def test_value_counts_desc_deterministic_and_backend_identical():
    import polars as pl
    from goldencheck.core.frame import PolarsFrame, PyFrame
    d = {"s": ["b", "a", "b", "a", "c", "b"]}   # counts b=3,a=2,c=1
    pol = PolarsFrame(pl.DataFrame(d)).column("s").value_counts_desc()
    pyf = PyFrame.from_columns(d).column("s").value_counts_desc()
    assert pyf == pol
    # tie case: a & b both 2 -> (count DESC, value ASC) => a before b, on BOTH backends
    d2 = {"s": ["b", "a", "b", "a"]}
    assert PolarsFrame(pl.DataFrame(d2)).column("s").value_counts_desc() == [("a", 2), ("b", 2)]
    assert PyFrame.from_columns(d2).column("s").value_counts_desc() == [("a", 2), ("b", 2)]
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PyColumn' object has no attribute 'str_match_count'`):
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k "regex_ops_match or value_counts_desc_deterministic" -v
```

- [ ] **Step 3: Implement in `core/frame.py`.** Add at module level (near `_CAST_KIND`):
```python
class NativeRequiredError(RuntimeError):
    """A covered hard op needs the native regex kernel. Install it with
    `pip install goldencheck[native]` (or build it in-tree)."""


def _VC_KEY(kv: tuple[Any, int]) -> tuple[int, bool, Any]:
    value, count = kv
    return (-count, value is None, value if value is not None else "")   # count DESC, nulls-last, value ASC
```
Add these methods to `PyColumn` (after `dtype`; needs `from collections import Counter` at top and `from goldencheck.core._native_loader import native_enabled, native_module`):
```python
def _regex_kernel():
    if not native_enabled("regex"):
        raise NativeRequiredError(
            "goldencheck native regex kernel unavailable; the encoding/format/"
            "pattern_consistency checks need `pip install goldencheck[native]`."
        )
    return native_module()

# --- on PyColumn ---
def str_match_count(self, pattern: str) -> int:
    return _regex_kernel().str_contains_count(self._v, pattern)

def str_filter(self, pattern: str, *, matching: bool) -> PyColumn:
    mask = _regex_kernel().str_filter_mask(self._v, pattern)   # list[bool | None]
    return PyColumn([v for v, m in zip(self._v, mask) if m is not None and m == matching])

def str_replace_all(self, pattern: str, value: str) -> PyColumn:
    return PyColumn(_regex_kernel().str_replace_all(self._v, pattern, value))

def value_counts_desc(self) -> list[tuple[Any, int]]:
    return sorted(Counter(self._v).items(), key=_VC_KEY)
```
(`_regex_kernel` is a module-level helper, not a method — define it at module scope.) Then **rewrite `PolarsColumn.value_counts_desc`** to use the SAME key:
```python
def value_counts_desc(self) -> list[tuple[Any, int]]:
    vc = self._s.value_counts()
    pairs = zip(vc[self._s.name].to_list(), vc["count"].to_list())   # (value, count)
    return sorted(pairs, key=_VC_KEY)
```

- [ ] **Step 4: Run → PASS** + import gate + existing value_counts consumer tests green:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -k "pattern_consistency" -v   # value_counts consumer, must stay green UNEDITED
```
If a `pattern_consistency` test changed, a tie was reordered — investigate, don't loosen. Ruff clean.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git commit -m "feat(goldencheck): S2.2 PyColumn regex ops (native-guarded) + deterministic value_counts_desc on both backends"
```

---

## Task 4: expand `scan_columns` to the 3 hard profilers (native-gated)

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/engine/scanner.py`
- Test: `packages/python/goldencheck/tests/engine/test_scan_columns_hardops_parity.py` (new)

- [ ] **Step 1: Write the byte-parity test** `tests/engine/test_scan_columns_hardops_parity.py` (needs native built):
```python
"""S2.2 byte-identity gate: encoding/format/pattern profilers produce identical Findings
on the native-backed PyFrame vs PolarsFrame (polars present). Proves scan_columns' hard-op
coverage == the Polars path, so the nopolars-lane assertions are trustworthy."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck import scan_columns
from goldencheck.core.frame import PolarsFrame, PyFrame
from goldencheck.core._native_loader import native_enabled
from goldencheck.profilers.encoding_detection import EncodingDetectionProfiler
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler

pytestmark = pytest.mark.skipif(not native_enabled("regex"), reason="needs native regex kernel")

def _datasets():
    return [
        {"email": [f"u{i}@x.com" for i in range(18)] + ["notanemail", "also bad"]},   # format: email + non-match
        {"note": ["cafe", "café", "naïve", "plain", "x​zero"]},        # encoding: non-ascii + zero-width
        {"code": ["AB-12", "CD-34", "EF-56", "X"]},                                    # pattern: skeleton LL-DD vs L
        {"nums": [1, 2, 3]},                                                            # non-str -> all 3 early-return, no findings
        {"phone": ["(555) 123-4567"] * 15 + ["555.111.2222"] * 3 + ["bad", None]},     # format phone + null (matching=False path)
    ]

@pytest.mark.parametrize("data", _datasets())
def test_hard_profilers_backend_parity(data):
    pol = PolarsFrame(pl.DataFrame(data))
    pyf = PyFrame.from_columns(data)
    for profiler in (EncodingDetectionProfiler(), FormatDetectionProfiler(), PatternConsistencyProfiler()):
        for col in data:
            assert profiler.profile(pol, col) == profiler.profile(pyf, col), (type(profiler).__name__, col)

@pytest.mark.parametrize("data", _datasets())
def test_scan_columns_includes_hard_checks(data):
    pol = PolarsFrame(pl.DataFrame(data))
    from goldencheck.engine.scanner import _MECHANICAL_PROFILERS, _HARD_PROFILERS
    expected = []
    for name in data:
        for profiler in (*_MECHANICAL_PROFILERS, *_HARD_PROFILERS):
            expected.extend(profiler.profile(pol, name))
    assert scan_columns(data) == expected
```

- [ ] **Step 2: Run → FAIL** (`ImportError: cannot import name '_HARD_PROFILERS'`):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_scan_columns_hardops_parity.py -v
```

- [ ] **Step 3: Edit `scanner.py`.** Import the 3 profiler classes if not already imported (they are — used by `COLUMN_PROFILERS`), import `native_enabled` from `goldencheck.core._native_loader`, and replace the S2.1 `_COVERED_COLUMN_PROFILERS` block + `scan_columns` with:
```python
_MECHANICAL_PROFILERS = [NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()]
_HARD_PROFILERS = [EncodingDetectionProfiler(), FormatDetectionProfiler(), PatternConsistencyProfiler()]


def scan_columns(columns: dict[str, list]) -> list[Finding]:
    """Polars-free reduced scan of the covered column checks over in-memory columns.
    The mechanical checks (nullability/uniqueness/cardinality) always run; the regex
    checks (encoding/format/pattern_consistency) run when the native regex kernel is
    available (`pip install goldencheck[native]`) and are skipped-with-a-log otherwise.
    Date/relational checks still need Polars -- use scan_dataframe for a full scan."""
    frame = PyFrame.from_columns(columns)
    profilers = list(_MECHANICAL_PROFILERS)
    if native_enabled("regex"):
        profilers += _HARD_PROFILERS
    else:
        logger.info(
            "scan_columns: native regex kernel unavailable; skipping encoding/format/"
            "pattern_consistency checks. Install with `pip install goldencheck[native]`."
        )
    findings: list[Finding] = []
    for name in columns:
        for profiler in profilers:
            findings.extend(profiler.profile(frame, name))
    return findings
```
Keep `scan_columns` in `__all__`. (The S2.1 name `_COVERED_COLUMN_PROFILERS` is removed; grep the package for other references first — there should be none outside scanner.py + its S2.1 parity test. If the S2.1 parity test `tests/engine/test_scan_columns_parity.py` imports `_COVERED_COLUMN_PROFILERS`, update that import to `_MECHANICAL_PROFILERS` — that is a test-infra rename, not a behavior change; the S2.1 assertions themselves stay.)

- [ ] **Step 4: Run → PASS** (new parity test + S2.1 parity test + import gate):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_scan_columns_hardops_parity.py packages/python/goldencheck/tests/engine/test_scan_columns_parity.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
Ruff clean on scanner.py + the new test.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/engine/scanner.py packages/python/goldencheck/tests/engine/
git commit -m "feat(goldencheck): S2.2 scan_columns runs encoding/format/pattern checks polars-free (native-gated)"
```

---

## Task 5: nopolars-lane covered-scan + import-blocker (native present, polars absent)

**Files:**
- Modify: `packages/python/goldencheck/tests/nopolars/test_polars_absent.py`
- Modify: `packages/python/goldencheck/tests/test_import_no_polars.py`

- [ ] **Step 1: Add a hard-op covered-scan test** to `tests/nopolars/test_polars_absent.py` (append; the module is skipif'd when polars is present, so it runs only in the `goldencheck_nopolars` lane). Confirm `import sys` is at module top (S2.0 added it). Guard on native being present (the lane must have native built to exercise the hard checks):
```python
def test_hard_checks_run_without_polars() -> None:
    import pytest
    from goldencheck.core._native_loader import native_enabled
    if not native_enabled("regex"):
        pytest.skip("nopolars lane without native regex kernel; hard checks skip by design")
    from goldencheck import scan_columns

    findings = scan_columns({"email": [f"u{i}@x.com" for i in range(18)] + ["bad", "worse"]})
    checks = {f.check for f in findings}
    assert "format_detection" in checks       # regex ran polars-free
    assert "polars" not in sys.modules
```

- [ ] **Step 2: Extend the import-blocker** in `tests/test_import_no_polars.py` — prove `scan_columns` runs the hard checks with polars unimportable + native present (REQUIRED suite). It must SKIP cleanly if native isn't importable in the subprocess:
```python
def test_scan_columns_hard_checks_with_polars_unimportable():
    import importlib.util
    if importlib.util.find_spec("goldencheck._native") is None and importlib.util.find_spec("goldencheck_native") is None:
        import pytest
        pytest.skip("native kernel not built; hard-op polars-free path is CI-parity-lane verified")
    code = (
        "import sys, importlib.abc\n"
        "class _B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, n, path=None, target=None):\n"
        "        if n=='polars' or n.startswith('polars.'):\n"
        "            raise ModuleNotFoundError(n)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _B())\n"
        "import os; os.environ['GOLDENCHECK_NATIVE']='auto'\n"
        "from goldencheck import scan_columns\n"
        "fs = scan_columns({'email': [f'u{i}@x.com' for i in range(18)] + ['bad','worse']})\n"
        "checks = {f.check for f in fs}\n"
        "assert 'format_detection' in checks, checks\n"
        "assert 'polars' not in sys.modules\n"
    )
    pkg_dir = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 3: Run affected tests:**
```bash
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py packages/python/goldencheck/tests/nopolars -v
```
Expected: import-gate tests pass (the new blocker test passes if native built, else skips cleanly); nopolars-module tests SKIP locally (polars present). Report which path (passed vs skipped) the native-dependent tests took.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/tests/nopolars/test_polars_absent.py packages/python/goldencheck/tests/test_import_no_polars.py
git commit -m "test(goldencheck): S2.2 covered-scan proof -- hard checks run polars-free with native (lane + blocker)"
```

---

## Task 6: advisory CI lane builds native + final verification

**Files:**
- Modify: `.github/workflows/ci.yml` (the `goldencheck_nopolars` advisory job — build native so the hard-op checks actually run there)

- [ ] **Step 1: Inspect the S2.0 `goldencheck_nopolars` job.** Read `.github/workflows/ci.yml`, find the `goldencheck_nopolars` job (added in S2.0 #1618). It uninstalls polars. For S2.2 its covered scan should also exercise the hard checks, which needs native built. Add a build step BEFORE the pytest step (mirror the pattern any existing native-parity lane uses to build `goldencheck._native`):
```yaml
      - name: Build goldencheck native (regex kernel)
        run: uv run python scripts/build_goldencheck_native.py
```
Keep it advisory (`continue-on-error` / not in `ci-required`, exactly as S2.0 left it). If the job already builds native or a simpler wiring exists, match it — do NOT restructure the job. If building native in this lane proves heavy/flaky, leave the lane as-is (mechanical-only) and instead rely on the separate native-parity lane (`GOLDENCHECK_NATIVE=1`) for the hard-op parity — note the decision in the commit message. (Workflow-file edits force all CI jobs to re-run per repo CLAUDE.md — expected.)

- [ ] **Step 2: Validate the workflow YAML parses** (a broken ci.yml = 0 jobs, per @feedback_ci_yaml_startup_failure):
```bash
$PY -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml parses')"
```

- [ ] **Step 3: Final whole-batch verification.**
```bash
cd /d/show_case/gc-s22 && <python preamble> && <native build if not already>
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v                    # import gate + blockers
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py \
              packages/python/goldencheck/tests/engine/test_scan_columns_parity.py \
              packages/python/goldencheck/tests/engine/test_scan_columns_hardops_parity.py -v  # backend + parity
$PY -m pytest packages/python/goldencheck/tests -k "encoding_detection or format_detection or pattern_consistency" -v  # existing profiler tests UNEDITED
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
cd packages/rust/extensions/goldencheck-core && cargo test --release 2>&1 | grep -E "^error|test result:"
```
Report exact pass/skip counts + confirm the existing profiler tests are green with ZERO edits. Do NOT run the full goldencheck suite locally (OOM risk per @feedback_avoid_full_suite_oom) — it runs in CI.

- [ ] **Step 4: Commit.**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(goldencheck): S2.2 build native in the nopolars advisory lane so hard-op checks run"
```

---

## Done criteria (S2.2 complete)
- [ ] Native `regex` kernel (`str_contains_count`/`str_filter_mask`/`str_replace_all`) in `goldencheck-core` + `goldencheck-native` shim + `_COMPONENT_SYMBOLS["regex"]`; `native_enabled("regex")` True when built.
- [ ] `PyColumn` has `dtype` (byte-identical to Polars inference on the covered corpus) + `str_match_count`/`str_filter` (three-valued mask)/`str_replace_all` (native-guarded, raise `NativeRequiredError` when absent) + pure-Python `value_counts_desc`.
- [ ] `value_counts_desc` deterministic `(count DESC, nulls-last, value ASC)` on BOTH backends; existing `pattern_consistency` tests pass UNEDITED.
- [ ] `scan_columns` runs encoding/format/pattern polars-free when native present, skips-with-log otherwise; byte-parity vs Polars proven per profiler across all finding branches (incl. the null + `matching=False` filter trap).
- [ ] nopolars lane + import-blocker prove the hard checks run polars-free with native; both skip cleanly when native isn't built.
- [ ] Existing suite green; `scan_dataframe` unchanged; `import goldencheck` loads zero Polars; Rust builds clean (`cargo test`/`clippy` no `^error`).
- [ ] Scope: NO str_to_date, NO date-typed ops, NO temporal wiring (all S2.3). NO full type-inference engine.
