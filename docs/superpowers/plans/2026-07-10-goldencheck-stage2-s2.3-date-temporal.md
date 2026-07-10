# GoldenCheck Stage-2 S2.3 (str_to_date chrono kernel + date-typed PyColumn + temporal) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the 4th hard profiler — `temporal` — polars-free byte-identically, by adding a native `chrono` `str_to_date` kernel + a date-typed `PyColumn` surface (`dtype` date/datetime, `str_to_date`, `gt_mask`, `fill_null`, `sum`, `cast("str")`) and wiring `temporal.profile(frame)` into `scan_columns`. Completes the Stage-2 covered substrate.

**Architecture:** A pyo3-free `goldencheck-core/src/date.rs` (chrono — same engine as Polars) returns canonical ISO strings; the `PyColumn.str_to_date` seam converts them to `datetime.date` objects so real date `>`/`str()` match Polars. Temporal is a whole-frame `profile(frame)` profiler run once in `scan_columns`, gated on `native_enabled("str_to_date")`.

**Tech Stack:** Rust (`chrono`, pyo3, goldencheck-core/native), Python 3.11+, pytest, cargo.

**Spec:** `docs/superpowers/specs/2026-07-10-goldencheck-stage2-s2.3-date-temporal-design.md`

---

## PRE-FLIGHT (before Task 1 — base must have S2.2's PyColumn/scan_columns)

This branch was cut from the S2.2 branch tip, so S2.2's code is ALREADY present here (built locally atop S2.2). Confirm:
```bash
cd /d/show_case/gc-s23
grep -q 'def value_counts_desc' packages/python/goldencheck/goldencheck/core/frame.py \
  && grep -q '_HARD_PROFILERS' packages/python/goldencheck/goldencheck/engine/scanner.py \
  && echo "S2.2 backend present -- proceed" || echo "S2.2 MISSING -- wrong base, STOP"
```
**Rebase-later note:** S2.2 (#1635) is in the merge queue. When it merges, rebase this branch onto fresh `origin/main` before opening the PR (`git fetch origin main -q && git rebase origin/main`). Until then, build/test locally atop S2.2 (this is valid — S2.2 is byte-identical + tested). If S2.2 gets changes before merge, re-rebase.

## Conventions (this plan runs in the `gc-s23` worktree)

Branch `feat/goldencheck-stage2-s3-date-temporal`, worktree `D:\show_case\gc-s23`.

**Python test preamble** (run from `/d/show_case/gc-s23`):
```bash
export PYTHONPATH="D:/show_case/gc-s23/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENCHECK_NATIVE=auto
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
$PY -c "import goldencheck; print(goldencheck.__file__)"   # MUST be under gc-s23
```
Ruff (100-char): `$PY -m ruff check <paths>`.

**Rust toolchain** (off default PATH on this box):
```bash
export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH"
export CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup
which cargo && cargo --version    # must succeed
```

**Native build** (Task 1 onward — the parity tests need `goldencheck._native` importable HERE with the new symbol):
```bash
cd /d/show_case/gc-s23 && $PY scripts/build_goldencheck_native.py
# Windows: cargo emits _native.dll and the script targets .so/.dylib; if import fails, copy:
cp packages/rust/extensions/goldencheck-native/target/release/_native.dll \
   packages/python/goldencheck/goldencheck/_native.pyd
$PY -c "import goldencheck._native as n; print('str_to_date present:', hasattr(n,'str_to_date'))"
GOLDENCHECK_NATIVE=auto $PY -c "from goldencheck.core._native_loader import native_enabled; print('str_to_date enabled:', native_enabled('str_to_date'))"
```
Do NOT commit `_native.pyd` or `target/`. If the local Windows build genuinely can't be made to work, mark native-dependent test steps CI-verified and say so — do NOT fake a pass.

**Rust discipline:** after Rust edits, `cargo build --release` + grep `^error`; `rustfmt` the touched `.rs` files BY NAME; `cargo clippy -p goldencheck-core`.

**INVARIANTS:**
- Byte-identical: `temporal` produces IDENTICAL `Finding`s via the native date-backed `PyFrame` path vs `PolarsFrame`. `Finding` is a plain `@dataclass` — compare with `==`.
- Existing tests pass UNEDITED (regression gate): `relations/temporal.py`'s tests + `tests/core/test_native_parity.py` + S2.1/S2.2 tests. EXCEPTION (contract-tracking infra edit, like S2.2's S2.1-test update): the S2.2 `scan_columns` parity tests are updated to ALSO expect temporal's findings when native is present — Task 3.
- `import goldencheck` loads ZERO polars. `scan_dataframe` unchanged.
- Commit per task; do NOT push (rebase onto main after S2.2 merges, THEN PR).

