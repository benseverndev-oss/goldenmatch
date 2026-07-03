# GoldenFlow Core Foundation (Wave 0a) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a pyo3-free `goldenflow-core` crate that owns GoldenFlow's phone kernel, make `native-flow` a thin shim over it, and flip goldenflow's native loader to suite-standard reference-mode — with zero change to any output.

**Architecture:** Mirror the goldenmatch `score-core` (pyo3-free, standalone workspace) + `native` (PyO3 shim, standalone workspace) split. The pure per-string phone functions move from `native-flow/src/phone.rs` into `goldenflow-core/src/phone.rs`; `native-flow` keeps only the Arrow marshaling (`util.rs`) and the `*_arrow` `#[pyfunction]` wrappers, which now call `goldenflow_core::phone::*`. The Python loader adopts the `_COMPONENT_SYMBOLS` + `_FALLBACK_ONLY` + `_has_symbol` reference-mode pattern from goldenmatch. `phone_validate` is held on `_FALLBACK_ONLY` because its only native symbol (`phone_valid_arrow` → `is_valid`) implements a different validity spec than the product-chosen `is_possible`.

**Tech Stack:** Rust (pyo3 abi3, arrow, `phonenumber` crate), Python 3.11+, Polars, maturin, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-02-goldenflow-core-cross-surface-wave0-design.md`

**Precedents to read before starting:**
- `packages/rust/extensions/score-core/Cargo.toml` — the standalone-workspace pyo3-free core.
- `packages/rust/extensions/native/Cargo.toml` — how a PyO3 shim path-deps a `-core`.
- `packages/python/goldenmatch/goldenmatch/core/_native_loader.py` — the reference-mode loader (`_COMPONENT_SYMBOLS`, `_FALLBACK_ONLY`, `_has_symbol`, `native_enabled`).

**Build/run notes (Windows dev):** the standalone core builds with plain `cargo test` from inside its own dir. The native ext builds via `pip install maturin && maturin build --release --out <dir>` then pip-install the wheel — do NOT rely on `scripts/build_native.py` locally (its `.so` copy step fails on Windows, which produces `.pyd`). Rust env per repo: `RUSTUP_HOME=D:/.rustup`, `CARGO_HOME=C:/Users/bsevern/.cargo`, PATH prepend `D:/.rustup/toolchains/1.94.1-x86_64-pc-windows-msvc/bin`. `POLARS_SKIP_CPU_CHECK=1` for local Polars imports.

---

## File Structure

- **Create** `packages/rust/extensions/goldenflow-core/Cargo.toml` — standalone-workspace pyo3-free crate, `phonenumber` dep.
- **Create** `packages/rust/extensions/goldenflow-core/src/lib.rs` — `pub mod phone;`.
- **Create** `packages/rust/extensions/goldenflow-core/src/phone.rs` — pure per-string phone fns (moved logic) + unit tests.
- **Modify** `packages/rust/extensions/native-flow/Cargo.toml` — add `goldenflow-core` path dep; drop the direct `phonenumber` dep.
- **Modify** `packages/rust/extensions/native-flow/src/phone.rs` — keep `*_arrow` pyfunctions; bodies now call `goldenflow_core::phone::*`.
- **Modify** `packages/rust/extensions/Cargo.toml` — add `goldenflow-core` to the workspace `exclude` list (standalone, like `goldencheck-core`).
- **Modify** `packages/python/goldenflow/goldenflow/core/_native_loader.py` — reference-mode flip.
- **Test (existing, must stay green)** `packages/python/goldenflow/tests/transforms/test_native_parity.py`.
- **Test (new/modify)** `packages/python/goldenflow/tests/core/test_native_loader.py` — loader reference-mode behavior.
- **Modify** `.github/workflows/ci.yml` — goldenflow native-default lane + `GOLDENFLOW_NATIVE=0` fallback lane (mirror the goldenmatch `python_goldenmatch` / `python_goldenmatch_fallback` jobs from PR #1346).

---

## Task 1: Scaffold `goldenflow-core` with a failing phone unit test

**Files:**
- Create: `packages/rust/extensions/goldenflow-core/Cargo.toml`
- Create: `packages/rust/extensions/goldenflow-core/src/lib.rs`
- Create: `packages/rust/extensions/goldenflow-core/src/phone.rs`

- [ ] **Step 1: Write the crate manifest**

`packages/rust/extensions/goldenflow-core/Cargo.toml`:
```toml
# Standalone workspace so this pyo3-free core can be a path dependency of BOTH
# the `native-flow` crate (its own workspace, extension-module) and, in Wave 0c,
# the `goldenflow-wasm` crate — without either workspace claiming it. Same
# isolation rationale as goldenmatch's score-core / fingerprint-core. No
# rust-toolchain.toml on purpose: inherits each parent crate's toolchain when
# built as a path dep.
[workspace]

