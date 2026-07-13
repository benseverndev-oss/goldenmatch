# GoldenFlow auto-detect owned-kernel Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port GoldenFlow's zero-config auto-detect *type-inference decision* into an owned `goldenflow_core::profile` kernel, exposed over a zero-copy Arrow columnar path (`Column.profile()`) and an arrow-free list/WASM path (`infer_type`), with cross-surface byte-parity and pure-Python/TS fallbacks retained.

**Architecture:** One always-compiled `goldenflow_core::profile::infer_type(values, hint) -> String` owns the decision on every surface; `profile_column(...) -> ColumnProfileOut` wraps it with null/unique/samples for the columnar path only. The Python list path keeps null/unique/samples in Python over raw values (byte-exact, dodges the `[1,"1"]` stringify-collision). `select_transforms`, the GoldenCheck profile path, and the already-fused apply are untouched.

**Tech Stack:** Rust (goldenflow-core pyo3-free + native-flow PyO3/Arrow + goldenflow-wasm/wasm-bindgen), Python (polars-optional engine), TypeScript (goldenflow-js core + wasm). Spec: `docs/superpowers/specs/2026-07-13-goldenflow-auto-detect-owned-kernel-design.md`.

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion

---

## Reference: the two Python behaviors this must match byte-for-byte

From `packages/python/goldenflow/goldenflow/engine/profiler_bridge.py` (origin/main):

- `_infer_type(series)`: numeric/bool/date **by Polars dtype** short-circuit; else the five regexes over the first ≤100 non-null, `str.strip()`-ed, non-empty values, thresholds email 0.7 / zip 0.7 / date 0.5 / phone 0.6 / name 0.5, **most-specific-first**, else `"string"`.
- `_infer_type_list(values)`: all-non-null `bool` → `"boolean"`; all-non-null `int|float` (not bool) → `"numeric"`; **no Date-dtype case**; else the SAME regex block over `str(s).strip()`.

The five regexes (copy exactly):
```
_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_PHONE_RE = r"^[\+\(]?[\d][\d\(\)\-\.\s]{6,18}\d$"
_DATE_RE  = r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})$"
_NAME_RE  = r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$"
_ZIP_RE   = r"^\d{5}(-\d{4})?$"
```
`_EMAIL_RE` is byte-identical to what `goldenflow_core::email::email_validate` already hand-rolls — reuse it (Task 1 asserts agreement); the other four are new hand-rolled matchers (NO `regex` crate — cross-runtime parity, per the email.rs/address.rs precedent).

`TypeHint` maps the caller's dtype/value knowledge to the kernel: `Numeric|Boolean|Date` short-circuit; `Utf8` runs the regex block.

---

## File Structure

**Create:**
- `packages/rust/extensions/goldenflow-core/src/profile.rs` — `TypeHint`, `ColumnProfileOut`, `infer_type`, `profile_column` + the 4 new matchers + unit tests.
- `packages/python/goldenflow/tests/parity/profile_corpus.jsonl` — cross-surface oracle corpus (generated).
- `packages/python/goldenflow/scripts/gen_profile_corpus.py` — corpus generator + `--check` drift guard.
- `packages/python/goldenflow/tests/transforms/test_profile_kernels.py` — full-profile pinned-vector + native-lane tests + decision-equivalence.
- `packages/typescript/goldenflow/tests/parity/profile.parity.test.ts` — TS/wasm parity vs the byte-copied corpus.

**Modify:**
- `packages/rust/extensions/goldenflow-core/src/lib.rs` — `pub mod profile;` + version bump.
- `packages/rust/extensions/native-flow/src/profile.rs` (new small module) + `src/column.rs` (`Column.profile()`) + `src/lib.rs` (register 1 fn) + `Cargo.toml`/`pyproject.toml`/`Cargo.lock` version + `python/goldenflow_native/__init__.py` fallback version.
- `packages/rust/extensions/goldenflow-wasm/src/lib.rs` — `infer_type` export + `Cargo.toml`/`Cargo.lock` version.
- `packages/python/goldenflow/goldenflow/engine/profiler_bridge.py` — route Path 1 + Path 2a through native with pure-Python fallback.
- `packages/python/goldenflow/goldenflow/core/_native_loader.py` — add `profile` component (floor `infer_type_list_arrow`).
- `packages/python/goldenflow/goldenflow/__init__.py` — `__version__` bump.
- `packages/python/goldenflow/pyproject.toml` — version + native floor bump.
- `packages/typescript/goldenflow/src/core/engine/profiler-bridge.ts` — `inferType` wasm dispatch + pure-TS fallback; `src/core/wasm/{backend,loader}.ts` — wire the export; `package.json` version.
- `scripts/check_native_symbols.py` config OR `parity/*.yaml` — none needed (literal idiom already covers `*_arrow`); verify.
- Docs: `packages/python/goldenflow/CLAUDE.md`, `CHANGELOG.md`, ADR, `docs/design/2026-07-06-goldenflow-owned-kernel-boundary.md` + `tests/transforms/test_owned_kernel_boundary.py`.