**S2.2 backend facts (present in this base):** `PyColumn` has `dtype` (str/int/float/bool/other; bool-before-int; all-None→"other"), regex ops, `eq`, `filter_by` (`[v for v,m in zip(self._v, mask._v) if m]`), `value_counts_desc`; plus `NativeRequiredError`, `_VC_KEY`, `_regex_kernel()`, and imports `from goldencheck.core._native_loader import native_enabled, native_module`. `PolarsColumn.str_to_date` = `s.str.to_date(format=fmt, strict=strict)`, `.gt_mask` = `s>other._s`, `.fill_null` = `s.fill_null(v)`, `.sum` = `s.sum()`, `.cast` = `s.cast(_CAST_KIND[kind], strict=)`.

---

## Task 1: native `str_to_date` chrono kernel (core + shim + loader)

**Files:**
- Modify: `packages/rust/extensions/goldencheck-core/Cargo.toml` (add `chrono`)
- Create: `packages/rust/extensions/goldencheck-core/src/date.rs`
- Modify: `packages/rust/extensions/goldencheck-core/src/lib.rs` (mod + re-export)
- Create: `packages/rust/extensions/goldencheck-native/src/date.rs`
- Modify: `packages/rust/extensions/goldencheck-native/src/lib.rs` (mod + register)
- Modify: `packages/python/goldencheck/goldencheck/core/_native_loader.py` (`_COMPONENT_SYMBOLS["str_to_date"]`)

- [ ] **Step 1: `goldencheck-core/Cargo.toml`** — add to `[dependencies]`:
```toml
# Same engine Polars uses for str.to_date, so date parsing is byte-identical.
chrono = { version = "0.4", default-features = false, features = ["std"] }
```

- [ ] **Step 2: Create `goldencheck-core/src/date.rs`** (pyo3-free):
```rust
//! Pyo3-free date kernel mirroring Polars' `str.to_date(strict=False)` (both back
//! onto `chrono`, so parse-validity + canonical output are byte-identical). Nulls
//! and unparseable strings map to `None`; parsed dates re-emit as canonical ISO
//! `%Y-%m-%d` (the seam converts to `datetime.date`).
use chrono::NaiveDate;

pub fn str_to_date(values: &[Option<String>], fmt: &str) -> Vec<Option<String>> {
    values
        .iter()
        .map(|v| match v.as_deref() {
            None => None,
            Some(s) => NaiveDate::parse_from_str(s, fmt)
                .ok()
                .map(|d| d.format("%Y-%m-%d").to_string()),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn v(xs: &[Option<&str>]) -> Vec<Option<String>> { xs.iter().map(|x| x.map(String::from)).collect() }

    #[test]
    fn parses_valid_and_nulls_failures() {
        let data = v(&[Some("2021-01-05"), Some("2021-1-5"), Some("2021-13-01"),
                       Some("2021-02-30"), Some(""), Some("nope"), None, Some("2021-01-05x")]);
        let got = str_to_date(&data, "%Y-%m-%d");
        assert_eq!(got, v(&[Some("2021-01-05"), Some("2021-01-05"), None, None, None, None, None, None]));
    }
}
```
(Note: `"2021-1-5"` non-padded parses — chrono `%m`/`%d` accept 1-2 digits — and canonicalizes to `"2021-01-05"`. `"2021-13-01"`/`"2021-02-30"` are out-of-range → None. Trailing `"2021-01-05x"` → None (whole-string match).)

- [ ] **Step 3: `goldencheck-core/src/lib.rs`** — add `mod date;` and `pub use date::str_to_date;` (mirror the existing blocks). NOTE: this `pub use str_to_date` and the regex `pub use` are distinct names — no clash.

- [ ] **Step 4: Build + test core:**
```bash
cd /d/show_case/gc-s23/packages/rust/extensions/goldencheck-core
cargo test --release 2>&1 | tee /tmp/gc_core_s23.log; grep -E "^error|test result:" /tmp/gc_core_s23.log
```
Expected: the new test passes, `test result: ok`. `rustfmt src/date.rs`; `cargo clippy --release` (no `^error`/`^warning` from your code).

