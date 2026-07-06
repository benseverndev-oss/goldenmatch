# InferMap Rust cutover — Wave 1 (`infermap-core` scaffold + `detect` kernel) — design

**Status:** approved (brainstorm 2026-07-06), pending spec review
**Motivation:** InferMap is the only core Golden Suite package with **zero Rust**. Wave 1
brings it into the unified cutover fold — a pyo3-free `infermap-core` as the single
source of truth — by standing up the crate family + parity infra on the most
self-contained, pure, **LLM-free** piece: domain auto-detection (`detect.py`). Native
+ Python this wave; WASM/TS is Wave 1b. Related: `project_rust_is_the_reference`,
the GoldenAnalysis P4 scaffold, ADR 0033.

## 1. Scope + decomposition (YAGNI)

InferMap = a schema-mapping engine: `MapEngine` orchestrates **extract schema → score
(M×N field similarity) → assign (Hungarian)**. The cutover decomposes as:

- **Wave 1 (this spec):** the `infermap-core`/`infermap-native` scaffold + the `detect`
  domain-detection kernel. Native + Python only.
- **Wave 1b:** `infermap-wasm` + TS dispatch for `detect` (string/JSON boundary).
- **Wave 2+:** the pure scorers (`exact`, `initialism`, `alias`, then `fuzzy_name`
  Jaro-Winkler — the real M×N muscle, with `rapidfuzz`-parity risk). The **LLM scorer
  stays host** (uncuttable).
- **Wave N:** `assignment` (scipy Hungarian — LAP tie-break parity risk) and
  `calibration`. `MapEngine` orchestration stays host ("smart pipe").

**Honest framing:** `detect` is small-compute (O(domains × hints × columns) token
matching). This wave is **scaffold + anti-drift** (single-source the tokenize / match /
score / tie logic, currently hand-rolled identically in `detect.py` and `detect.ts`),
NOT a perf win. It is the right *first* cutover: lowest-risk way to stand up the crate
family + parity gate + build/publish infra before the heavier scorers (cf. GoldenAnalysis
scaffolding on histogram/quantile first).

## 2. Architecture — smart pipe / dumb kernel

`detect.py` keeps the host responsibilities: load domain packs via
`goldencheck_types.load_domain`, build each domain's **deduped** `name_hints`, and wrap
the kernel's output in a `goldencheck_types.DetectionResult`. The pure scoring + decision
moves into the kernel. The pack *data* never becomes a Rust dependency — only passed-in
strings.

## 3. The kernel (`infermap-core`, pyo3-free)

```rust
pub struct Detection {
    pub domain: Option<String>,
    pub score: f64,
    pub runner_up: Option<String>,
    pub runner_up_score: f64,
    pub reason: String,   // "confident" | "tie" | "below_min_score" | "no_data"
}

/// `columns`: the df's column names. `domains`: (name, deduped name_hints) IN HOST
/// ORDER. Mirrors `detect.py::detect_domain_detailed`'s scoring + decision exactly.
pub fn detect_domain(columns: &[String], domains: &[(String, Vec<String>)], min_score: f64) -> Detection {
    if columns.is_empty() { return Detection{ domain: None, score: 0.0, runner_up: None, runner_up_score: 0.0, reason: "no_data".into() }; }
    // score each domain: hits = count of columns matching ANY hint; score = hits/len
    let mut scored: Vec<(String, f64)> = Vec::new();
    for (name, hints) in domains {
        if hints.is_empty() { continue; }   // matches `if not all_hints: continue`
        let hits = columns.iter().filter(|c| hints.iter().any(|h| hint_matches(h, c))).count();
        scored.push((name.clone(), hits as f64 / columns.len().max(1) as f64));
    }
    if scored.is_empty() { return Detection{ ...reason: "no_data" }; }
    // STABLE sort desc by score to match Python's `sort(key=score, reverse=True)`
    // (stable, ties keep original/host order). Use sort_by with a stable sort +
    // reverse comparator that DOES NOT reorder ties (see §6).
    // ... best / runner_up / tie-count / min_score gate → reason.
}

// tokenize on [_\-.\s]+, lowercase, drop empties (see §6 Unicode note)
fn tokens(s: &str) -> Vec<String> { ... }
// true iff hint's tokens appear as a contiguous run in col's tokens
fn hint_matches(hint: &str, col: &str) -> bool { ... }
```