---

## Phase 1 — Core kernel (goldenflow-core::profile)

The parity-critical crux. All work is `cargo test` on a pyo3-free crate (safe on this box).

### Task 1: `infer_type` + matchers with TDD boundary vectors

**Files:**
- Create: `packages/rust/extensions/goldenflow-core/src/profile.rs`
- Modify: `packages/rust/extensions/goldenflow-core/src/lib.rs` (add `pub mod profile;`)

- [ ] **Step 1: Write the failing tests** (these vectors ARE the parity contract — each pins a regex boundary that diverges if hand-rolled wrong)

```rust
// packages/rust/extensions/goldenflow-core/src/profile.rs
#[cfg(test)]
mod tests {
    use super::*;

    fn t(vals: &[&str], hint: TypeHint) -> String {
        let v: Vec<Option<&str>> = vals.iter().map(|s| Some(*s)).collect();
        infer_type(&v, hint)
    }

    #[test] fn hint_short_circuits_skip_regex() {
        assert_eq!(t(&["2020-01-01"], TypeHint::Numeric), "numeric");
        assert_eq!(t(&["x"], TypeHint::Boolean), "boolean");
        assert_eq!(t(&["whatever"], TypeHint::Date), "date");
    }
    #[test] fn empty_and_all_blank_is_string() {
        assert_eq!(infer_type(&[None, None], TypeHint::Utf8), "string");
        assert_eq!(t(&["   ", ""], TypeHint::Utf8), "string"); // stripped-empty skipped
    }
    #[test] fn email_matcher() { // threshold 0.7
        assert_eq!(t(&["a@b.co","x@y.io","p@q.net"], TypeHint::Utf8), "email");
        assert_eq!(t(&["a@b"], TypeHint::Utf8), "string");         // no dot
        assert_eq!(t(&["a b@c.co"], TypeHint::Utf8), "string");    // whitespace
    }
    #[test] fn zip_matcher() { // threshold 0.7, checked BEFORE date/phone
        assert_eq!(t(&["12345","90210-1234"], TypeHint::Utf8), "zip");
        assert_eq!(t(&["1234"], TypeHint::Utf8), "string");        // 4 digits
        assert_eq!(t(&["12345-12"], TypeHint::Utf8), "string");    // bad +4
    }
    #[test] fn date_matcher() { // threshold 0.5
        assert_eq!(t(&["2020-01-02","1999/12/31"], TypeHint::Utf8), "date"); // yyyy-m-d
        assert_eq!(t(&["1/2/99","12-31-2020"], TypeHint::Utf8), "date");     // m/d/yy(yy)
        assert_eq!(t(&["January 2, 2020","Mar 3 1999"], TypeHint::Utf8), "date"); // month name
        assert_eq!(t(&["2020"], TypeHint::Utf8), "string");
    }
    #[test] fn phone_matcher() { // threshold 0.6 ; 8..=20 chars, digit-bordered
        assert_eq!(t(&["(212) 555-1234","+1 415 555 9999"], TypeHint::Utf8), "phone");
        assert_eq!(t(&["12"], TypeHint::Utf8), "string");          // too short
        assert_eq!(t(&["abc-defg"], TypeHint::Utf8), "string");
    }
    #[test] fn name_matcher() { // threshold 0.5 ; Titlecased multi-word
        assert_eq!(t(&["John Smith","Jane Marie Doe"], TypeHint::Utf8), "name");
        assert_eq!(t(&["john smith"], TypeHint::Utf8), "string");  // lowercase
        assert_eq!(t(&["John"], TypeHint::Utf8), "string");        // single word
    }
    #[test] fn most_specific_first_and_threshold() {
        // 1 email of 3 = 0.33 < 0.7 -> not email; falls through to string
        assert_eq!(t(&["a@b.co","foo","bar"], TypeHint::Utf8), "string");
        // zip beats date: "12345" matches ZIP (checked first)
        assert_eq!(t(&["12345","12345","12345"], TypeHint::Utf8), "zip");
    }
    #[test] fn only_first_100_sampled() {
        let mut v: Vec<Option<&str>> = vec![Some("a@b.co"); 100];
        v.extend(vec![Some("not-an-email"); 100]); // ignored (beyond 100)
        assert_eq!(infer_type(&v, TypeHint::Utf8), "email");
    }
    #[test] fn email_matcher_agrees_with_email_validate() {
        for s in ["a@b.co","x@y", "no at", "a@b.c.d"] {
            let via_profile = is_email(s);
            let via_email = crate::email::email_validate(s) == Some(true);
            assert_eq!(via_profile, via_email, "mismatch on {s:?}");
        }
    }
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd packages/rust/extensions/goldenflow-core && cargo test --lib profile`
Expected: FAIL (module/functions not defined).