- [ ] **Step 5: Create `goldencheck-native/src/date.rs`** (shim; fully-qualified to avoid the E0255 name clash S2.2 hit):
```rust
//! PyO3 shim over `goldencheck_core::date`. Input: Python `list[str | None]` +
//! a format string -> `list[str | None]` (canonical ISO or None).
use pyo3::prelude::*;

#[pyfunction]
pub fn str_to_date(values: Vec<Option<String>>, fmt: &str) -> Vec<Option<String>> {
    goldencheck_core::str_to_date(&values, fmt)
}
```

- [ ] **Step 6: `goldencheck-native/src/lib.rs`** — add `mod date;` with the other `mod` lines and register inside `#[pymodule] fn _native`:
```rust
m.add_function(wrap_pyfunction!(date::str_to_date, m)?)?;
```

- [ ] **Step 7: `_native_loader.py`** — add to `_COMPONENT_SYMBOLS`:
```python
    "str_to_date": ("str_to_date",),
```

- [ ] **Step 8: Build native + verify symbol:**
```bash
cd /d/show_case/gc-s23 && $PY scripts/build_goldencheck_native.py    # + .dll->.pyd copy if needed
$PY -c "import goldencheck._native as n; assert hasattr(n,'str_to_date'); print('sd:', n.str_to_date(['2021-01-05','nope',None], '%Y-%m-%d'))"
GOLDENCHECK_NATIVE=auto $PY -c "from goldencheck.core._native_loader import native_enabled; print('str_to_date enabled:', native_enabled('str_to_date'))"
```
Expected: `sd: ['2021-01-05', None, None]`; `str_to_date enabled: True`. (If Windows build fails after reasonable effort, report DONE_WITH_CONCERNS — crates committed + `cargo test` green, Python-facing verify CI-deferred. Do NOT fabricate.)

- [ ] **Step 9: Commit** (source only; verify no `target/`/`.pyd` staged via `git status`):
```bash
cd /d/show_case/gc-s23
git add packages/rust/extensions/goldencheck-core packages/rust/extensions/goldencheck-native packages/python/goldencheck/goldencheck/core/_native_loader.py
git status
git commit -m "feat(goldencheck-native): S2.3 str_to_date chrono kernel + loader gate"
```

---

## Task 2: date-typed `PyColumn` surface (dtype + str_to_date + gt_mask/fill_null/sum/cast)

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/frame.py`
- Test: `packages/python/goldencheck/tests/core/test_frame.py`

- [ ] **Step 1: Write failing backend tests** (append to `tests/core/test_frame.py`; native-guarded so a no-native env skips):
```python
@pytest.mark.skipif(not native_enabled("str_to_date"), reason="needs native date kernel")
def test_pycolumn_str_to_date_and_date_ops_match_polars():
    import polars as pl
    from datetime import date
    from goldencheck.core.frame import PolarsFrame, PyFrame
    d = {"s": ["2021-01-05", "2021-1-5", "2021-13-01", "2021-02-30", "", "nope", None, "2020-06-01x"]}
    pol = PolarsFrame(pl.DataFrame(d)).column("s")
    pyf = PyFrame.from_columns(d).column("s")
    pol_dates = pol.str_to_date("%Y-%m-%d", strict=False)
    pyf_dates = pyf.str_to_date("%Y-%m-%d", strict=False)
    assert pyf_dates.to_list() == pol_dates.to_list()          # datetime.date | None, byte-identical
    assert pyf_dates.dtype == pol_dates.dtype == "date"
    # date ops on two date columns
    dd = {"a": ["2021-05-01", "2021-01-01", None], "b": ["2021-01-01", "2021-06-01", "2021-01-01"]}
    pa = PolarsFrame(pl.DataFrame(dd)); ya = PyFrame.from_columns(dd)
    results = {}
    for tag, fr in (("pol", pa), ("py", ya)):
        A = fr.column("a").str_to_date("%Y-%m-%d", strict=False)
        B = fr.column("b").str_to_date("%Y-%m-%d", strict=False)
        mask = A.gt_mask(B).fill_null(False)
        results[tag] = (mask.to_list(), mask.sum(), A.filter_by(mask).cast("str").to_list())
    assert results["pol"] == results["py"]
