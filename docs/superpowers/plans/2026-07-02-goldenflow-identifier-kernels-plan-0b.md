# GoldenFlow Identifier Kernels (Wave 0b) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add owned, Rust-first checksummed-identifier transforms (payment card, IBAN, ISBN, EAN/UPC, EU VAT) to GoldenFlow — kernels in `goldenflow-core`, native-first dispatch, pure-Python fallback proven byte-identical to the Rust oracle.

**Architecture:** Each identifier family is a set of pure functions in `goldenflow-core/src/identifiers/<family>.rs` (validate → `bool`, canonicalize → `Option<String>`). `native-flow` exposes them as Arrow shims; a new pure-Python fallback in `goldenflow/transforms/identifiers.py` reproduces the exact bytes. Dispatch is native-over-Arrow when the wheel is present, pure-Python otherwise — **no Polars authority tier** (identifiers are cheap and deterministic). A checked-in corpus with `goldenflow-core` as the oracle gates byte parity.

**Tech Stack:** Rust (pyo3-free core + PyO3/arrow shim), Python 3.11+, Polars, pytest.

**Depends on:** Wave 0a (PR #1405) — `goldenflow-core` crate, `native-flow` shim, and the reference-mode loader must be merged first. Branch this off updated `main`.

**Spec:** `docs/superpowers/specs/2026-07-02-goldenflow-core-cross-surface-wave0-design.md`

**Environment (Windows dev, memory-constrained):** Rust `cargo test` on the pyo3-free core is safe and is the real TDD loop. For Python use `ruff` + `py_compile` + at most ONE targeted single-file `pytest` with `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8` via `.venv/Scripts/python.exe`; never the full suite, never `uv sync`/`maturin`/`pip` loops; kill lingering python after. The native-wheel build + full parity is the CI `native_flow` lane's job. `cargo clippy` may need `cargo-clippy clippy -- -D warnings` in a worktree.

---

## File Structure

- **Create** `goldenflow-core/src/identifiers/mod.rs` — `pub mod luhn; pub mod iban; pub mod isbn; pub mod ean; pub mod vat;` + a shared `fn strip_sep(s: &str) -> String` (remove spaces/dashes/dots).
- **Create** `goldenflow-core/src/identifiers/{luhn,iban,isbn,ean,vat}.rs` — kernels + Rust unit tests with canonical vectors.
- **Modify** `goldenflow-core/src/lib.rs` — add `pub mod identifiers;`.
- **Create** `native-flow/src/identifiers.rs` — Arrow shims (`*_validate_arrow` via `map_str_to_bool`, `*_format`/`*_normalize`/`*_mask` via `map_str_to_str`) delegating to `goldenflow_core::identifiers`.
- **Modify** `native-flow/src/lib.rs` — `mod identifiers;` + register the new pyfunctions.
- **Modify** `goldenflow/goldenflow/transforms/_native.py` — add per-transform native helper wrappers (mirror the phone helpers).
- **Modify** `goldenflow/goldenflow/transforms/identifiers.py` — add the 10 new transforms (pure-Python fallback + native dispatch), each `@register_transform(..., auto_apply=False)`.
- **Modify** `goldenflow/goldenflow/core/_native_loader.py` — add identifier components to `_COMPONENT_SYMBOLS`.
- **Create** `goldenflow/tests/parity/identifiers_corpus.jsonl` — the oracle corpus.
- **Modify** `goldenflow/tests/transforms/test_identifiers.py` — this file ALREADY EXISTS (ssn/ein unit tests); APPEND the new card/IBAN/ISBN/EAN/VAT unit cases, do not overwrite it.
- **Create** `goldenflow/tests/transforms/test_identifiers_parity.py` — byte-parity (native + fallback vs corpus).
- **Create** `goldenflow/scripts/gen_identifiers_corpus.py` — regenerates the corpus from the native kernels (oracle); a CI check fails on drift.

**Component/transform naming** (loader `_COMPONENT_SYMBOLS` keys → transforms):
- `cc` → `cc_validate`, `cc_format`, `cc_mask`
- `iban` → `iban_validate`, `iban_format`
- `isbn` → `isbn_validate`, `isbn_normalize`
- `ean` → `ean_validate`
- `vat` → `vat_validate`, `vat_format`

---

## Task 1: Card (Luhn) kernel — establishes the full pattern

This task sets the pattern (kernel → shim → Python fallback → loader → corpus row → tests) that Tasks 2-5 repeat. Do it thoroughly.

**Files:** `goldenflow-core/src/identifiers/{mod.rs,luhn.rs}`, `goldenflow-core/src/lib.rs`, `native-flow/src/identifiers.rs`, `native-flow/src/lib.rs`, `goldenflow/goldenflow/transforms/{_native.py,identifiers.py}`, `goldenflow/goldenflow/core/_native_loader.py`, tests.

- [ ] **Step 1: Kernel TDD (Rust, red).** Create `goldenflow-core/src/identifiers/mod.rs`:
```rust
//! Owned checksummed-identifier kernels (pyo3-free). validate -> bool,
//! canonicalize -> Option<String>. These are the reference implementations;
//! the Python/TS fallbacks must reproduce their bytes exactly (byte-parity harness).
pub mod luhn;
// (iban, isbn, ean, vat added in later tasks)

/// Remove ASCII spaces, '-' and '.' — the separators identifiers tolerate.
pub(crate) fn strip_sep(s: &str) -> String {
    s.chars().filter(|c| !matches!(c, ' ' | '-' | '.')).collect()
}
```
Create `goldenflow-core/src/identifiers/luhn.rs` with ONLY a failing test module first:
```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn valid_cards() {
        assert!(cc_validate("4242 4242 4242 4242")); // Visa test
        assert!(cc_validate("5555555555554444"));    // Mastercard
        assert!(cc_validate("378282246310005"));     // Amex (15)
    }
    #[test]
    fn invalid_cards() {
        assert!(!cc_validate("4242424242424241")); // bad checksum
        assert!(!cc_validate("1234"));             // too short
        assert!(!cc_validate("4242abcd42424242")); // non-digit
    }
    #[test]
    fn format_and_mask() {
        assert_eq!(cc_format("4242424242424242").as_deref(), Some("4242 4242 4242 4242"));
        assert_eq!(cc_format("378282246310005").as_deref(), Some("3782 822463 10005")); // Amex 4-6-5
        assert_eq!(cc_format("4242424242424241"), None); // invalid -> None
        assert_eq!(cc_mask("4242424242424242").as_deref(), Some("************4242"));
        assert_eq!(cc_mask("bogus"), None);
    }
}
```
Run `cd goldenflow-core && cargo test identifiers::luhn` → RED (functions undefined).

- [ ] **Step 2: Implement `luhn.rs` (green).** Above the tests:
```rust
use super::strip_sep;

/// Luhn checksum over an all-ASCII-digit string. Caller guarantees digits.
fn luhn_ok(digits: &str) -> bool {
    let mut sum = 0u32;
    let mut dbl = false;
    for c in digits.bytes().rev() {
        let mut d = (c - b'0') as u32;
        if dbl {
            d *= 2;
            if d > 9 { d -= 9; }
        }
        sum += d;
        dbl = !dbl;
    }
    sum % 10 == 0
}

fn normalized_digits(s: &str) -> Option<String> {
    let t = strip_sep(s);
    if t.is_empty() || !t.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some(t)
}

pub fn cc_validate(s: &str) -> bool {
    match normalized_digits(s) {
        Some(d) => (13..=19).contains(&d.len()) && luhn_ok(&d),
        None => false,
    }
}

/// Group digits by brand: Amex (starts 34/37, len 15) -> 4-6-5; else 4-4-4-4...
pub fn cc_format(s: &str) -> Option<String> {
    let d = normalized_digits(s)?;
    if !((13..=19).contains(&d.len()) && luhn_ok(&d)) {
        return None;
    }
    let groups: &[usize] = if d.len() == 15 && (d.starts_with("34") || d.starts_with("37")) {
        &[4, 6, 5]
    } else {
        &[4, 4, 4, 4, 4] // 4-digit groups, remainder trails
    };
    Some(group(&d, groups))
}

pub fn cc_mask(s: &str) -> Option<String> {
    let d = normalized_digits(s)?;
    if !(13..=19).contains(&d.len()) {
        return None;
    }
    let last4 = &d[d.len() - 4..];
    Some(format!("{}{}", "*".repeat(d.len() - 4), last4))
}

/// Split `d` into the given group sizes joined by spaces; any leftover after the
/// listed groups is split into further 4s (keeps 16/19-digit cards grouped).
fn group(d: &str, sizes: &[usize]) -> String {
    let mut out = Vec::new();
    let mut i = 0;
    for &n in sizes {
        if i >= d.len() { break; }
        let end = (i + n).min(d.len());
        out.push(&d[i..end]);
        i = end;
    }
    while i < d.len() {
        let end = (i + 4).min(d.len());
        out.push(&d[i..end]);
        i = end;
    }
    out.join(" ")
}
```
Add `pub mod identifiers;` to `goldenflow-core/src/lib.rs`. Run `cargo test`, `cargo-clippy clippy -- -D warnings`, `cargo fmt --check` → all green. (If the Amex 4-6-5 or the trailing-4s expectation surprises you, adjust the TEST to match a deliberate, documented grouping — the kernel is the oracle, so pick a rule and lock it.)

- [ ] **Step 3: Arrow shim.** Create `native-flow/src/identifiers.rs`:
```rust
//! Arrow shims over goldenflow_core::identifiers. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_bool, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::identifiers::luhn;
use pyo3::prelude::*;

#[pyfunction]
pub fn cc_validate_arrow(py: Python, array: PyArrowType<ArrayData>) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| Some(luhn::cc_validate(s)))?))
}
#[pyfunction]
pub fn cc_format_arrow(py: Python, array: PyArrowType<ArrayData>) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, luhn::cc_format)?))
}
#[pyfunction]
pub fn cc_mask_arrow(py: Python, array: PyArrowType<ArrayData>) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, luhn::cc_mask)?))
}
```
Note: `map_str_to_bool` expects `Fn(&str)->Option<bool>`; wrap validate as `|s| Some(luhn::cc_validate(s))` (validate is total, never null). In `native-flow/src/lib.rs` add `mod identifiers;` and register the three pyfunctions in the `_native` module.
Run `cd native-flow && cargo build --release` (+ clippy/fmt). No Python here.

- [ ] **Step 4: Python fallback + native dispatch.** In `goldenflow/goldenflow/transforms/_native.py`, add helper wrappers mirroring the phone helpers (call `native_module().cc_validate_arrow(series.to_arrow())` → `pl.from_arrow`, guarded by `native_enabled("cc")`). In `transforms/identifiers.py`, add `cc_validate`, `cc_format`, `cc_mask` as `@register_transform(name=..., input_types=["identifier","string"], auto_apply=False, priority=..., mode="series")`. Each: if `native_enabled("cc")` and the native helper is present, dispatch native; else run a pure-Python fallback that reproduces the kernel byte-for-byte (same strip, same Luhn, same grouping/mask rules). Keep the pure-Python Luhn/format/mask in this file.

- [ ] **Step 5: Loader component.** In `_native_loader.py`, add to `_COMPONENT_SYMBOLS`: `"cc": ("cc_validate_arrow",)` (floor symbol). Now `native_enabled("cc")` is True whenever the wheel exports it.

- [ ] **Step 6: Corpus + parity test.** Create `goldenflow/tests/parity/identifiers_corpus.jsonl` with `cc` rows: `{"transform":"cc_validate","input":"4242 4242 4242 4242","expected":"true"}`, the invalids, `cc_format`/`cc_mask` rows (expected strings), and null/empty rows. Create `goldenflow/scripts/gen_identifiers_corpus.py` that computes `expected` by calling the NATIVE kernels (the oracle) so the corpus can be regenerated; commit the generated file. Create `goldenflow/tests/transforms/test_identifiers_parity.py`: parametrize over the corpus. A `test_identifiers_fallback_parity` asserts the pure-Python fallback path (GOLDENFLOW_NATIVE=0) equals `expected`; a separate `test_identifiers_native_parity` (note "native" in the name so the fallback lane's `-k "not native and not Native"` deselects it) asserts the native path equals `expected` too, guarded at module or test level by the existing idiom `if not native_available(): pytest.skip(..., allow_module_level=True)` (there is NO `@native_only` marker in this repo — mirror `tests/transforms/test_native_parity.py`). APPEND the direct unit cases to the EXISTING `tests/transforms/test_identifiers.py` (do not recreate it — it holds the ssn/ein tests).
Run the single parity file once with `POLARS_SKIP_CPU_CHECK=1` (fallback path). Native path is validated in CI.

- [ ] **Step 7: Commit.**
```bash
git add packages/rust/extensions/goldenflow-core packages/rust/extensions/native-flow \
        packages/python/goldenflow/goldenflow/transforms/identifiers.py \
        packages/python/goldenflow/goldenflow/transforms/_native.py \
        packages/python/goldenflow/goldenflow/core/_native_loader.py \
        packages/python/goldenflow/tests packages/python/goldenflow/scripts/gen_identifiers_corpus.py
git commit -m "feat(goldenflow): owned card/Luhn identifier kernel (native-first, byte-parity)"
```

---

## Task 2: IBAN kernel

**Algorithm:** uppercase + `strip_sep`; validate: length 15-34, first two chars A-Z (country), chars 3-4 digits (check digits); move first 4 chars to the end; map each letter to two digits (A=10..Z=35); the resulting big integer mod 97 must equal 1 (compute iteratively: `acc = (acc * 10 + d) % 97` per digit, and `% 97` folding for letters' two digits). `iban_format` → validated value grouped in 4s (`GB82 WEST 1234 5698 7654 32`); invalid → None.
**Signatures:** `pub fn iban_validate(&str)->bool`, `pub fn iban_format(&str)->Option<String>`.
**Canonical test vectors:** valid `GB82 WEST 1234 5698 7654 32`, `DE89370400440532013000`, `FR1420041010050500013M02606`; invalid `GB82WEST12345698765433` (bad check), `XX00` (too short). `iban_format("DE89370400440532013000") == Some("DE89 3704 0044 0532 0130 00")`.

- [ ] Follow Task 1's pattern exactly: `iban.rs` (TDD red→green with the vectors above), shim `iban_validate_arrow`/`iban_format_arrow`, register in lib.rs, Python fallback in identifiers.py, loader `"iban": ("iban_validate_arrow",)`, corpus `iban` rows (regenerate via the oracle script), parity + unit tests. Rust `cargo test` green, clippy/fmt clean. Commit `feat(goldenflow): owned IBAN identifier kernel (mod-97)`.

---

## Task 3: ISBN kernel

**Algorithm:** `strip_sep` (keep a trailing `X`/`x` for ISBN-10). `isbn_validate`: ISBN-10 = 10 chars, first 9 digits + last digit-or-X, weighted sum `sum(d[i]*(10-i)) % 11 == 0` (X=10); ISBN-13 = 13 digits, `sum(d[i]* (1 if i even else 3)) % 10 == 0`. `isbn_normalize` → canonical ISBN-13 (no separators): if ISBN-10 valid, convert (`978` + first 9 digits, recompute check digit); if ISBN-13 valid, return the 13 digits; else None.
**Signatures:** `pub fn isbn_validate(&str)->bool`, `pub fn isbn_normalize(&str)->Option<String>`.
**Vectors:** valid ISBN-10 `0-306-40615-2`, ISBN-13 `978-0-306-40615-7`, `0-19-852663-6`; invalid `0306406153`. `isbn_normalize("0306406152") == Some("9780306406157")`.

- [ ] Follow Task 1's pattern: `isbn.rs` TDD, shims `isbn_validate_arrow`/`isbn_normalize_arrow`, register, Python fallback, loader `"isbn": ("isbn_validate_arrow",)`, corpus rows via oracle, tests. Commit `feat(goldenflow): owned ISBN identifier kernel (10/13 checksum + normalize)`.

---

## Task 4: EAN/UPC kernel

**Algorithm:** `strip_sep`; digits only. `ean_validate` accepts EAN-13, EAN-8, and UPC-A (12 digits, treat as EAN-13 with a leading 0). Check digit: from the right excluding the check digit, alternate weights 3 and 1 (EAN-13/UPC) — standard GTIN mod-10. Lengths accepted: 8, 12, 13 (and optionally 14/GTIN-14). Invalid length or bad check → false.
**Signature:** `pub fn ean_validate(&str)->bool`.
**Vectors:** valid EAN-13 `4006381333931`, EAN-8 `73513537`, UPC-A `036000291452`; invalid `4006381333930`.

- [ ] Follow Task 1's pattern: `ean.rs` TDD, shim `ean_validate_arrow`, register, Python fallback, loader `"ean": ("ean_validate_arrow",)`, corpus rows via oracle, tests. Commit `feat(goldenflow): owned EAN/UPC identifier kernel (GTIN mod-10)`.

---

## Task 5: EU VAT kernel (bounded)

**Scope commitment (from spec):** structural validation (country prefix + length/charset pattern) for ALL supported EU prefixes; full checksum ONLY where a public algorithm exists. Unsupported prefixes → false (documented). This bounds the rabbit hole.
- **Supported prefixes + structural patterns:** the 27 EU member states' documented formats (e.g. `DE` = `DE` + 9 digits; `FR` = `FR` + 2 chars + 9 digits; `IT` = `IT` + 11 digits; `NL` = `NL` + 9 digits + `B` + 2 digits; `ES` = `ES` + 9 chars; etc.). Put the pattern table in `vat.rs` as data.
- **Checksum coverage (initial):** implement the published checksums for at least `DE` (mod-11), `NL` (mod-11 weighted), `IT` (Luhn-like mod-10), `ES` (DNI/NIE/CIF), `FR` (mod-97 key). List exactly which prefixes are checksum-validated vs structural-only in a `// CHECKSUM: {...}` comment and in the docstring. More can be added later without changing the contract.
**Signatures:** `pub fn vat_validate(&str)->bool`, `pub fn vat_format(&str)->Option<String>` (uppercase, `strip_sep`, keep prefix; None if structurally invalid).
**Vectors:** valid `DE136695976`, `NL004495445B01` (structural+checksum), `IT00743110157`; invalid `DE136695970` (bad mod-11), `ZZ123` (unsupported prefix → false), `DE12345` (bad length).

- [ ] Follow Task 1's pattern: `vat.rs` TDD (both a structural-only prefix and a checksum prefix), shims `vat_validate_arrow`/`vat_format_arrow`, register, Python fallback (mirror the same prefix table + checksums), loader `"vat": ("vat_validate_arrow",)`, corpus rows via oracle, tests. Document the supported-prefix + checksum-coverage list in the transform docstring. Commit `feat(goldenflow): owned EU VAT identifier kernel (structural + published checksums)`.

---

## Task 6: Register transforms, selector, and count source-of-truth

**Files:** `goldenflow/goldenflow/__init__.py` (ensure `transforms/identifiers.py` is imported — it already is, confirm the new transforms register), `goldenflow/goldenflow/engine/selector.py` if identifiers need a type mapping, docs count check.

- [ ] **Step 1:** Confirm all 10 transforms register: `python -c "from goldenflow.transforms import registry; print(sorted(t for t in registry() if any(k in t for k in ('cc_','iban','isbn','ean','vat'))))"` (single invocation, POLARS_SKIP_CPU_CHECK=1). Expect the 10 names.
- [ ] **Step 2:** They are `auto_apply=False` (validate/normalize on request, like `ssn_format`) — verify zero-config does NOT apply them (a unit test: run `transform_df` on a frame with a card column, assert no `cc_*` transform in the manifest).
- [ ] **Step 3:** Add unit tests for the transform layer (a `pl.Series` in → expected `pl.Series` out) for at least one transform per family, both native (if wheel) and fallback.
- [ ] **Step 4: Commit** `test(goldenflow): identifier transform registration + zero-config posture`.

---

## Task 7: Corpus oracle-drift guard in CI

**Files:** `.github/workflows/ci.yml` (the `native_flow` lane added in Wave 0a).

- [ ] **Step 1:** In the `native_flow` lane (which already builds the wheel), after the build, run `python scripts/gen_identifiers_corpus.py --check` — regenerates the corpus in-memory from the native kernels and diffs against the checked-in `identifiers_corpus.jsonl`, failing on drift. This keeps the oracle and the committed corpus in lockstep. Also run `pytest tests/transforms/test_identifiers_parity.py` in that lane (native path) and in the `python_goldenflow_fallback` lane (fallback path — it already runs `tests/transforms`).
- [ ] **Step 2: Commit** `ci(goldenflow): identifier corpus oracle-drift guard + parity in native lane`.

---

## Task 8: PR

- [ ] Push `feat/goldenflow-identifiers-0b` (off updated main after 0a merged). Open PR `feat(goldenflow): Wave 0b — owned checksummed-identifier kernels`. Body: the 10 new transforms, native-first + byte-parity-to-oracle, VAT bounded scope, `auto_apply=False`. Note Wave 0c (WASM/TS) follows. Arm `--auto --squash` once the native + fallback lanes are green.

---

## Notes / guardrails

- **The kernel is the oracle.** Where a checksum/grouping choice is ambiguous (Amex grouping, VAT structural-vs-checksum per prefix), pick a rule, encode it in Rust, and make the Python fallback + corpus match. Don't chase an external "standard" beyond the documented scope.
- **No Polars authority tier** for these (unlike phone). Native-over-Arrow when the wheel exists, pure-Python otherwise — both byte-identical to the corpus.
- **`auto_apply=False`** for all 10 — zero-config must not silently rewrite card/IBAN/VAT columns.
- **VAT is explicitly bounded** — structural for all supported prefixes, checksum where public, unsupported → false + documented. Do not let VAT balloon the wave.
- Version bump: additive → **minor** bump for `goldenflow` (Python) at PR time; `goldenflow-native` wheel version bumps in lockstep (Cargo.toml + pyproject.toml + `__init__.py` fallback) since it gains new symbols and should be republished.