- [ ] **Step 3: Implement `profile.rs`** (matchers hand-rolled to the exact regex semantics; `is_email` reuses `email_validate`). The threshold loop:

```rust
//! Owned auto-detect profiling kernel — the type-inference DECISION behind
//! GoldenFlow's zero-config `transform_df(config=None)`. Byte-parity reference
//! for `_infer_type` / `_infer_type_list`. The column-NAME override
//! (`_override_type_by_column_name`) stays in the Python/TS caller — this kernel
//! is a pure function of column VALUES.

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum TypeHint { Utf8, Numeric, Boolean, Date }

/// Map a caller-supplied hint string to `TypeHint` (shared by native-flow +
/// goldenflow-wasm so all surfaces agree). Unknown → `Utf8` (run the regexes).
pub fn hint_from_str(h: &str) -> TypeHint {
    match h {
        "numeric" => TypeHint::Numeric,
        "boolean" => TypeHint::Boolean,
        "date" => TypeHint::Date,
        _ => TypeHint::Utf8,
    }
}

pub struct ColumnProfileOut {
    pub null_count: u64,
    pub unique_count: u64,
    pub samples: Vec<String>,
    pub inferred_type: String,
}

// (email 0.7, zip 0.7, date 0.5, phone 0.6, name 0.5) — order = most-specific first.
const CHECKS: &[(&str, fn(&str) -> bool, f64)] = &[
    ("email", is_email, 0.7),
    ("zip",   is_zip,   0.7),
    ("date",  is_date,  0.5),
    ("phone", is_phone, 0.6),
    ("name",  is_name,  0.5),
];

pub fn infer_type(values: &[Option<&str>], hint: TypeHint) -> String {
    match hint {
        TypeHint::Numeric => return "numeric".into(),
        TypeHint::Boolean => return "boolean".into(),
        TypeHint::Date => return "date".into(),
        TypeHint::Utf8 => {}
    }
    // sample = first 100 non-null; then strip + drop empties (mirror the Python order)
    let sample: Vec<&str> = values.iter().flatten().copied().take(100).collect();
    let stripped: Vec<&str> = sample.iter().map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
    if stripped.is_empty() { return "string".into(); }
    let n = stripped.len() as f64;
    for (name, matcher, threshold) in CHECKS {
        let hits = stripped.iter().filter(|v| matcher(v)).count() as f64;
        if hits / n >= *threshold { return (*name).into(); }
    }
    "string".into()
}

pub fn profile_column(values: &[Option<&str>], hint: TypeHint) -> ColumnProfileOut {
    use std::collections::HashSet;
    let mut null_count = 0u64;
    let mut seen: HashSet<&str> = HashSet::new();
    let mut samples: Vec<String> = Vec::with_capacity(5);
    for v in values {
        match v {
            None => null_count += 1,
            Some(s) => {
                seen.insert(s);
                if samples.len() < 5 { samples.push((*s).to_string()); }
            }
        }
    }
    ColumnProfileOut {
        null_count,
        unique_count: seen.len() as u64,
        samples,
        inferred_type: infer_type(values, hint),
    }
}

fn is_email(s: &str) -> bool { crate::email::email_validate(s) == Some(true) }
// is_zip / is_date / is_phone / is_name: hand-roll to the exact regex — make the
// Step-1 vectors pass. Byte-scan, ASCII semantics (\d = [0-9], \s = char::is_whitespace
// as proven in text_golden.rs). NO regex crate.
```
(Implement `is_zip`/`is_date`/`is_phone`/`is_name` to pass the vectors. Note `_PHONE_RE` total length is 8–20 chars: leading optional `+`/`(`, a digit, 6–18 middle chars from `[\d()\-.\s]`, a trailing digit.)