```
(Use a local dict keyed by backend — do NOT stash onto the frame instance: `PolarsFrame`/`PyFrame` use `__slots__`, so `fr._vc = ...` would raise `AttributeError` and false-RED the test.)

- [ ] **Step 2: Run → FAIL** (`AttributeError: 'PyColumn' object has no attribute 'str_to_date'`):
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py -k str_to_date_and_date_ops -v
```

- [ ] **Step 3: Implement in `core/frame.py`.** Add `from datetime import date, datetime` at the top (with the other imports). Add a `_date_kernel()` module helper next to `_regex_kernel()`:
```python
def _date_kernel():
    if not native_enabled("str_to_date"):
        raise NativeRequiredError(
            "goldencheck native date kernel unavailable; the temporal check needs "
            "`pip install goldencheck[native]`."
        )
    return native_module()
```
EXTEND `PyColumn.dtype` (datetime BEFORE date, before the str check) — the property becomes:
```python
@property
def dtype(self) -> str:
    non_null = [v for v in self._v if v is not None]
    if not non_null:
        return "other"
    first = non_null[0]
    if isinstance(first, bool):
        return "bool"
    if isinstance(first, datetime):     # datetime subclasses date -> check first
        return "datetime"
    if isinstance(first, date):
        return "date"
    if isinstance(first, int):
        return "int"
    if isinstance(first, float):
        return "float"
    if isinstance(first, str):
        return "str"
    return "other"
```
Add these methods to `PyColumn`:
```python
def str_to_date(self, fmt: str, *, strict: bool) -> PyColumn:
    if strict:
        raise NotImplementedError("goldencheck str_to_date supports strict=False only")
    iso = _date_kernel().str_to_date(self._v, fmt)
    return PyColumn([date.fromisoformat(s) if s is not None else None for s in iso])

def gt_mask(self, other: PyColumn) -> PyColumn:
    return PyColumn([None if a is None or b is None else a > b
                     for a, b in zip(self._v, other._v)])

def fill_null(self, value: Any) -> PyColumn:
    return PyColumn([value if v is None else v for v in self._v])

def sum(self) -> Any:
    return sum(v for v in self._v if v is not None)

def cast(self, kind: str, *, strict: bool = False) -> PyColumn:
    if kind != "str":
        raise NotImplementedError(f"PyColumn.cast supports 'str' only, got {kind!r}")
    return PyColumn([None if v is None else str(v) for v in self._v])
```
(`import goldencheck` must still load zero polars — `date`/`datetime` are stdlib; the loader import is already present from S2.2. Confirm the import gate in Step 4.)

- [ ] **Step 4: Run → PASS** + import gate:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/test_import_no_polars.py -v
```
If the date-ops parity assertion fails, a PyColumn op diverges — report the differing values, do NOT loosen. Ruff clean.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/core/frame.py packages/python/goldencheck/tests/core/test_frame.py
git status   # no _native.pyd staged
git commit -m "feat(goldencheck): S2.3 date-typed PyColumn ops (str_to_date/gt_mask/fill_null/sum/cast) + date dtype"
```

---