[package]
name = "goldenflow-core"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "Owned reference kernels for GoldenFlow (phone; identifiers in Wave 0b), pyo3-free, shared across the native ext, WASM, and future SQL surfaces"

[lib]
name = "goldenflow_core"

[dependencies]
# Same pin native-flow used, so phone output is byte-identical before/after the
# extraction (parity by construction — one source of truth).
phonenumber = "0.3"
```

- [ ] **Step 2: Write `lib.rs`**

`packages/rust/extensions/goldenflow-core/src/lib.rs`:
```rust
//! GoldenFlow owned reference kernels (pyo3-free).
//!
//! This crate is the single source of truth for GoldenFlow's transform
//! primitives. The native PyO3 ext (`native-flow`) and, from Wave 0c, the WASM
//! surface (`goldenflow-wasm`) are thin marshaling shims over these functions.
//! The pure-Python / pure-TS transform paths are non-authoritative fallbacks
//! that must reproduce these bytes (asserted by the byte-parity harness).
pub mod phone;
```

- [ ] **Step 3: Write the failing phone test (no impl yet)**

`packages/rust/extensions/goldenflow-core/src/phone.rs`:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn e164_nanp_alpha() {
        // 1-800-FLOWERS is canonical NANP; nanp_only keeps it.
        let reg = region_of("US");
        assert_eq!(e164(reg, "1-800-356-9377", true).as_deref(), Some("+18003569377"));
    }

    #[test]
    fn e164_intl_dropped_under_nanp_only() {
        let reg = region_of("US");
        assert_eq!(e164(reg, "+33142685300", true), None);
    }

    #[test]
    fn country_code_and_valid() {
        let reg = region_of("US");
        assert_eq!(country_code(reg, "1-800-356-9377", true), Some(1));
        assert_eq!(valid(reg, "1-800-356-9377", true), Some(true));
    }
}
```

- [ ] **Step 4: Run the test to verify it fails to COMPILE (functions not defined)**

Run: `cd packages/rust/extensions/goldenflow-core && cargo test`
Expected: FAIL — `cannot find function region_of/e164/country_code/valid in this scope`.

---

## Task 2: Move the pure phone logic into `goldenflow-core`

**Files:**
- Modify: `packages/rust/extensions/goldenflow-core/src/phone.rs`

- [ ] **Step 1: Add the moved functions above the `tests` module**