- [ ] **Step 4: Run tests to verify pass**

Run: `cargo test --lib profile`
Expected: PASS (all vectors green).

- [ ] **Step 5: Clippy (matches CI `rust` lane) + commit**

Run: `cargo-clippy clippy --manifest-path packages/rust/extensions/goldenflow-core/Cargo.toml --all-targets -- -D warnings`
Expected: clean (watch `unreachable_patterns` on any range `matches!`).

```bash
git add packages/rust/extensions/goldenflow-core/src/profile.rs packages/rust/extensions/goldenflow-core/src/lib.rs
git commit -m "feat(goldenflow-core): owned auto-detect profile kernel (infer_type + profile_column)"
```

### Task 2: bump goldenflow-core version (cache-bust for the maturin lane)

New module + `lib.rs` edit forces a rebuild, but bump anyway for wheel/lock hygiene (the gotcha-5 lesson: existing-module edits need it; a new module is safe, but we also touch dependents).

- [ ] **Step 1:** `packages/rust/extensions/goldenflow-core/Cargo.toml` `version = "0.13.0"` → `"0.14.0"`.
- [ ] **Step 2:** `cargo update -p goldenflow-core --manifest-path packages/rust/extensions/native-flow/Cargo.toml` AND `... --manifest-path packages/rust/extensions/goldenflow-wasm/Cargo.toml` (both lockfiles pin it; `--locked` CI fails otherwise).
- [ ] **Step 3: Commit** `git commit -am "chore(goldenflow-core): 0.13.0 -> 0.14.0"`

---

## Phase 2 — native-flow shim (Path 1 columnar + Path 2a list)

### Task 3: `infer_type_list_arrow` + `Column.profile()`

**Files:**
- Create: `packages/rust/extensions/native-flow/src/profile.rs`
- Modify: `packages/rust/extensions/native-flow/src/column.rs`, `src/lib.rs`

- [ ] **Step 1: Write the failing Rust test** (native crate builds a `.pyd`; keep the unit test at the marshaling seam — a pure `infer_type` re-export delegation test — and defer real Arrow round-trip to the Python parity lane per the box rules).

```rust
// native-flow/src/profile.rs — delegation is trivial; the real assertion is the
// Python parity test. A smoke test that infer_type_list_arrow round-trips a hint:
#[cfg(test)]
mod tests {
    use super::infer_type_list_arrow;
    #[test] fn hint_and_infer() {
        // Numeric hint short-circuits regardless of values
        assert_eq!(infer_type_list_arrow(vec![Some("x".into())], "numeric"), "numeric");
        // Utf8 hint runs the matchers
        let emails = vec![Some("a@b.co".into()), Some("x@y.io".into()), Some("p@q.net".into())];
        assert_eq!(infer_type_list_arrow(emails, "string"), "email");
    }
}
```

- [ ] **Step 2: Run** `cd packages/rust/extensions/native-flow && cargo test --lib profile` → FAIL.

- [ ] **Step 3: Implement.** In `src/profile.rs` (reuse the core `hint_from_str` — do NOT redefine it):

```rust
use goldenflow_core::profile::{hint_from_str, infer_type};
use pyo3::prelude::*;

/// Path 2a: infer the type of a plain Python list of Option<str> (already
/// stringified by the caller via str(v)). Returns just the type string — the
/// list path computes null/unique/samples in Python over RAW values.
#[pyfunction]
pub fn infer_type_list_arrow(values: Vec<Option<String>>, hint: &str) -> String {
    let view: Vec<Option<&str>> = values.iter().map(|o| o.as_deref()).collect();
    infer_type(&view, hint_from_str(hint))
}
```
In `src/column.rs` add a `#[pymethods] impl Column` method `profile(&self) -> PyResult<Py<PyDict>>` that downcasts `self.array`:
- `Utf8`/`LargeUtf8`/`Utf8View` → cast to `LargeStringArray` (as the chain path does), build `Vec<Option<&str>>` borrowed view, call `goldenflow_core::profile::profile_column(view, TypeHint::Utf8)`.
- `Int64`/`Float64` → `TypeHint::Numeric`; `Boolean` → `TypeHint::Boolean`; Date/Timestamp → `TypeHint::Date`. For typed arrays compute `null_count` (Array::null_count) + `unique_count` off the typed buffer; `samples` = first 5 non-null formatted to match Polars `cast(Utf8)` (`float_fmt::float_to_polars_string` for f64, decimal for i64, `"true"`/`"false"` for bool); `inferred_type` from the hint.
Return a dict `{null_count, unique_count, samples, inferred_type}`.
Register in `src/lib.rs`: `m.add_function(wrap_pyfunction!(profile::infer_type_list_arrow, m)?)?;` and `mod profile;`.