## Task 3: wire `temporal` into `scan_columns` + byte-parity gate

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/engine/scanner.py`
- Modify: `packages/python/goldencheck/tests/engine/test_scan_columns_parity.py` (S2.1) + `tests/engine/test_scan_columns_hardops_parity.py` (S2.2) — expect temporal findings when native present
- Test: `packages/python/goldencheck/tests/engine/test_scan_columns_temporal_parity.py` (new)

- [ ] **Step 1: Write the temporal byte-parity test** `tests/engine/test_scan_columns_temporal_parity.py`:
```python
"""S2.3 byte-identity gate: TemporalOrderProfiler produces identical Findings on the
native-date-backed PyFrame vs PolarsFrame, and scan_columns includes them polars-free."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck import scan_columns
from goldencheck.core._native_loader import native_enabled
from goldencheck.core.frame import PolarsFrame, PyFrame
from goldencheck.relations.temporal import TemporalOrderProfiler

pytestmark = pytest.mark.skipif(not native_enabled("str_to_date"), reason="needs native date kernel")


def _datasets():
    return [
        # start > end violations
        {"start_date": ["2021-05-01", "2021-01-01", "2021-03-01"],
         "end_date":   ["2021-01-01", "2021-06-01", "2021-02-01"]},
        # clean (start <= end)
        {"created": ["2020-01-01", "2020-02-01"], "updated": ["2020-06-01", "2020-07-01"]},
        # non-date columns -> no temporal findings
        {"name": ["a", "b"], "qty": ["1", "2"]},
        # a date pair with a null row
        {"signup": ["2021-01-01", None, "2021-09-01"], "last_login": ["2020-01-01", "2021-01-01", "2021-10-01"]},
    ]


@pytest.mark.parametrize("data", _datasets())
def test_temporal_backend_parity(data):
    pol = TemporalOrderProfiler().profile(PolarsFrame(pl.DataFrame(data)))
    pyf = TemporalOrderProfiler().profile(PyFrame.from_columns(data))
    assert pyf == pol


@pytest.mark.parametrize("data", _datasets())
def test_scan_columns_includes_temporal(data):
    from goldencheck.engine.scanner import _HARD_PROFILERS, _MECHANICAL_PROFILERS
    pol = PolarsFrame(pl.DataFrame(data))
    expected = []
    for name in data:
        for profiler in (*_MECHANICAL_PROFILERS, *_HARD_PROFILERS):
            expected.extend(profiler.profile(pol, name))
    expected.extend(TemporalOrderProfiler().profile(pol))
    assert scan_columns(data) == expected
```
(This `expected` uses `_HARD_PROFILERS` unconditionally + temporal gated on `native_enabled("str_to_date")`. Assumption: regex + str_to_date ship in the SAME native module, so both symbols are always present together — matches the S2.2 hardops test's unconditional `_HARD_PROFILERS`. If the symbols were ever split across separate native builds, these `expected` builders would need per-component gates.)

- [ ] **Step 2: Run → FAIL** (temporal findings missing from `scan_columns`):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_scan_columns_temporal_parity.py -v
```
Expected: `test_temporal_backend_parity` may already PASS (profiler runs directly on both frames); `test_scan_columns_includes_temporal` FAILS (scan_columns doesn't run temporal yet).

- [ ] **Step 3: Edit `scanner.py`.** `TemporalOrderProfiler` is already imported (used by `scan_dataframe`). In `scan_columns`, after the per-column loop, append temporal gated on the date kernel:
```python
    findings: list[Finding] = []
    for name in columns:
        for profiler in profilers:
            findings.extend(profiler.profile(frame, name))
    if native_enabled("str_to_date"):
        findings.extend(TemporalOrderProfiler().profile(frame))
    else:
        logger.info(
            "scan_columns: native date kernel unavailable; skipping the temporal-order "
            "check. Install with `pip install goldencheck[native]`."
        )
    return findings
```
(Keep the S2.2 regex-gate + skip-log for the hard-3 as-is above the loop; this adds the temporal gate after it.)

- [ ] **Step 4: Update the S2.1 + S2.2 scan_columns parity tests** so their `expected` also runs temporal when native present (temporal fires on any date-pair columns; even if the S2.1/S2.2 datasets have none, `TemporalOrderProfiler().profile(pol)` returns `[]` and appending it is a harmless no-op that keeps `expected` in lockstep with `scan_columns`). In BOTH `tests/engine/test_scan_columns_parity.py::test_scan_columns_matches_polars_covered_output` AND `tests/engine/test_scan_columns_hardops_parity.py::test_scan_columns_includes_hard_checks`, after the covered/hard profiler loop that builds `expected`, add:
```python
    from goldencheck.relations.temporal import TemporalOrderProfiler
    if native_enabled("str_to_date"):
        expected.extend(TemporalOrderProfiler().profile(pol))
```
(These files already import `native_enabled` — S2.2 added it to the S2.1 test; the S2.2 hardops test has `pytestmark` skipif on `native_enabled("regex")`, so add the `native_enabled` import if missing.) This is the same contract-tracking discipline as S2.2's S2.1-test update.

- [ ] **Step 5: Run → PASS** (temporal parity + S2.1 + S2.2 parity + import gate):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/ packages/python/goldencheck/tests/test_import_no_polars.py -v
```
If `test_temporal_backend_parity` fails, a date op diverges — report the differing Findings; do NOT loosen. Ruff clean on scanner.py + the 3 test files.

- [ ] **Step 6: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/engine/scanner.py packages/python/goldencheck/tests/engine/
git status
git commit -m "feat(goldencheck): S2.3 scan_columns runs temporal check polars-free (native-gated)"
```

---

## Task 4: nopolars-lane + import-blocker (temporal polars-free) + final verification

**Files:**
- Modify: `packages/python/goldencheck/tests/nopolars/test_polars_absent.py`
- Modify: `packages/python/goldencheck/tests/test_import_no_polars.py`

- [ ] **Step 1: Append a temporal covered-scan test** to `tests/nopolars/test_polars_absent.py` (skips if native absent):
```python
def test_temporal_check_runs_without_polars() -> None:
    import pytest
    from goldencheck.core._native_loader import native_enabled
    if not native_enabled("str_to_date"):
        pytest.skip("nopolars lane without native date kernel; temporal skips by design")
    from goldencheck import scan_columns

    findings = scan_columns({
        "start_date": ["2021-05-01", "2021-01-01"],
        "end_date": ["2021-01-01", "2021-06-01"],
    })
    checks = {f.check for f in findings}
    assert "temporal_order" in checks
    assert "polars" not in sys.modules
```

- [ ] **Step 2: Append an import-blocker test** to `tests/test_import_no_polars.py` (skips cleanly if native not built; mirror S2.2's `test_scan_columns_hard_checks_with_polars_unimportable`):
```python
def test_temporal_check_with_polars_unimportable():
    import importlib.util
    if importlib.util.find_spec("goldencheck._native") is None and importlib.util.find_spec("goldencheck_native") is None:
        import pytest
        pytest.skip("native kernel not built; temporal polars-free path is CI-parity-lane verified")
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
        "fs = scan_columns({'start_date': ['2021-05-01','2021-01-01'], 'end_date': ['2021-01-01','2021-06-01']})\n"
        "checks = {f.check for f in fs}\n"
        "assert 'temporal_order' in checks, checks\n"
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
Expected: import-gate tests pass (new temporal blocker PASSES if native built, else skips); nopolars-module tests SKIP locally (polars present). Report which path each native-dependent test took.

- [ ] **Step 4: Final whole-batch verification** (report every result line):
```bash
cd /d/show_case/gc-s23   # (python + native already built)
$PY -m pytest packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests/core/test_frame.py packages/python/goldencheck/tests/engine/ -v
$PY -m pytest packages/python/goldencheck/tests -k "temporal" -v          # existing temporal tests UNEDITED
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup
cd packages/rust/extensions/goldencheck-core && cargo test --release 2>&1 | grep -E "^error|test result:"
```
Expected: import gate green (incl. all S2.1/S2.2/S2.3 blockers); backend + engine parity suites green; existing temporal tests green UNEDITED; ruff clean; `cargo test result: ok`. Do NOT run the full goldencheck suite (OOM). Report exact pass/skip counts.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/tests/nopolars/test_polars_absent.py packages/python/goldencheck/tests/test_import_no_polars.py
git status
git commit -m "test(goldencheck): S2.3 covered-scan proof -- temporal runs polars-free with native (lane + blocker)"
```

- [ ] **Step 6 (CI):** The advisory `goldencheck_nopolars` lane already builds native (S2.2 Task 6), so the new `test_temporal_check_runs_without_polars` runs there automatically — NO ci.yml change needed. Confirm by reading the job: it does `build_goldencheck_native.py` before pytest. If (and only if) it does not, add the build step per S2.2 Task 6. Note the finding in the final report.

---

## Done criteria (S2.3 complete → Stage-2 substrate DONE)
- [ ] Native `str_to_date` chrono kernel in goldencheck-core + shim + `_COMPONENT_SYMBOLS["str_to_date"]`; `native_enabled("str_to_date")` True when built.
- [ ] `PyColumn`: `dtype` reports `"date"`/`"datetime"` for date/datetime values; `str_to_date` (native-guarded, returns `datetime.date` column), `gt_mask`, `fill_null`, `sum`, `cast("str")` — all byte-identical to Polars.
- [ ] `scan_columns` runs `temporal` polars-free when the date kernel is present (whole-frame, once, after the per-column loop), skips-with-log otherwise; byte-parity proven vs Polars across violation/clean/non-date/null-row datasets.
- [ ] S2.1 + S2.2 scan_columns parity tests updated to keep `expected` in lockstep (temporal appended when native present); existing `temporal` tests pass UNEDITED.
- [ ] nopolars lane + import-blocker prove temporal runs polars-free with native; skip cleanly without.
- [ ] Existing suite green; `scan_dataframe` unchanged; `import goldencheck` loads zero Polars; Rust builds clean.
- [ ] Scope: NO date arithmetic, NO general cast, NO strict=True. **Stage-2 covered substrate is now complete** (all column + relation profilers that can run polars-free byte-identically do; remaining program = reader + P4 deps-flip).