Prepend to `phone.rs` (verbatim logic from the current `native-flow/src/phone.rs`, minus Arrow/pyo3):
```rust
//! International phone kernel — a Rust port of libphonenumber (`phonenumber`
//! crate). Pure functions over `&str`; the Arrow marshaling lives in the
//! native-flow shim (`util.rs`). Each fn returns `None` for a row it cannot
//! resolve, so the caller's Python fallback settles that row (never worse).
//!
//! `nanp_only`: emit a result ONLY for country-calling-code-1 numbers and
//! `None` otherwise — the parity-safe mode the gated default uses. The Rust
//! port is byte-identical to Python `phonenumbers` on NANP, but with a
//! mismatched default region ("US") it mis-strips a leading national "1" on
//! some `+CC` international numbers; restricting native to code-1 sidesteps that.
use phonenumber::{country, Mode, PhoneNumber};

pub fn region_of(region: &str) -> Option<country::Id> {
    region.parse::<country::Id>().ok()
}

fn parse(region: Option<country::Id>, s: &str) -> Option<PhoneNumber> {
    phonenumber::parse(region, s).ok()
}

fn parse_gated(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<PhoneNumber> {
    let n = parse(region, s)?;
    if nanp_only && n.country().code() != 1 {
        return None;
    }
    Some(n)
}

pub fn e164(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<String> {
    parse_gated(region, s, nanp_only).map(|n| n.format().mode(Mode::E164).to_string())
}

pub fn national(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<String> {
    parse_gated(region, s, nanp_only).map(|n| n.format().mode(Mode::National).to_string())
}

pub fn country_code(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<i64> {
    parse_gated(region, s, nanp_only).map(|n| i64::from(n.country().code()))
}

pub fn valid(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<bool> {
    // Same semantics as the current `phone_valid_arrow`: parsed-and-invalid ->
    // Some(false), parse failure -> None (Python decides). NOTE this is the
    // `is_valid` spec — which is exactly why `phone_validate` is held on
    // `_FALLBACK_ONLY` in the loader (the product spec is `is_possible`).
    parse_gated(region, s, nanp_only).map(|n| phonenumber::is_valid(&n))
}
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `cd packages/rust/extensions/goldenflow-core && cargo test`
Expected: PASS (3 tests). Also run `cargo clippy -- -D warnings` and `cargo fmt --check`.

- [ ] **Step 3: Commit**

```bash
git add packages/rust/extensions/goldenflow-core
git commit -m "feat(goldenflow-core): pyo3-free phone kernel extracted into owned core crate"
```

---

## Task 3: Rewire `native-flow` to depend on `goldenflow-core`

**Files:**
- Modify: `packages/rust/extensions/native-flow/Cargo.toml`
- Modify: `packages/rust/extensions/native-flow/src/phone.rs`
- Modify: `packages/rust/extensions/Cargo.toml`

- [ ] **Step 1: Add the path dep, drop the direct `phonenumber` dep**

In `native-flow/Cargo.toml` `[dependencies]`, remove the `phonenumber = "0.3"` line and add:
```toml
# Owned reference kernels (pure phone logic). native-flow is now a marshaling
# shim: Arrow in/out + GIL release here, the actual computation in the core.
goldenflow-core = { path = "../goldenflow-core" }
```
Keep `pyo3` and `arrow` unchanged.

- [ ] **Step 2: Rewrite `native-flow/src/phone.rs` as the Arrow shim**

Replace the whole file with (the four `*_arrow` pyfunctions, bodies delegating to core):
```rust
//! Arrow zero-copy surface over `goldenflow_core::phone`. Bytes in, call the
//! owned kernel per element, bytes out — GIL released around the loop. All
//! phone computation lives in goldenflow-core; this file only marshals.
use crate::util::{map_str_to_bool, map_str_to_i64, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::phone;
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_e164_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_str(py, array.0, move |s| phone::e164(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_national_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_str(py, array.0, move |s| phone::national(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_country_code_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_i64(py, array.0, move |s| phone::country_code(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_valid_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_bool(py, array.0, move |s| phone::valid(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}
```
(`util.rs` and `lib.rs` in native-flow are unchanged.)

- [ ] **Step 3: Add `goldenflow-core` to the extensions workspace exclude**

In `packages/rust/extensions/Cargo.toml`, add to the `exclude` list (near `native-flow`):
```toml
    # goldenflow-core is native-flow's pyo3-free path dependency (standalone
    # workspace, like score-core/goldencheck-core). Not a bridge member.
    "goldenflow-core",
```

- [ ] **Step 4: Build the native ext and confirm it compiles**

Run: `cd packages/rust/extensions/native-flow && cargo build --release`
Expected: builds clean. Run `cargo clippy -- -D warnings` and `cargo fmt --check`.

- [ ] **Step 5: Build the wheel and run the existing Python phone parity suite (must stay green — proves output-identical)**

```bash
cd packages/rust/extensions/native-flow
pip install maturin
maturin build --release --out target/wheels
pip install --force-reinstall target/wheels/goldenflow_native-*.whl
cd ../../../python/goldenflow
GOLDENFLOW_NATIVE=1 POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/transforms/test_native_parity.py -v
```
Expected: PASS — same results as before the refactor (byte-identical). If any test fails, the extraction changed behavior; fix before committing.

- [ ] **Step 6: Commit**

```bash
git add packages/rust/extensions/native-flow packages/rust/extensions/Cargo.toml
git commit -m "refactor(native-flow): shim over goldenflow-core; output-identical"
```

---

## Task 4: Flip the loader to reference-mode

**Files:**
- Modify: `packages/python/goldenflow/goldenflow/core/_native_loader.py`
- Test: `packages/python/goldenflow/tests/core/test_native_loader.py`

- [ ] **Step 1: Write failing loader tests**

Create/extend `packages/python/goldenflow/tests/core/test_native_loader.py`:
```python
import importlib
import os
import pytest
from goldenflow.core import _native_loader as L


def test_phone_validate_is_fallback_only(monkeypatch):
    # Even with native present, phone_validate must NOT dispatch to native:
    # its only native symbol (phone_valid_arrow -> is_valid) is the wrong spec.
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", object())  # pretend native is importable
    assert L.native_enabled("phone_validate") is False


def test_phone_wired_component_enabled_when_symbol_present(monkeypatch):
    class FakeNative:
        def phone_e164_arrow(self): ...
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", FakeNative())
    assert L.native_enabled("phone") is True


def test_force_off(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    monkeypatch.setattr(L, "_native", object())
    assert L.native_enabled("phone") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/python/goldenflow && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/core/test_native_loader.py -v`
Expected: FAIL (`phone_validate` currently isn't special-cased; `_FALLBACK_ONLY`/`_has_symbol` don't exist).

- [ ] **Step 3: Implement the reference-mode loader**

In `_native_loader.py`, replace `_GATED_ON` + `native_enabled` with the reference-mode block (mirror goldenmatch). Keep the import try/except and `native_module`/`native_available` unchanged. Replace the docstring `auto` bullet and add:
```python
# Reference-mode (2026-07: Rust is the reference). Under ``auto`` the native
# kernel runs wherever a WIRED symbol exists for the component, EXCEPT the
# known-divergent components in ``_FALLBACK_ONLY``. ``_GATED_ON`` is retained
# only as documentation of the byte-exact surface; it no longer governs ``auto``.
_GATED_ON: frozenset[str] = frozenset({"phone"})

# Floor symbols per component (wheel-skew safe: probe the actual module).
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "phone": ("phone_e164_arrow", "phone_national_arrow", "phone_country_code_arrow"),
    # NOTE: no "phone_validate" entry. Its only native symbol, phone_valid_arrow,
    # implements `is_valid`, NOT the product-chosen `is_possible` spec, so it is
    # deliberately unwired AND listed in _FALLBACK_ONLY below.
}

# Components whose only native path is intentionally non-authoritative (the
# native symbol exists but implements the wrong spec). Mirrors goldenmatch's
# _FALLBACK_ONLY={"sail_scoring"}.
_FALLBACK_ONLY: frozenset[str] = frozenset({"phone_validate"})


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    syms = _COMPONENT_SYMBOLS.get(component)
    if not syms:
        return False
    return any(hasattr(_native, s) for s in syms)


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call."""
    mode = os.environ.get("GOLDENFLOW_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENFLOW_NATIVE=1 but goldenflow._native is not built/importable"
            )
        return True
    return (
        _native is not None
        and component not in _FALLBACK_ONLY
        and _has_symbol(component)
    )
```
Also fix the module docstring: the stale claim "the `phonenumber` Rust crate exposes no `is_possible_number`" becomes: "`phone_valid_arrow`/`is_valid` exists but is not the chosen validity spec (`is_possible`), so `phone_validate` stays pure-Python via `_FALLBACK_ONLY`."

- [ ] **Step 4: Run loader tests + the phone parity suite (both green)**

Run:
```bash
cd packages/python/goldenflow
POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/core/test_native_loader.py -v
GOLDENFLOW_NATIVE=1 POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/transforms/test_native_parity.py -v
```
Expected: both PASS. Confirm the phone transforms still route to native under `auto` (`native_enabled("phone")` True) and `phone_validate` does not.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenflow/goldenflow/core/_native_loader.py packages/python/goldenflow/tests/core/test_native_loader.py
git commit -m "feat(goldenflow): reference-mode loader (_has_symbol + _FALLBACK_ONLY); phone_validate stays Python"
```

---

## Task 5: CI inversion — native-default lane + fallback lane

**Files:**
- Modify: `.github/workflows/ci.yml`

**Reference:** replicate the goldenmatch jobs added in PR #1346 (`python_goldenmatch` builds `_native` and runs the full suite native-default; `python_goldenmatch_fallback` runs a core slice under `GOLDENMATCH_NATIVE=0`). Read those jobs in `ci.yml` first and mirror them for goldenflow.

- [ ] **Step 1: Locate goldenflow's current CI job(s)** in `.github/workflows/ci.yml` (the dynamic python matrix + the `changes` path-filter entry for goldenflow). Note whether goldenflow currently builds the native wheel at all (today the phone parity tests are `@native_only` and skip without it).

- [ ] **Step 2: Make the native wheel a required build for goldenflow's default lane.** Add a build step (`maturin build` of `native-flow`, install the wheel) before pytest, so the goldenflow suite runs native-default. Mirror the goldenmatch native build step.

- [ ] **Step 3: Add a `GOLDENFLOW_NATIVE=0` fallback lane** (a small dedicated job or matrix entry) that installs goldenflow WITHOUT the native wheel and asserts the pure-Python path, with `-k "not native"` to skip native-dispatch-only tests. Wire it into the required-checks aggregation the same way #1346 did.

- [ ] **Step 4: Add the `goldenflow-core` + `native-flow` paths to the `changes` path-filter** so a core/shim change re-runs the goldenflow lanes.

- [ ] **Step 5: Push and let CI validate. DO NOT auto-merge until green** — CI is the validator for the native-default inversion (it can surface pure-Python-only test assumptions not reproducible locally). Reconcile any reds (update the test to the native path, or file a parity gap) before merge. Per repo SOP, arm auto-merge only after the lanes are green.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(goldenflow): native-default lane + GOLDENFLOW_NATIVE=0 fallback lane (reference-mode inversion)"
```

---

## Task 6: Open the PR

- [ ] **Step 1:** Push `feat/goldenflow-core-wave0` and open a PR titled `feat(goldenflow): Wave 0a — owned-kernel core + reference-mode loader`. Body: summarize the output-identical extraction, the reference-mode flip, and that phone_validate stays Python by `_FALLBACK_ONLY`. Note Wave 0b (identifiers) and 0c (WASM/TS) follow as stacked work.
- [ ] **Step 2:** Arm auto-merge (`gh pr merge --auto --squash`) only once the native + fallback lanes are green. Docs sweep is deferred to the end of Wave 0 (0c), per the spec — do NOT do a partial docs sweep here.

---

## Notes / guardrails

- **Output-identical is the contract for 0a.** The only way any user-visible output changes is if the phone extraction or the loader flip is wrong. `test_native_parity.py` is the guard; it must stay green under `GOLDENFLOW_NATIVE=1`.
- **`_FALLBACK_ONLY = {"phone_validate"}` is a defensive guard, not a rewiring.** There is no `native_enabled("phone_validate")` call site today (`phone_validate` has no `_native.py` helper), so it never routes to native regardless. The guard exists so a FUTURE wiring can't accidentally route it once a symbol map is in place. The Task 4 test asserts the guard holds; don't remove it as "dead."
- **`_MODE==1` bypasses `_FALLBACK_ONLY`** (require-native override) — same as goldenmatch; that's the caller forcing native on their own head, acceptable and consistent. Don't "fix" it.
- **Task 1 test vector:** `valid(reg, "1-800-356-9377", true) == Some(true)` assumes `phonenumber::is_valid` accepts the real 1-800-FLOWERS number. If it returns `Some(false)` at Task 2, suspect the test vector, not the extraction — swap in a plain NANP number like `"212-555-0100"`.
- **No new user-facing transforms in 0a.** Identifiers are Wave 0b. Keep this PR a pure structural + loader change so it reviews as zero-behavior-change.
- **Version bumps:** `goldenflow-native` wheel is unchanged in behavior; bump its version (Cargo.toml + pyproject.toml + `goldenflow_native/__init__.py` fallback, in lockstep) only if you publish a wheel from this change. In-tree/CI builds don't need a bump. `goldenflow` (Python) needs no bump for 0a (no output change).