- [ ] **Step 4: Run** `cargo test --lib profile` → PASS.

- [ ] **Step 5: fmt (CI fmt-checks native-flow) + clippy + commit**

Run: `cargo fmt --manifest-path packages/rust/extensions/native-flow/Cargo.toml -- --check` then `cargo-clippy clippy --manifest-path packages/rust/extensions/native-flow/Cargo.toml --all-targets -- -D warnings`
```bash
git add packages/rust/extensions/native-flow/src/profile.rs packages/rust/extensions/native-flow/src/column.rs packages/rust/extensions/native-flow/src/lib.rs
git commit -m "feat(native-flow): infer_type_list_arrow + Column.profile()"
```

### Task 4: bump native-flow version (3 spots)

- [ ] **Step 1:** `native-flow/Cargo.toml` `0.26.0` → `0.27.0`; `native-flow/pyproject.toml` `0.26.0` → `0.27.0`; `native-flow/python/goldenflow_native/__init__.py` fallback `"0.26.0"` → `"0.27.0"`.
- [ ] **Step 2:** `cargo update -p goldenflow-native --manifest-path packages/rust/extensions/native-flow/Cargo.toml` (self-lock).
- [ ] **Step 3: Commit** `git commit -am "chore(native-flow): 0.26.0 -> 0.27.0"`

---

## Phase 3 — Python wiring + loader + fallback

### Task 5: `profile` loader component

**Files:** Modify `packages/python/goldenflow/goldenflow/core/_native_loader.py`

- [ ] **Step 1: Write the failing test** in `tests/test_native_loader.py` (add):

```python
def test_profile_component_symbol():
    from goldenflow.core._native_loader import _COMPONENT_SYMBOLS
    assert _COMPONENT_SYMBOLS["profile"] == ("infer_type_list_arrow",)
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest packages/python/goldenflow/tests/test_native_loader.py::test_profile_component_symbol -v` → FAIL.
- [ ] **Step 3: Implement** — add to `_COMPONENT_SYMBOLS`:
```python
    # profile: the zero-config auto-detect type-inference decision
    # (infer_type). Floor symbol infer_type_list_arrow; Column.profile() is the
    # columnar path. Locale-free, region-free.
    "profile": ("infer_type_list_arrow",),
```
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(goldenflow): profile native loader component"`

### Task 6: Route `profile_columns` (Path 2a) through native with fallback

**Files:** Modify `packages/python/goldenflow/goldenflow/engine/profiler_bridge.py`

- [ ] **Step 1: Write the failing test** `tests/transforms/test_profile_kernels.py`:

```python
import os
from goldenflow.engine.profiler_bridge import profile_columns, _infer_type_list

def test_profile_columns_inferred_type_matches_pure():
    cols = {
        "email": ["a@b.co", "x@y.io", "p@q.net"],
        "zip": ["12345", "90210", "10001-1234"],
        "nums": [1, 2, 3],
        "mixed": [1, "1"],            # -> string; unique_count must be 2 (raw)
        "name": ["John Smith", "Jane Doe", "Bob Roe"],
    }
    prof = profile_columns(cols)
    got = {c.name: c.inferred_type for c in prof.columns}
    assert got == {"email":"email","zip":"zip","nums":"numeric","mixed":"string","name":"name"}
    mixed = next(c for c in prof.columns if c.name == "mixed")
    assert mixed.unique_count == 2  # raw-value set, NOT stringified
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest packages/python/goldenflow/tests/transforms/test_profile_kernels.py::test_profile_columns_inferred_type_matches_pure -v` (set `POLARS_SKIP_CPU_CHECK=1`). This is a **green-from-the-start contract guard**: it asserts the invariant (correct `inferred_type` + raw `unique_count`) holds on whichever path runs. It passes on the pure path today — that is intentional and correct; do NOT stub a fake failure. The genuine RED-first native assertion is `test_native_infer_type_list_equals_pure_native` in Step 4 (fails/ skips only in the native lane).

