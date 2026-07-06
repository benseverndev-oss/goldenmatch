# InferMap Rust cutover — Wave 1 (`infermap-core` + `detect`) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stand up `infermap-core` (pyo3-free) + `infermap-native` (abi3 wheel) + Python dispatch + a `native == pure` parity gate, cutting the `detect` domain-detection logic to Rust as the single source of truth. Native + Python only (WASM/TS = Wave 1b).

**Architecture:** Smart-pipe/dumb-kernel — `detect.py` loads domain packs (host) and passes `(columns, domains_with_hints, min_score)` to the kernel; `infermap-core::detect_domain` does the pure tokenize→match→score→sort→tie/min-score decision. `infermap-native` is a thin pyo3 shim taking Python lists (NO Arrow).

**Tech Stack:** Rust (pyo3/abi3 maturin, CI-built — box can't `cargo build`), Python 3.11+ (box-testable pure path), mirrors the GoldenAnalysis P4 native scaffold.

**Branch:** `feat/infermap-core-wave1-detect` (already created off `origin/main`; the spec is committed on it).

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-core-wave1-detect-design.md`

**Env notes:**
- Rust does NOT build on this box — write code + `#[cfg(test)]` tests; do not run `cargo`. CI is the gate.
- Python pure path IS box-runnable: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, prefix `PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0`. Run `ruff check` on touched Python.
- gh: `unset GH_TOKEN; gh auth switch --user benzsevern`. Merge-queue repo — no `--delete-branch`. GraphQL may be rate-limited → create PR via `gh api ... /pulls` (REST).

---

### Task 1: `infermap-core` crate (the kernel)

**Files:**
- Create `packages/rust/extensions/infermap-core/Cargo.toml`
- Create `packages/rust/extensions/infermap-core/src/lib.rs`

- [ ] **Step 1: `Cargo.toml`** (a normal lib crate; a member of NO workspace — it's a path-dep of infermap-native; give it its own empty `[workspace]` OR rely on infermap-native's. Mirror `analysis-core/Cargo.toml`: it has NO `[workspace]` line and is pulled in by analysis-native's standalone workspace. Match analysis-core exactly.)

```toml
[package]
name = "infermap-core"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <ben@bensevern.dev>"]
description = "Pyo3-free kernels for InferMap (schema mapping); the single source of truth."

[lib]
name = "infermap_core"

[dependencies]

[profile.release]
opt-level = 3
lto = "thin"
```
(Match `analysis-core/Cargo.toml`: it declares NO `[workspace]` line — isolation comes from the `exclude` in `extensions/Cargo.toml`, Task 3 — and it DOES carry the `[profile.release]` block above. No external deps: tokenization is a manual char scan, no `regex` crate.)

- [ ] **Step 2: Write the kernel + tests** (`src/lib.rs`)

```rust
//! InferMap kernels (pyo3-free). Single source of truth mirrored value-for-value by
//! `infermap/detect.py::_detect_core_pure` and `detect.ts`.

#[derive(Debug, Clone, PartialEq)]
pub struct Detection {
    pub domain: Option<String>,
    pub score: f64,
    pub runner_up: Option<String>,
    pub runner_up_score: f64,
    pub reason: String,
}

/// Tokenize on `_`, `-`, `.`, and ASCII/Unicode whitespace; lowercase; drop empties.
/// (See spec §6: `\s` diverges from Python at `\x1c`-`\x1f`/`\x85` — documented edge;
/// real column names are ASCII.)
fn tokens(s: &str) -> Vec<String> {
    s.split(|c: char| c == '_' || c == '-' || c == '.' || c.is_whitespace())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_lowercase())
        .collect()
}

/// True iff `hint`'s tokens appear as a contiguous run in `col`'s tokens.
fn hint_matches(hint: &str, col: &str) -> bool {
    let h = tokens(hint);
    let c = tokens(col);
    if h.is_empty() || c.is_empty() {
        return false;
    }
    // windows(n) yields nothing when n > c.len() -- no usize underflow.
    c.windows(h.len()).any(|w| w == h.as_slice())
}

/// Domain auto-detection. `columns`: df column names. `domains`: (name, deduped hints)
/// IN HOST ORDER. Byte-mirror of `detect.py::detect_domain_detailed` scoring+decision.
pub fn detect_domain(columns: &[String], domains: &[(String, Vec<String>)], min_score: f64) -> Detection {
    let no_data = || Detection {
        domain: None, score: 0.0, runner_up: None, runner_up_score: 0.0, reason: "no_data".to_string(),
    };
    if columns.is_empty() {
        return no_data();
    }
    let mut scored: Vec<(String, f64)> = Vec::new();
    for (name, hints) in domains {
        if hints.is_empty() {
            continue;
        }
        let hits = columns.iter().filter(|c| hints.iter().any(|h| hint_matches(h, c))).count();
        scored.push((name.clone(), hits as f64 / columns.len() as f64)); // columns non-empty
    }
    if scored.is_empty() {
        return no_data();
    }
    // STABLE desc sort by score; ties keep host order (matches Python sort(reverse=True)).
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let (best_name, best_score) = scored[0].clone();
    let (runner_up, runner_up_score) = match scored.get(1) {
        Some((n, s)) => (Some(n.clone()), *s),
        None => (None, 0.0),
    };
    if best_score < min_score {
        return Detection { domain: None, score: best_score, runner_up, runner_up_score, reason: "below_min_score".to_string() };
    }
    let top_count = scored.iter().filter(|(_, s)| *s == best_score).count();
    if top_count > 1 {
        return Detection { domain: None, score: best_score, runner_up, runner_up_score, reason: "tie".to_string() };
    }
    Detection { domain: Some(best_name), score: best_score, runner_up, runner_up_score, reason: "confident".to_string() }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn d(name: &str, hints: &[&str]) -> (String, Vec<String>) {
        (name.to_string(), hints.iter().map(|s| s.to_string()).collect())
    }
    fn cols(xs: &[&str]) -> Vec<String> { xs.iter().map(|s| s.to_string()).collect() }

    #[test]
    fn confident_multitoken_hint() {
        // "provider npi" hint matches column "provider_npi" (contiguous run)
        let r = detect_domain(&cols(&["provider_npi", "first_name"]),
            &[d("health", &["provider npi"]), d("fin", &["iban"])], 0.3);
        assert_eq!(r.domain, Some("health".to_string()));
        assert_eq!(r.reason, "confident");
        assert_eq!(r.score, 0.5); // 1 of 2 columns hit
    }
    #[test]
    fn empty_columns_no_data() {
        assert_eq!(detect_domain(&[], &[d("h", &["x"])], 0.3).reason, "no_data");
    }
    #[test]
    fn no_hints_no_data() {
        assert_eq!(detect_domain(&cols(&["a"]), &[d("h", &[])], 0.3).reason, "no_data");
    }
    #[test]
    fn below_min_score() {
        let r = detect_domain(&cols(&["a", "b", "c", "d"]), &[d("h", &["a"])], 0.3);
        assert_eq!(r.reason, "below_min_score"); // 1/4 = 0.25 < 0.3
        assert_eq!(r.domain, None);
    }
    #[test]
    fn tie_two_domains() {
        let r = detect_domain(&cols(&["a", "b"]), &[d("x", &["a"]), d("y", &["b"])], 0.3);
        assert_eq!(r.reason, "tie"); // both 0.5
        assert_eq!(r.domain, None);
    }
    #[test]
    fn hint_longer_than_column_no_underflow() {
        // hint "a b c" (3 tokens) vs column "a" (1 token) -- windows(3) empty, no panic
        assert!(!hint_matches("a b c", "a"));
    }
    #[test]
    fn ascii_case_insensitive() {
        assert!(hint_matches("NPI", "provider_npi"));
    }
}
```

- [ ] **Step 3: Verify (read-only)** — matches spec §3; no external deps; tests reference the fns. No local build.
- [ ] **Step 4: Commit** — `git add packages/rust/extensions/infermap-core && git commit -m "feat(infermap-core): detect_domain kernel (Wave 1)"`

---

### Task 2: `infermap-native` crate (pyo3 shim + maturin layout)

**Files:**
- Create `packages/rust/extensions/infermap-native/Cargo.toml`
- Create `packages/rust/extensions/infermap-native/pyproject.toml`
- Create `packages/rust/extensions/infermap-native/src/lib.rs`
- Create `packages/rust/extensions/infermap-native/python/infermap_native/__init__.py`
- Create `packages/rust/extensions/infermap-native/README.md`

- [ ] **Step 1: `Cargo.toml`** — mirror `analysis-native/Cargo.toml` EXACTLY but drop `arrow` + `rustc-hash` (detect needs no Arrow). Keep the standalone `[workspace]`, `[lib] name="_native" crate-type=["cdylib"]`, `pyo3 = { version=">=0.28,<0.29", features=["extension-module","abi3-py311"] }`, dep `infermap-core = { path="../infermap-core" }`, and the `[profile.release]` block. Keep the header comment explaining the standalone-workspace rationale.

- [ ] **Step 2: `src/lib.rs`** (register with `self::` per spec §4/§7 — the `_WRAP` regex needs a `::`)

```rust
use infermap_core::detect_domain as core_detect;
use pyo3::prelude::*;

#[pyfunction]
fn detect_domain(
    columns: Vec<String>,
    domains: Vec<(String, Vec<String>)>,
    min_score: f64,
) -> PyResult<(Option<String>, f64, Option<String>, f64, String)> {
    let d = core_detect(&columns, &domains, min_score);
    Ok((d.domain, d.score, d.runner_up, d.runner_up_score, d.reason))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // `self::` qualification is REQUIRED: check_native_symbols._WRAP is
    // `wrap_pyfunction!\(\s*(?:\w+::)+(\w+)` -- the bare form would not be scanned.
    m.add_function(wrap_pyfunction!(self::detect_domain, m)?)?;
    Ok(())
}
```

- [ ] **Step 3: `pyproject.toml`** — mirror `analysis-native/pyproject.toml`: `[build-system] maturin`; `[project] name="infermap-native" version="0.1.0"`; `[tool.maturin] python-source="python"` + `module-name="infermap_native._native"`. Update the header comment to infermap.

- [ ] **Step 4: `python/infermap_native/__init__.py`** — mirror the goldenanalysis_native wrapper docstring (ships only the compiled `_native`; discovered via `infermap._native_loader`).

- [ ] **Step 5: `README.md`** — short (maturin needs `readme`); mirror analysis-native's.

- [ ] **Step 6: Verify (read-only).** Commit — `"feat(infermap-native): pyo3 detect_domain shim + maturin layout (Wave 1)"`

---

### Task 3: Workspace + lockstep wiring (the `uv sync`-breaking steps)

**Files:**
- Modify `packages/rust/extensions/Cargo.toml` (workspace exclude)
- Modify root `pyproject.toml` (`[tool.uv.sources]`)
- Modify `packages/python/infermap/pyproject.toml` (`native` extra)
- Create `packages/rust/extensions/infermap-native/Cargo.lock` (force-add; globally gitignored)

- [ ] **Step 1: workspace exclude** — in `packages/rust/extensions/Cargo.toml`, add `"infermap-native"` AND `"infermap-core"` to `exclude` (both halves — see spec §5), mirroring the existing goldencheck/analysis comment blocks. `members` stays `["bridge"]`.
- [ ] **Step 2: uv source** — root `pyproject.toml` `[tool.uv.sources]`: add `infermap-native = { path = "packages/rust/extensions/infermap-native" }`.
- [ ] **Step 3: `native` extra** — `packages/python/infermap/pyproject.toml` `[project.optional-dependencies]`: add `native = ["infermap-native>=0.1.0"]`. Do NOT add it to the `all` extra (mirror goldenmatch — compiled native stays out of aggregates).
- [ ] **Step 4: Cargo.lock** — `infermap-native/Cargo.lock` is a **normally-tracked** file (NOT gitignored — all five existing native crates' lockfiles are tracked via plain `git add`, maintained by a human). The box can't `cargo generate-lockfile`. This is **NOT a blocker**: the `*_native` CI lanes build WITHOUT `--locked`/`--frozen`, so cargo regenerates a missing lock and CI stays green. So: if a cargo-capable machine is available, generate + `git add packages/rust/extensions/infermap-native/Cargo.lock`; otherwise **omit it** and let CI/a maintainer add it later. Do NOT use `git add -f` / claim it's gitignored.
- [ ] **Step 5: verify `uv sync` still resolves** — `D:/show_case/goldenmatch/.venv` is the shared env; run `uv sync --all-packages 2>&1 | tail` (or the repo's documented sync) to confirm the new path source + extra don't break resolution. Expected: clean sync (a small lock diff for the new path source).
- [ ] **Step 6: Commit** — `"build(infermap): wire infermap-native (uv source, native extra, workspace exclude)"`

---

### Task 4: Python loader + `detect.py` dispatch (box-testable)

**Files:**
- Create `packages/python/infermap/infermap/_native_loader.py`
- Modify `packages/python/infermap/infermap/detect.py`
- Test: `packages/python/infermap/tests/test_detect_dispatch.py` (new)

- [ ] **Step 1: `_native_loader.py`** — mirror `goldenanalysis/core/_native_loader.py` VERBATIM with these substitutions: `GOLDENANALYSIS_NATIVE`→`INFERMAP_NATIVE`; import paths `infermap._native` → `infermap_native._native`; `_GATED_ON = frozenset({"detect_domain"})`; `_COMPONENT_SYMBOLS = {"detect_domain": "detect_domain"}`. Keep `native_module()`, `native_available()`, `native_enabled(component)`, `_has_symbol` identical.

- [ ] **Step 2: Write the failing test** (`tests/test_detect_dispatch.py`) — pure-path unit tests of the refactored `_detect_core_pure` + `detect_domain_detailed`:

```python
"""Wave 1 detect dispatch/pure-path tests (box-safe; INFERMAP_NATIVE=0)."""
import polars as pl

from infermap.detect import _detect_core_pure, detect_domain_detailed


def test_core_pure_confident():
    r = _detect_core_pure(["provider_npi", "first_name"],
                          [("health", ["provider npi"]), ("fin", ["iban"])], 0.3)
    assert r == ("health", 0.5, "fin", 0.0, "confident")


def test_core_pure_tie():
    r = _detect_core_pure(["a", "b"], [("x", ["a"]), ("y", ["b"])], 0.3)
    assert r[0] is None and r[4] == "tie"


def test_core_pure_below_min():
    r = _detect_core_pure(["a", "b", "c", "d"], [("h", ["a"])], 0.3)
    assert r[0] is None and r[4] == "below_min_score"


def test_core_pure_no_data_empty_cols():
    assert _detect_core_pure([], [("h", ["x"])], 0.3)[4] == "no_data"


def test_detect_domain_detailed_end_to_end():
    # real packs via goldencheck_types; a healthcare-ish frame should resolve
    df = pl.DataFrame({"provider_npi": [1], "patient_id": [2]})
    res = detect_domain_detailed(df)
    assert res.reason in {"confident", "tie", "below_min_score"}  # deterministic, not no_data
```

- [ ] **Step 3: Run — expect fail** (`_detect_core_pure` doesn't exist yet):
```
PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/infermap/tests/test_detect_dispatch.py -q
```

- [ ] **Step 4: Refactor `detect.py`** — extract the scoring/decision into `_detect_core_pure(columns, domains, min_score) -> tuple` + a `_detect_core` dispatcher; `detect_domain_detailed` loads packs → builds `domains` in host order → calls `_detect_core` → wraps the 5-tuple in `DetectionResult`. Exact shape:

```python
from infermap._native_loader import native_enabled, native_module

def _detect_core(columns, domains, min_score):
    if native_enabled("detect_domain"):
        return native_module().detect_domain(columns, domains, min_score)
    return _detect_core_pure(columns, domains, min_score)

def _detect_core_pure(columns, domains, min_score):
    if not columns:
        return (None, 0.0, None, 0.0, "no_data")
    scored = []
    for name, hints in domains:
        if not hints:
            continue
        hits = sum(1 for c in columns if any(_hint_matches(h, c) for h in hints))
        scored.append((name, hits / len(columns)))
    if not scored:
        return (None, 0.0, None, 0.0, "no_data")
    scored.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = scored[0]
    runner_name, runner_score = (scored[1] if len(scored) > 1 else (None, 0.0))
    if best_score < min_score:
        return (None, best_score, runner_name, runner_score, "below_min_score")
    if sum(1 for _, s in scored if s == best_score) > 1:
        return (None, best_score, runner_name, runner_score, "tie")
    return (best_name, best_score, runner_name, runner_score, "confident")
```
And rewrite `detect_domain_detailed` to:
```python
def detect_domain_detailed(df, candidates=None, min_score=DEFAULT_MIN_SCORE):
    columns = [str(c) for c in df.columns]
    domain_names = candidates or [d for d in list_domains() if d != "generic"]
    domains = []
    for d in domain_names:
        pack = load_domain(d)
        hints = list({h for spec in pack.types.values() for h in spec.name_hints})
        domains.append((d, hints))
    domain, score, runner_up, runner_up_score, reason = _detect_core(columns, domains, min_score)
    return DetectionResult(domain=domain, score=score, runner_up=runner_up,
                           runner_up_score=runner_up_score, reason=reason)
```
Keep `_tokens` + `_hint_matches` module-level (used by `_detect_core_pure`). `detect_domain` (the thin wrapper) is unchanged (`return detect_domain_detailed(...).domain`).

**BYTE-IDENTITY CHECK:** the old code built `all_hints` as a set *inside* the loop and skipped empties there; the new code builds the deduped `hints` host-side and the pure fn skips empties — same result (any-match is dedup/order-invariant). Confirm `no_data`-on-empty-columns still returns BEFORE pack loading is unnecessary — the old code returned no_data before scoring; the new `_detect_core_pure` returns no_data on empty columns too, so `detect_domain_detailed` can call it unconditionally. (Verify the old `columns` empty branch semantics are preserved.)

- [ ] **Step 5: Run the dispatch tests + the FULL existing detect tests** (the refactor must not regress):
```
PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/infermap/tests/test_detect_dispatch.py \
  packages/python/infermap/tests/ -k "detect" -q
ruff check packages/python/infermap/infermap/detect.py \
  packages/python/infermap/infermap/_native_loader.py \
  packages/python/infermap/tests/test_detect_dispatch.py
```
Expected: PASS + ruff clean.

- [ ] **Step 6: Commit** — add the 3 files; `"feat(infermap): native loader + detect dispatch (Wave 1)"`

---

### Task 5: Parity gate + build script + publish workflow + CI lane

**Files:**
- Create `packages/python/infermap/tests/test_native_parity.py`
- Create `scripts/build_infermap_native.py`
- Create `.github/workflows/publish-infermap-native.yml`
- Modify `.github/workflows/ci.yml` (infermap_native parity lane)

- [ ] **Step 1: parity test** — mirror `goldenanalysis/tests/core/test_native_parity.py`'s `native_only` skip idiom. Compares `native_module().detect_domain(...)` vs `_detect_core_pure(...)` across fixtures built from the REAL packs:
```python
native_only = pytest.mark.skipif(not native_available(), reason="infermap native ext not built")
_CASES = [ ... ]  # confident, tie, below_min_score, empty->no_data, multi-token, hint>col, 3-way tie
@native_only
@pytest.mark.parametrize("columns,domains,min_score", _CASES)
def test_detect_parity(columns, domains, min_score):
    from infermap.detect import _detect_core_pure
    assert tuple(native_module().detect_domain(columns, domains, min_score)) == _detect_core_pure(columns, domains, min_score)
```
Include a 3-way score-tie fixture (pins the stable-sort tie order, spec §8) and an ASCII-only fixture (Unicode edge is out of scope, spec §6).

- [ ] **Step 2: build script** — `scripts/build_infermap_native.py`, mirror `scripts/build_analysis_native.py` (builds the crate, drops `infermap/_native.<abi3>.so`). Substitute crate path `infermap-native` + target name `infermap/_native`.

- [ ] **Step 3: publish workflow** — `.github/workflows/publish-infermap-native.yml`, mirror `publish-goldenanalysis-native.yml`: trigger on `infermap-native-v*` tag; build both macOS arches on `macos-14`; `workflow_dispatch` publish toggle.

- [ ] **Step 4: CI parity lane** — add an `infermap_native` job to `ci.yml` mirroring `goldenanalysis_native` (build the wheel, run `pytest packages/python/infermap/tests/test_native_parity.py` under `INFERMAP_NATIVE=1`). **ADVISORY, not blocking** — `goldenanalysis_native` is NOT in the `ci-required` needs list (`ci.yml:~3668`), so do NOT add `infermap_native` there either (mirror = advisory). The `changes`-job wiring needs **THREE** edits (not just a filter) — mirror `analysis_native` exactly:
  1. an **output line** in the `changes` job `outputs:` (`ci.yml:~73`): `infermap_native: ${{ steps.filter.outputs.infermap_native }}` — without this `needs.changes.outputs.infermap_native` is always empty and the lane never runs;
  2. a **filter block** (`ci.yml:~569`) watching the host + crate paths: `packages/python/infermap/infermap/detect.py`, `.../infermap/_native_loader.py`, `packages/rust/extensions/infermap-core/**`, `packages/rust/extensions/infermap-native/**`, `packages/python/infermap/tests/test_native_parity.py`, `scripts/build_infermap_native.py`;
  3. the job `if:` gate (`ci.yml:~2447` region) `if: needs.changes.outputs.infermap_native == 'true'`.

- [ ] **Step 5: Verify parity test collects + skips cleanly on the box** (no wheel → skip, not error):
```
PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/infermap/tests/test_native_parity.py -q
ruff check packages/python/infermap/tests/test_native_parity.py scripts/build_infermap_native.py
```
Expected: all SKIPPED, 0 errors, ruff clean.

- [ ] **Step 6: Commit** — `"test(infermap): native parity gate + build/publish/CI wiring (Wave 1)"`

---

### Task 6: native_symbols gate entry

**Files:**
- Modify `scripts/check_native_symbols.py` (REGISTRY += infermap)
- Create `parity/native_symbols/infermap.allow` (empty, if `load_allow` needs the file to exist)
- Modify `.github/workflows/ci.yml` (native_symbols matrix, if hardcoded)

- [ ] **Step 1: REGISTRY entry** — add to `scripts/check_native_symbols.py` `REGISTRY`:
```python
    "infermap": {
        "crate_reg": ["packages/rust/extensions/infermap-native/src/lib.rs"],
        "py_root": "packages/python/infermap/infermap",
        "loader_tokens": ("native_module",),
        "allow": "parity/native_symbols/infermap.allow",
    },
```
- [ ] **Step 2: allow file** — `load_allow` tolerates a missing file (returns `set()`), so `parity/native_symbols/infermap.allow` is **OPTIONAL — skip it** (the gate reconciles fully with zero allow-list entries). Only create an empty one if a future symbol needs suppression.
- [ ] **Step 3: run the gate locally** (box-safe — it's pure Python, static scan):
```
D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/check_native_symbols.py infermap 2>&1 | tail
```
Expected: PASS — `detect_domain` referenced (via `native_module().detect_domain`) reconciles with the `self::detect_domain` registration. If it FAILs "scanned zero references" the loader_tokens are wrong; if "missing" the `self::` registration regex didn't match — fix per spec §7.
- [ ] **Step 4: CI wiring** — the `native_symbols` CI job is a single hardcoded run step `run: python scripts/check_native_symbols.py goldenmatch` (`ci.yml:~1540`), NOT a matrix. Add a **second run step** in the same job: `run: python scripts/check_native_symbols.py infermap`. (It triggers correctly — the new `infermap.allow` + the `check_native_symbols.py` edit both fall under the existing `native_symbols` path filter.)
- [ ] **Step 5: Commit** — `"chore(infermap): native_symbols gate entry (Wave 1)"`

---

### Task 7: CLAUDE.md note

- [ ] **Step 1** — add a note to root `CLAUDE.md`: InferMap entered the Rust fold — `infermap-core` (pyo3-free) + `infermap-native` on the `detect` domain-detection kernel (smart-pipe: packs stay host, hints passed in; NO Arrow — pyo3 takes strings). `INFERMAP_NATIVE` gate. GOTCHAs: register `wrap_pyfunction!(self::detect_domain, m)` (the `_WRAP` regex needs `::`); exclude BOTH `-core` and `-native` from the workspace; `str.lower()`/`\s` diverge from Rust at non-ASCII + `\x1c`-`\x1f` (ASCII-scoped, documented edge — py/ts already disagree there). Scorers (M×N muscle) + assignment (scipy Hungarian) + calibration = later waves; LLM scorer stays host. WASM/TS = Wave 1b.
- [ ] **Step 2: Commit** — `"docs: InferMap Wave 1 CLAUDE note"`

---

### Finalize

- [ ] Push `feat/infermap-core-wave1-detect`; open PR base `main` (REST if GraphQL rate-limited). PR body: InferMap's first Rust cutover — scaffold + `detect` kernel; native-first (WASM=Wave 1b); the `self::`/workspace-exclude/Unicode gotchas; anti-drift+scaffold not perf; scorers/assignment/calibration later. Rust/parity are CI-gated; Python pure path verified locally.
- [ ] `gh pr merge --auto --squash` (NO `--delete-branch`) and STOP. Do not poll CI.