Decision logic (byte-identical to `detect.py`): `best = scored[0]` after stable
desc-sort; `runner_up = scored.get(1)`; `if best.score < min_score → below_min_score`;
`else if count(score == best.score) > 1 → tie`; `else → confident`. All `Option`/`0.0`
fields exactly as the Python `DetectionResult(...)` branches.

Unit tests in `infermap-core`: multi-token hint run, empty columns → no_data, tie,
below-min-score, runner-up, hint longer than column, ASCII-case-insensitivity.

## 4. `infermap-native` (abi3 pyo3 shim — takes strings, NO Arrow)

Unlike the analysis kernels, `detect` needs no Arrow — pyo3 extracts Python lists
directly:

```rust
#[pyfunction]
fn detect_domain(
    columns: Vec<String>,
    domains: Vec<(String, Vec<String>)>,
    min_score: f64,
) -> PyResult<(Option<String>, f64, Option<String>, f64, String)> {
    let d = infermap_core::detect_domain(&columns, &domains, min_score);
    Ok((d.domain, d.score, d.runner_up, d.runner_up_score, d.reason))
}
```
Returns a tuple (pyo3-native, no custom class needed); the Python host maps it to the
5 `DetectionResult` fields. `#[pymodule] fn _native(...)` registers it.

## 5. Python dispatch + gate + scaffold

- **`infermap/_native_loader.py`** — mirror `goldenanalysis/core/_native_loader.py`:
  discover `infermap._native` (in-tree) → `infermap_native._native` (wheel) → None;
  `INFERMAP_NATIVE` env gate (`auto`/`0`/`1`, `1` = require-native-or-raise);
  `_COMPONENT_SYMBOLS = {"detect_domain": "detect_domain"}` (functional gate via
  `_has_symbol`); `_GATED_ON = frozenset({"detect_domain"})` (doc).
- **`detect.py`** — extract the scoring+decision into a dispatcher. `detect_domain_detailed`
  loads packs → builds `domains: list[(name, sorted-or-insertion-order list[str])]` →
  calls `_detect_core(columns, domains, min_score)` → wraps the returned 5-tuple in
  `DetectionResult`. `_detect_core`:
  ```python
  def _detect_core(columns, domains, min_score):
      if native_enabled("detect_domain"):
          return native_module().detect_domain(columns, domains, min_score)
      return _detect_core_pure(columns, domains, min_score)
  ```
  `_detect_core_pure` is the byte-identical reference (the current `_tokens`/`_hint_matches`
  + scoring/sort/tie/min-score logic, refactored to take `domains` explicitly). NO
  try/except (pure-Python-input, no dtype rejection to fall back from).
  **Host ordering:** build `domains` in the SAME order Python currently iterates
  (`candidates or [d for d in list_domains() if d != "generic"]`) so the stable sort's
  tie-handling is identical native vs pure.
- **Packaging (lockstep, mirror goldenanalysis):**
  - `packages/rust/extensions/infermap-core/` + `infermap-native/` crates (abi3 maturin,
    `pyproject.toml [project].version` = `0.1.0`, Cargo `0.1.0`; bump BOTH in lockstep).
  - `packages/python/infermap/pyproject.toml`: add `native = ["infermap-native>=0.1.0"]`
    to `[project.optional-dependencies]`.
  - root `pyproject.toml [tool.uv.sources]`: `infermap-native = { path =
    "packages/rust/extensions/infermap-native" }`.
  - `packages/rust/extensions/Cargo.toml` workspace `exclude += "infermap-native"`
    (standalone abi3 ext-module, like `analysis-native`); `infermap-core` joins
    `members` (a normal lib crate).
  - `scripts/build_infermap_native.py` (mirror `build_analysis_native.py`).
  - `.github/workflows/publish-infermap-native.yml` (tag `infermap-native-v*`; mirror
    `publish-goldenanalysis-native.yml`).
  - Commit `Cargo.lock` (globally gitignored → force-add).