- [ ] **Step 3: Implement** in `profile_columns`: for each column keep `null_count`/`unique_count`/`samples`/`unique_pct` computed in Python exactly as now; replace the `inferred_type` derivation with:
```python
from goldenflow.core._native_loader import native_enabled, native_module
def _infer_type_list_native_or_pure(values: list) -> str:
    if native_enabled("profile"):
        nm = native_module()
        if nm is not None and hasattr(nm, "infer_type_list_arrow"):
            # hint derived EXACTLY as _infer_type_list decides numeric/bool
            non_null = [v for v in values if v is not None]
            if non_null and all(isinstance(v, bool) for v in non_null):
                hint = "boolean"
            elif non_null and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
                hint = "numeric"
            else:
                hint = "string"  # Utf8
            strs = [None if v is None else str(v) for v in values]
            return nm.infer_type_list_arrow(strs, hint)
    return _infer_type_list(values)
```
and call `_override_type_by_column_name(name, _infer_type_list_native_or_pure(values))`.

- [ ] **Step 4: Add a native==pure equivalence test** (native-lane only, name contains `native`):
```python
def test_native_infer_type_list_equals_pure_native():
    nm = pytest.importorskip_native()  # skip if native absent; see conftest helper
    ...  # for a battery of columns assert infer_type_list_arrow(...)==_infer_type_list(...)
```
(Use a module-level `native_available()` skip + `native` in the test name so the fallback lane `-k "not native"` deselects it — mirror `test_native_parity.py`.)

- [ ] **Step 5: Run + Commit**

Run: whole-package `ruff check packages/python/goldenflow` (matches CI isort) then the two tests.
```bash
git add packages/python/goldenflow/goldenflow/engine/profiler_bridge.py packages/python/goldenflow/tests/transforms/test_profile_kernels.py
git commit -m "feat(goldenflow): profile_columns inferred_type via owned kernel (Path 2a)"
```

### Task 7: Route `profile_dataframe` built-in fallback (Path 1) through `Column.profile()`

**Files:** Modify `profiler_bridge.py` (`_profile_column` / the built-in fallback loop only — NOT the GoldenCheck branch).

- [ ] **Step 1: Write the failing test:**
```python
def test_profile_dataframe_builtin_matches_native_column(monkeypatch):
    pl = pytest.importorskip("polars")
    df = pl.DataFrame({"email":["a@b.co","x@y.io"], "n":[1,2], "s":["foo","bar"]})
    # force built-in path (no goldencheck): monkeypatch scan_file import to raise
    ...
    prof = profile_dataframe(df)  # built-in fallback
    got = {c.name: c.inferred_type for c in prof.columns}
    assert got == {"email":"email","n":"numeric","s":"string"}
```
- [ ] **Step 2: Run** → PASS on pure (contract guard) / exercises native in CI native lane.
- [ ] **Step 3: Implement** a `_profile_column_native_or_pure(series)` that, when `native_enabled("profile")` and a `Column` class is importable, does `Column.from_arrow(series.to_frame())` → `.profile()` (hint from `series.dtype`: numeric/bool/temporal/else-Utf8), maps the returned dict into `ColumnProfile`, then applies `_override_type_by_column_name`; else the existing `_profile_column`. **Gotcha (P1b): pass a 1-col DataFrame (`series.to_frame()`), never a bare Series, to `Column.from_arrow`.**
- [ ] **Step 4: Run** the built-in + a native-lane equivalence test → PASS.
- [ ] **Step 4b: Pin typed-column stats format (native lane).** Add a native-named test asserting `Column.profile()` on `Int64`/`Float64`/`Boolean` columns returns `null_count`/`unique_count` equal to Polars and `samples` byte-equal to `series.head(5).cast(pl.Utf8).to_list()` — including a `Float64` with `1.0`/`-0.0` (via `float_to_polars_string`) and a `Boolean` (`"true"`/`"false"`). This pins Contract 3's typed-sample formatting (display-only, but cheap to lock). Skips when native absent.
- [ ] **Step 5: Commit** `git commit -am "feat(goldenflow): profile_dataframe built-in via Column.profile() (Path 1)"`

---

## Phase 4 — WASM + TypeScript

(CI-only surface — box OOMs vitest. Rigorous static review + the `wasm_flow` lane are the gate. Before pushing any TS: `grep -nE '[A-Za-z0-9]\*/' <touched>.ts` for the JSDoc `*/` bug.)

### Task 8: `goldenflow-wasm` `infer_type` export

**Files:** Modify `packages/rust/extensions/goldenflow-wasm/src/lib.rs`, `Cargo.toml` (`0.3.0` → `0.4.0`) + `Cargo.lock`.

- [ ] **Step 1:** Add inside the `cfg(target_arch="wasm32")` module (mirror the identifier exports), `use goldenflow_core::profile;`:
```rust
    #[wasm_bindgen]
    pub fn infer_type(values: Vec<JsValue>, hint: String) -> String {
        // values: (string | null)[] from JS; map to Option<String> then &str
        let owned: Vec<Option<String>> = values.into_iter()
            .map(|v| if v.is_null() || v.is_undefined() { None } else { v.as_string() })
            .collect();
        let view: Vec<Option<&str>> = owned.iter().map(|o| o.as_deref()).collect();
        profile::infer_type(&view, profile::hint_from_str(&hint))
    }
```
(If `Vec<JsValue>` marshaling differs from the existing exports, follow whatever the identifier exports use for `(string|null)[]`. `profile::hint_from_str` is already `pub` in core from Task 1 — call it directly.)
- [ ] **Step 2:** wasm-pack build locally if available (else CI): the `wasm_flow` lane builds into `src/core/wasm/artifacts/`.
- [ ] **Step 3: Commit** `git commit -am "feat(goldenflow-wasm): infer_type export"`

### Task 9: TS `inferType` wasm-dispatch + pure fallback + parity test

**Files:** Modify `src/core/engine/profiler-bridge.ts` (`inferType`), `src/core/wasm/{backend,loader}.ts`; Create `tests/parity/profile.parity.test.ts`; byte-copy the corpus.