## 6. Parity gate + the load-bearing Unicode risk

- **`packages/python/infermap/tests/test_native_parity.py`** — a `native_only` skip
  guard (skips without the wheel; the CI lane builds it and runs under
  `INFERMAP_NATIVE=1`). Asserts `native == _detect_core_pure` across fixtures built from
  the REAL domain packs (`list_domains()` / `load_domain`): a healthcare-ish df
  (confident), a df matching two packs equally (tie), a df below min-score, an empty df
  (no_data), multi-token hints, and hint-longer-than-column. Also a box-safe
  `test_native_dtype_or_absent` verifying the pure path stands alone.
- **CI:** add an `infermap_native` lane to `ci.yml` (build the wheel, run the parity
  suite under `INFERMAP_NATIVE=1`), gated on the infermap path filter, in `ci-required`.
- **THE load-bearing parity risk — Unicode lowercasing.** `_tokens` does `s.lower()`;
  Python `str.lower()` and Rust `str::to_lowercase()` use *different* Unicode case
  mappings at the edges (Turkish `İ`, German `ß`→`ss`, etc.). Resolution: the kernel
  matches Python for **ASCII** (which all realistic column names are); the parity
  fixtures use ASCII, and non-ASCII column names are a **documented parity edge** (the
  one place native could diverge from pure) — NOT chased for byte-parity in Wave 1. If a
  future need arises, the fix is to restrict tokenization to ASCII-lowercase in BOTH
  surfaces. (This is `detect`'s analogue of the `mean` naive-summation nuance /
  `dates` non-portability lesson.) The regex `[_\-.\s]+` split is implemented as a
  manual char scan in Rust (split on `_`, `-`, `.`, and Unicode whitespace) to avoid
  regex-crate `\s` semantics drift; a fixture pins the separator set.

## 7. Rollout / docs

- Branch `feat/infermap-core-wave1-detect`, off fresh `origin/main`. Rust is CI-built
  (box can't `cargo build`); Python pure path box-verified; run `ruff` on touched Python.
- `native_symbols` gate: `check_native_symbols.py` REGISTRY gains `infermap` (host ref
  `native_module().detect_domain` ↔ the one `wrap_pyfunction!` export) — added together,
  self-reconciles. (Confirm the gate's per-package idiom fits `infermap`'s
  `native_module()` loader.)
- CLAUDE.md: an InferMap-cutover note (scaffold established; detect kernel; Unicode-
  lowercase edge; scorers/assignment/calibration = later waves; LLM scorer stays host).
- api-surface.mdx: goldenanalysis-style — the InferMap "Native / SQL" column flips from
  `—` to `wheel` once shipped (docs sweep at rollout, not in this PR).

## 8. Risks

- **Unicode lowercasing** (§6) — the one real parity risk; resolved by ASCII-scoping +
  documenting the edge.
- **Stable-sort tie handling** — Rust `sort_by` is stable; the reverse comparator must
  NOT reorder equal-score ties (use `b.score.partial_cmp(&a.score)` on a stable sort, or
  sort ascending then reverse-index carefully). A fixture with a 3-way score tie pins it.
- **Cold-start scaffold surface area** — large but mechanical; every piece mirrors the
  proven GoldenAnalysis P4 native scaffold. The risk is an omitted lockstep step (uv
  source / Cargo exclude / Cargo.lock force-add) breaking `uv sync` — the plan enumerates
  each.
- **`float` score equality** — `hits/len` is exact rational-ish f64; `score == best_score`
  and `score < min_score` comparisons are on identical f64 both surfaces (same op order),
  so exact. min_score default `0.3`.