- [ ] **Step 1:** In `profiler-bridge.ts::inferType`, when `enableWasm()` backend present, dispatch the type-inference to `getFlowWasmBackend().infer_type(sample, hint)`; else keep the existing pure-TS regex (the byte-matched fallback). Compute `hint` from JS values exactly as `_infer_type_list` does (all-boolean → `boolean`; all-number → `numeric`; else string). Wire `infer_type` through `wasm/backend.ts` + `wasm/loader.ts` (mirror an existing identifier binding — snake_case export name).
- [ ] **Step 2:** Create `tests/parity/profile.parity.test.ts`: byte-copy `packages/python/goldenflow/tests/parity/profile_corpus.jsonl` → `packages/typescript/goldenflow/tests/parity/profile_corpus.jsonl` (cmp-enforced in CI sync-check), assert pure-TS `inferType` == `expected_type` for every row; wasm leg `skipIf` no artifact.
- [ ] **Step 3:** Static cross-check (box can't run vitest): corpus keys == pure-TS-fn coverage == wasm-map coverage. Grep `*/` bug.
- [ ] **Step 4:** `package.json` `0.15.0` → `0.16.0`.
- [ ] **Step 5: Commit** `git commit -m "feat(goldenflow-js): inferType owned-kernel dispatch + profile parity"`

---

## Phase 5 — Cross-surface parity corpus + decision-equivalence

### Task 10: `profile_corpus.jsonl` + generator + drift guard

**Files:** Create `scripts/gen_profile_corpus.py`, `tests/parity/profile_corpus.jsonl`.

- [ ] **Step 1:** Write `gen_profile_corpus.py` (mirror `gen_identifiers_corpus.py`): rows `{"values":[...], "hint":"...", "expected_type":"..."}` where `expected_type` = `_infer_type_list(values)` with the matching hint (oracle = the pure-Python reference, which Rust unit tests already pin to the kernel). Include: each type at just-above / just-below threshold, empty/all-null, stripped-empties, most-specific-first collisions, and the **mixed `[1,"1"]`** row. `--check` regenerates to a temp and diffs (drift guard), exits non-zero on drift.
- [ ] **Step 2:** Generate the corpus; commit both. Run `python scripts/gen_profile_corpus.py --check` → clean.
- [ ] **Step 3:** In `test_profile_kernels.py`, add a corpus-driven test: for every row assert `_infer_type_list(values) == expected_type` (pure, always) AND (native lane) `infer_type_list_arrow(strs, hint) == expected_type`.
- [ ] **Step 4: Commit** `git commit -m "test(goldenflow): profile parity corpus + drift guard"`

### Task 11: decision-equivalence on the unique_pct gate

**Files:** `test_profile_kernels.py` (add).

- [ ] **Step 1:** Build a float column corpus straddling `unique_pct = 0.1` including `NaN`/`-0.0`; assert `select_transforms` output (specifically `category_auto_correct` presence) is identical native-vs-Polars even where the raw `unique_count` differs by the NaN/-0.0 edge. Assert numeric columns never contain `category_auto_correct` (so the edge can't change selection).
- [ ] **Step 2: Run + Commit** `git commit -m "test(goldenflow): unique_pct gate decision-equivalence (float NaN/-0.0 edge)"`

### Task 12: engine smoke — native on/off byte-identity

- [ ] **Step 1:** `test_profile_kernels.py`: a mixed-type fixture (email/zip/date/phone/name/numeric/bool/null-heavy); assert `transform_df(df, config=None)` (Polars) and `transform_columns_public(dict, None)` (Polars-free) produce identical `Manifest` records + selected transforms with `GOLDENFLOW_NATIVE=0` vs `=1`.
- [ ] **Step 2: Run + Commit** `git commit -m "test(goldenflow): auto-detect native on/off byte-identity smoke"`

---

## Phase 6 — versions, boundary doc, docs sweep, release

### Task 13: goldenflow package version + native floor

- [ ] **Step 1:** `packages/python/goldenflow/goldenflow/__init__.py` `__version__ "2.0.0"` → `"2.1.0"`; `pyproject.toml` `[project] version` → `2.1.0` and bump the `goldenflow-native` floor in the `[native]` extra to `>=0.27.0`. (Keep `__init__` and pyproject in lockstep — the drift lesson.)
- [ ] **Step 2:** Update `docs/design/2026-07-06-goldenflow-owned-kernel-boundary.md` + `tests/transforms/test_owned_kernel_boundary.py` — classify `infer_type`/profiling as owned (the boundary test fails on unclassified surfaces). Run that test.
- [ ] **Step 3: Commit** `git commit -am "chore(goldenflow): 2.0.0 -> 2.1.0 + native floor 0.27.0 + boundary doc"`

### Task 14: docs sweep (invoke @rollout-docs-sweep at the end)

- [ ] CLAUDE.md profiler section (auto-detect now owned + cross-surface), CHANGELOG `Unreleased`, ADR (new entry for the auto-detect kernel), performance.mdx if it lists owned surfaces. Commit.

### Task 15: pre-push full routine + PR

- [ ] **Step 1: Run the whole pre-push routine** (pre-empts the known CI reds):
  - `cargo-clippy clippy --manifest-path packages/rust/extensions/goldenflow-core/Cargo.toml --all-targets -- -D warnings`
  - `cargo fmt --manifest-path packages/rust/extensions/native-flow/Cargo.toml -- --check`
  - `ruff check packages/python/goldenflow` (whole package)
  - `grep -rnE '[A-Za-z0-9]\*/' packages/typescript/goldenflow/src` (JSDoc `*/`)
  - `python scripts/gen_profile_corpus.py --check`
  - Static: corpus keys == PURE_TS_FN keys == wasm-map keys.
- [ ] **Step 2:** Push the branch (GitHub auth dance: `gh auth switch --user benzsevern`, unset `GH_TOKEN`), open the PR against `main`, arm `gh pr merge --auto --squash`, STOP (merge-on-green standing authorization). Confirm the `native_flow` / `wasm_flow` / `python (goldenflow)` lanes are green (CI is the only gate for the maturin wheel + TS).

### Task 16 (post-merge): republish native wheel + lockstep releases

- [ ] Republish `goldenflow-native` (tag `goldenflow-native-v0.27.0`, or `gh workflow run publish-goldenflow-native.yml --ref main`); **verify `infer_type_list_arrow` in the built wheel via `grep -a` before claiming the Polars-free path reaches users** (wheel-skew lesson).
- [ ] Once native 0.27.0 is on PyPI: cut `goldenflow-v2.1.0`; then bump `golden-suite` floor + cut `golden-suite-v*` (lockstep rule). npm: publish goldenflow 0.16.0, verify `.wasm` in the tarball.

---

## Landmine checklist (from prior waves — verify each before push)

- [ ] `goldenflow-core` version bumped + `cargo update -p goldenflow-core` on BOTH dependent lockfiles (else `--locked` CI fails / stale-core links old kernel).
- [ ] `cargo fmt --check` on native-flow (CI fmt-checks it; fmt ≠ clippy).
- [ ] Whole-package `ruff check` (isort I001 on new first-party imports).
- [ ] No `*/` inside TS block comments.
- [ ] TS corpus is a byte-copy (cmp-enforced) of the Python oracle.
- [ ] `Column.from_arrow` gets a 1-col DataFrame, never a bare Series (Utf8View cast on ingest).
- [ ] GoldenCheck profile branch untouched; `select_transforms` untouched.
- [ ] `native_symbols` gate: goldenflow uses the `literal` idiom (`*_arrow` string literals) — `infer_type_list_arrow` referenced as a literal in `profiler_bridge.py` is covered; verify `scripts/check_native_symbols.py` passes.
