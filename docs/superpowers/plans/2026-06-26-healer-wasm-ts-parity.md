# Healer on WASM + TS — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the healer (config-suggestion loop) to the TS/JS surface by compiling the existing `suggest-core` Rust kernel to WebAssembly and wiring it into the TS `goldenmatch` package with full default-pipeline parity (the same automatic `dedupe({suggest, heal})` surface shipped in Python).

**Architecture:** One Rust kernel (`suggest-core`), now a second binding (`suggest-wasm`, a `wasm-bindgen` cdylib) alongside the Python `native` pyo3 shim. The load-bearing kernel change is feature-gating arrow + adding a pure `suggest_from_json` entry both paths share (zero Python change). TS wires the healer into `dedupe()` via a lean registry backend (default null → graceful-empty) that the heavy opt-in `goldenmatch/core/suggest-wasm` subpath registers — the exact TS analog of `pip install goldenmatch[native]`. The `column_signals` batch is caller-built, so TS gets a `suggestColumnSignals.ts` builder over existing TS functions.

**Tech Stack:** Rust (`suggest-core`, `wasm-bindgen`, `wasm-pack`, `wasm32-unknown-unknown`), TypeScript (`goldenmatch` pkg: vitest, tsup, esbuild), Python (parity fixtures only).

**Spec:** `docs/superpowers/specs/2026-06-26-healer-wasm-ts-parity-design.md`

---

## Reference files to mirror (READ THESE FIRST)

The repo has a complete `-core → -wasm → TS` precedent for autoconfig. Mirror it; do not invent a new pattern.

| Concern | Mirror this file exactly |
|---|---|
| arrow feature-gating in a `-core` crate | `packages/rust/extensions/autoconfig-core/Cargo.toml` (`arrow = ["dep:arrow", ...]`, `optional=true`) |
| wasm-bindgen wrapper crate | `packages/rust/extensions/autoconfig-wasm/{Cargo.toml,src/lib.rs}` |
| build script (committed glue + base64 bytes + fixtures) | `packages/typescript/goldenmatch/scripts/build_autoconfig_wasm.mjs` |
| lean registry backend (edge-safe, `import type` only) | `packages/typescript/goldenmatch/src/core/autoconfigWasmBackend.ts` |
| heavy opt-in module + `initSync` | `packages/typescript/goldenmatch/src/core/autoconfigWasm.ts` + the `goldenmatch/core/autoconfig-wasm` subpath export in `package.json` |
| committed wasm outputs | `packages/typescript/goldenmatch/src/core/_wasm/autoconfigWasm*.{js,d.ts,ts}` |
| wasm parity tests | `packages/typescript/goldenmatch/tests/parity/autoconfig-wasm-*.test.ts` |
| Python healer (the behavior to match) | `packages/python/goldenmatch/goldenmatch/core/suggest/{adapter.py,surface.py}` + `packages/python/goldenmatch/goldenmatch/_api.py` (the `dedupe_df` wiring lives in `_api.py`, NOT under `core/suggest/`) |
| CI wasm/native lanes | `.github/workflows/ci.yml` (`autoconfig` native filter + the TS job) |

**Toolchain / environment constraints (from the repo CLAUDE.md + memory):**
- TS build/test OOMs Ben's Windows box — **run `tsc`/`vitest`/`tsup` in CI, not locally** (targeted single-file vitest is sometimes OK; full suite is not). Committed `_wasm/` artifacts mean TS tooling needs **no** Rust toolchain.
- The wasm build (`build_suggest_wasm.mjs`) needs `wasm-pack` + the `wasm32-unknown-unknown` target. Run it where those exist; commit its outputs.
- Rust: prefer `cargo check`/`cargo test -p goldenmatch-suggest-core`; `cargo build` of heavy ext crates can OOM — keep to the small `suggest-core`/`suggest-wasm` crates.
- TS worktree install on exFAT D: has known friction — see memory `reference_ts_worktree_install_exfat`.

---

## Phase A — Kernel refactor (Rust, shared by all consumers)

### Task 1: Feature-gate arrow + serde + arrow-free constructors in `suggest-core`

**Files:**
- Modify: `packages/rust/extensions/suggest-core/Cargo.toml`
- Modify: `packages/rust/extensions/suggest-core/src/diagnostics.rs`
- Modify: `packages/rust/extensions/suggest-core/src/api.rs`
- Modify: `packages/rust/extensions/suggest-core/src/lib.rs` (module gating if needed)

This is behavior-preserving: the existing golden tests in `api.rs` (which use arrow) must stay green, so the crate's **dev/test build keeps the arrow feature on**.

- [ ] **Step 1: Make arrow optional in Cargo.toml.** Mirror autoconfig-core:

```toml
[dependencies]
arrow = { version = "59", default-features = false, optional = true }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
analysis-core = { path = "../analysis-core" }

[features]
default = []
arrow = ["dep:arrow"]

[dev-dependencies]
# golden + unit tests use arrow batches -> build tests with the feature on.
```

Note: `cargo test` must run with `--features arrow` (the tests construct `RecordBatch`). Add a comment in Cargo.toml saying so.

- [ ] **Step 2: Add serde derives to `ColumnSignal`** (`diagnostics.rs:16`):

```rust
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ColumnSignal { /* unchanged fields */ }
```

- [ ] **Step 3: Gate the arrow constructors + add arrow-free twins.** In `diagnostics.rs`:
  - Annotate `column_signals_from_batch`, `ScoreDiagnostics::from_batch`, `ClusterDiagnostics::from_batch` with `#[cfg(feature = "arrow")]` and gate their `use arrow::...` imports.
  - Add always-compiled arrow-free constructors that hold the exact math `from_batch` uses:

```rust
impl ScoreDiagnostics {
    /// Arrow-free twin of `from_batch`. `scores` = non-null pair scores;
    /// `n_pairs` = total rows incl. null scores (matches batch.num_rows()).
    pub fn from_scores(scores: &[f64], n_pairs: usize, threshold: f64, bins: i64) -> Self {
        let n = scores.len();
        if n == 0 {
            return Self { histogram: vec![], mass_above: 0.0, mass_just_below: 0.0, n_pairs };
        }
        let above = scores.iter().filter(|&&s| s >= threshold).count();
        let band_lo = (threshold - 0.10).max(0.0);
        let just_below = scores.iter().filter(|&&s| s >= band_lo && s < threshold).count();
        let histogram = analysis_core::histogram(scores, bins);
        Self { histogram, mass_above: above as f64 / n as f64,
               mass_just_below: just_below as f64 / n as f64, n_pairs }
    }
}

impl ClusterDiagnostics {
    /// Arrow-free twin. `quality` = per-cluster quality labels; `oversized` aligned.
    pub fn from_rows(quality: &[String], oversized: &[bool], n_clusters: usize) -> Self {
        let mut weak = 0usize; let mut split = 0usize;
        for q in quality { match q.as_str() { "weak" => weak += 1, "split" => split += 1, _ => {} } }
        let oversized_n = oversized.iter().filter(|&&b| b).count();
        Self { weak, oversized: oversized_n, split, n_clusters }
    }
}
```

  - Refactor the `#[cfg(feature = "arrow")]` `from_batch` constructors to extract the raw vecs and **delegate** to `from_scores`/`from_rows` (so the math lives in one place). `column_signals_from_batch` stays arrow-only (it extracts into `ColumnSignal`, which is now also serde-decodable for the JSON path).

- [ ] **Step 3b: Gate the existing test modules on the arrow feature.** The test modules in `api.rs` (`:81`) and `diagnostics.rs` (`:285`) use `use arrow::...` unconditionally and are gated only `#[cfg(test)]`. Once arrow is an *optional non-dev* dep, bare `cargo test` (no `--features arrow`) fails to **compile** those modules. Change both to `#[cfg(all(test, feature = "arrow"))]` so plain `cargo test` compiles cleanly (with zero arrow tests) instead of erroring.

- [ ] **Step 4: Gate the arrow `suggest()` in `api.rs`.** Annotate the existing `pub fn suggest(scored_pairs: &RecordBatch, ...)` and its `use arrow::...` with `#[cfg(feature = "arrow")]`. Leave the body otherwise unchanged.

- [ ] **Step 5: Verify the arrow path still builds + golden tests pass.**

Run: `cargo test -p goldenmatch-suggest-core --features arrow`
Expected: PASS (all existing unit + golden tests green — behavior preserved).

- [ ] **Step 6: Verify the no-arrow build compiles.**

Run: `cargo build -p goldenmatch-suggest-core` (no `--features`)
Expected: compiles with arrow absent (the arrow-free constructors + ColumnSignal serde available; no arrow symbols referenced).

- [ ] **Step 7: Commit.**

```bash
git add packages/rust/extensions/suggest-core
git commit -m "refactor(suggest-core): feature-gate arrow + arrow-free diagnostics constructors + ColumnSignal serde"
```

### Task 2: `suggest_from_json` entry point + arrow-equivalence test

**Files:**
- Modify: `packages/rust/extensions/suggest-core/src/api.rs`
- Test: in `api.rs` `#[cfg(test)]` (gated on `feature = "arrow"` since it compares to the arrow path)

> **CRITICAL — read `api.rs::suggest()` before writing code.** The real `suggest()`
> (api.rs:50-68) does NOT compute one `ScoreDiagnostics` from a single threshold. It
> **loops over `config.matchkeys`**; for each matchkey with `Some(threshold)` it builds
> a *fresh* `ScoreDiagnostics::from_batch(scored_pairs, mk.threshold, HISTOGRAM_BINS)`
> (`HISTOGRAM_BINS = 24`, api.rs:11) and runs `threshold_rule`; `scorer_swap_rule` runs
> **per matchkey**; `negative_evidence_rule` runs **once globally**. There is **no
> `config.threshold()` method** (`ConfigSummary` has only `matchkeys` + `negative_evidence`,
> contract.rs:41-45). So the JSON path must run the identical per-matchkey loop.

- [ ] **Step 1: Extract a shared post-parse fn FIRST (refactor, no behavior change).** In
  `api.rs`, pull the body of `suggest()` after the diagnostics are available into a private
  fn that takes the already-decoded raw inputs and runs the exact per-matchkey loop. The key
  move: have it accept the **raw score vec + n_pairs** (not a pre-built `ScoreDiagnostics`)
  so it can build a per-matchkey `ScoreDiagnostics::from_scores(&scores, n_pairs, mk.threshold,
  HISTOGRAM_BINS)` inside the loop — identical to today's per-matchkey `from_batch`.

```rust
const HISTOGRAM_BINS: i64 = 24;  // already at api.rs:11

/// Shared core: the per-matchkey rule loop. Both the arrow `suggest()` and the
/// JSON entry decode their inputs to these raw values, then call this — true
/// single source of truth.
fn suggest_core(
    scores: &[f64],           // non-null pair scores
    n_pairs: usize,           // total rows incl. null scores
    cluster_diag: &ClusterDiagnostics,
    signals: &[ColumnSignal],
    config: &ConfigSummary,
    priors: &AcceptancePriors,
) -> Vec<Suggestion> {
    let mut out = Vec::new();
    for mk in &config.matchkeys {
        if let Some(t) = mk.threshold {
            let score_diag = ScoreDiagnostics::from_scores(scores, n_pairs, t, HISTOGRAM_BINS);
            out.extend(threshold_rule(&score_diag, mk, config, priors));   // EXACT current args
        }
        out.extend(scorer_swap_rule(signals, mk, config, priors));         // per matchkey
    }
    out.extend(negative_evidence_rule(&cluster_diag, signals, config, priors)); // once
    rank(out, priors)  // EXACT current rank call
}
```

  Refactor the arrow `suggest()` to decode the batches → `scores`/`n_pairs` (via the score
  array) + `ClusterDiagnostics::from_batch` + `column_signals_from_batch`, then call
  `suggest_core(...)` and `serde_json::to_string`. **Read the real signatures of
  `threshold_rule`/`scorer_swap_rule`/`negative_evidence_rule`/`rank` and match them exactly**
  — the above arg lists are illustrative. Run `cargo test --features arrow` after this refactor
  alone — all existing golden/unit tests MUST still pass (pure refactor).

- [ ] **Step 2: Write the failing equivalence test** (`#[cfg(all(test, feature = "arrow"))]`),
  with THREE cases so a single-threshold shortcut can't pass: (a) the existing single-matchkey
  golden, (b) a **multi-matchkey** config (≥2 matchkeys, each with a threshold), (c) a config
  with a **no-threshold matchkey** (`threshold: None`) mixed with a thresholded one.

```rust
#[test]
fn json_path_matches_arrow_path_multikey() {
    let arrow_out = suggest(&sp_batch, &cl_batch, &cs_batch, config_json, priors_json).unwrap();
    let sp_json = /* {"score":[...non-null...], "n_pairs": N} */;
    let cl_json = /* [{"quality":..,"oversized":..}, ...] */;
    let cs_json = serde_json::to_string(&column_signals_vec).unwrap();
    let json_out = suggest_from_json(&sp_json, &cl_json, &cs_json, config_json, priors_json).unwrap();
    assert_eq!(arrow_out, json_out);
}
```

- [ ] **Step 3: Run it to confirm it fails** (`suggest_from_json` undefined).

Run: `cargo test -p goldenmatch-suggest-core --features arrow json_path_matches_arrow_path_multikey`
Expected: FAIL (function not defined).

- [ ] **Step 4: Implement `suggest_from_json`** (always compiled — no arrow), delegating to
  the shared `suggest_core`:

```rust
#[derive(serde::Deserialize)]
struct ScoredPairsJson { score: Vec<f64>, n_pairs: usize }
#[derive(serde::Deserialize)]
struct ClusterRowJson { quality: String, oversized: bool }

/// Pure-JSON entry: same per-matchkey loop as the arrow `suggest()` (via `suggest_core`).
pub fn suggest_from_json(
    scored_pairs_json: &str, clusters_json: &str, column_signals_json: &str,
    config_json: &str, priors_json: &str,
) -> Result<String, String> {
    let config: ConfigSummary = serde_json::from_str(config_json).map_err(|e| e.to_string())?;
    let priors: AcceptancePriors = serde_json::from_str(priors_json).map_err(|e| e.to_string())?;
    let sp: ScoredPairsJson = serde_json::from_str(scored_pairs_json).map_err(|e| e.to_string())?;
    let cl: Vec<ClusterRowJson> = serde_json::from_str(clusters_json).map_err(|e| e.to_string())?;
    let signals: Vec<ColumnSignal> = serde_json::from_str(column_signals_json).map_err(|e| e.to_string())?;
    let quality: Vec<String> = cl.iter().map(|c| c.quality.clone()).collect();
    let oversized: Vec<bool> = cl.iter().map(|c| c.oversized).collect();
    let cluster_diag = ClusterDiagnostics::from_rows(&quality, &oversized, cl.len());
    let ranked = suggest_core(&sp.score, sp.n_pairs, &cluster_diag, &signals, &config, &priors);
    serde_json::to_string(&ranked).map_err(|e| e.to_string())
}
```

- [ ] **Step 5: Run the equivalence test — PASS** (all three cases).

Run: `cargo test -p goldenmatch-suggest-core --features arrow`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add packages/rust/extensions/suggest-core/src/api.rs
git commit -m "feat(suggest-core): suggest_from_json entry (arrow-free, shared per-matchkey core) + multi-key equivalence test"
```

---

## Phase B — WASM crate + build

### Task 3: `suggest-wasm` crate

**Files:**
- Create: `packages/rust/extensions/suggest-wasm/Cargo.toml`
- Create: `packages/rust/extensions/suggest-wasm/src/lib.rs`

- [ ] **Step 1: Cargo.toml** — copy `autoconfig-wasm/Cargo.toml`, rename to `goldenmatch-suggest-wasm`, dep on `goldenmatch-suggest-core = { path = "../suggest-core" }` (NO arrow feature). Keep `crate-type=["cdylib"]`, `opt-level="s"`, `lto=true`, `[package.metadata.wasm-pack.profile.release] wasm-opt = false`.

- [ ] **Step 2: lib.rs** — one wasm-bindgen fn mirroring `autoconfig-wasm/src/lib.rs` style:

```rust
use goldenmatch_suggest_core::suggest_from_json;
use wasm_bindgen::prelude::*;

/// JSON in (the 5 args, packed into one object) -> suggestion JSON array.
#[wasm_bindgen]
pub fn suggest_review(input_json: &str) -> Result<String, JsError> {
    #[derive(serde::Deserialize)]
    struct In { scored_pairs: String, clusters: String, column_signals: String,
                config: String, priors: String }
    let i: In = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad suggest input json: {e}")))?;
    suggest_from_json(&i.scored_pairs, &i.clusters, &i.column_signals, &i.config, &i.priors)
        .map_err(|e| JsError::new(&e))
}
```

  Add `serde = { version="1", features=["derive"] }` + `serde_json` to the crate deps.

- [ ] **Step 3: Verify it builds for wasm.**

Run: `cargo build -p goldenmatch-suggest-wasm --target wasm32-unknown-unknown`
Expected: compiles (no arrow pulled).

- [ ] **Step 4: Commit.**

```bash
git add packages/rust/extensions/suggest-wasm
git commit -m "feat(suggest-wasm): wasm-bindgen wrapper over suggest_from_json"
```

### Task 4: `build_suggest_wasm.mjs` + committed outputs + golden fixtures

**Files:**
- Create: `packages/typescript/goldenmatch/scripts/build_suggest_wasm.mjs`
- Create (generated, committed): `packages/typescript/goldenmatch/src/core/_wasm/suggestWasmBindings.js`, `suggestWasmBindings.d.ts`, `suggestWasmBytes.ts`
- Create (generated, committed): `packages/typescript/goldenmatch/tests/parity/fixtures/suggest/*.json`

- [ ] **Step 1: Copy + adapt `build_autoconfig_wasm.mjs`** → `build_suggest_wasm.mjs`. Point `wasmCrate`/`coreCrate` at `suggest-wasm`/`suggest-core`; output names `suggestWasm*`. Keep the async-init strip (neutralize `import.meta.url`, re-export only `initSync`). Emit base64 bytes into `suggestWasmBytes.ts`.

- [ ] **Step 2: Author golden fixtures from the RUST kernel, not the wasm.** The fixtures'
  `expected` must come from an **independent oracle**, not from the wasm we're about to test
  against them (that would be tautological — it'd pin determinism, not correctness). Mirror
  autoconfig, whose build script *copies pre-authored golden files* from the crate. Concretely:
  add a `#[cfg(all(test, feature = "arrow"))]` Rust test in `suggest-core` that constructs a
  handful of cases (one per rule: lower_threshold, raise_threshold, swap_scorer,
  add_negative_evidence, plus an empty/no-op case), runs `suggest_from_json`, and **writes**
  each `{ input, expected }` to `suggest-core/tests/golden/suggest/<case>.json` (only when a
  `BLESS=1` env is set; otherwise it asserts the committed file matches — same bless pattern as
  the gym baseline). The `expected` is thus authored + guarded by the Rust kernel. Then have
  `build_suggest_wasm.mjs` **copy** those files into `tests/parity/fixtures/suggest/` (exactly
  as `build_autoconfig_wasm.mjs` copies `coreCrate/golden/*`). Commit both the crate golden
  files and the copied TS fixtures.

- [ ] **Step 3: Run the build** (where wasm-pack + wasm32 target exist):

Run: `node packages/typescript/goldenmatch/scripts/build_suggest_wasm.mjs`
Expected: writes the 3 `_wasm/suggestWasm*` files + the fixtures; prints sizes.

- [ ] **Step 4: Commit the generated artifacts.**

```bash
git add packages/typescript/goldenmatch/scripts/build_suggest_wasm.mjs \
        packages/typescript/goldenmatch/src/core/_wasm/suggestWasm* \
        packages/typescript/goldenmatch/tests/parity/fixtures/suggest
git commit -m "build(ts): build_suggest_wasm.mjs + committed wasm glue/bytes + golden fixtures"
```

---

## Phase C — TS kernel binding + column signals

### Task 5: `suggestWasm.ts` (heavy) + `suggestWasmBackend.ts` (lean registry) + opt-in subpath

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/suggestWasmBackend.ts` (lean registry; mirror `autoconfigWasmBackend.ts`)
- Create: `packages/typescript/goldenmatch/src/core/suggestWasm.ts` (heavy: `initSync(bytes)` + `enableSuggestWasm()` that registers)
- Modify: `packages/typescript/goldenmatch/package.json` (add the `./core/suggest-wasm` subpath export, mirror `./core/autoconfig-wasm`)
- Test: `packages/typescript/goldenmatch/tests/unit/suggestWasmBackend.test.ts`

- [ ] **Step 1: Failing test** — `getSuggestWasmBackend()` returns null by default; after `setSuggestWasmBackend(stub)` returns the stub; `disableSuggestWasm()` clears it.

- [ ] **Step 2:** Implement `suggestWasmBackend.ts` mirroring `autoconfigWasmBackend.ts` exactly (edge-safe, `import type` only). Backend interface:

```ts
export interface SuggestWasmBackend {
  /** raw kernel call: the 5 JSON strings packed -> suggestion JSON string. */
  suggestReview(input: SuggestKernelInput): string;
}
```

- [ ] **Step 3:** Implement `suggestWasm.ts` (heavy) mirroring `autoconfigWasm.ts`: `initSync(suggestWasmBytes)` once, wrap `suggest_review`, expose `enableSuggestWasm()` that calls `setSuggestWasmBackend(...)`. Wrap the init + every call in try/catch → on failure leave the backend unregistered (graceful-empty).

- [ ] **Step 4:** Add the `./core/suggest-wasm` export to `package.json` (mirror the autoconfig subpath line).

- [ ] **Step 5: Run the unit test — PASS** (no wasm needed; registry only).

Run (CI or targeted): `vitest run tests/unit/suggestWasmBackend.test.ts`

- [ ] **Step 6: Commit.**

```bash
git commit -am "feat(ts): suggest-wasm lean registry + heavy opt-in subpath (graceful-empty default)"
```

### Task 6: `suggestColumnSignals.ts` + column-signal parity

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/suggestColumnSignals.ts`
- Test: `packages/typescript/goldenmatch/tests/parity/suggest-column-signals.parity.test.ts`
- Reference (Python source of truth): `packages/python/goldenmatch/goldenmatch/core/suggest/adapter.py::_build_column_signals_batch`

- [ ] **Step 1: Failing parity test.** Add a Python-side fixture emitter (small script or extend the build) that dumps `_build_column_signals_batch` output for a known `(rows, clusters, config)` to `tests/parity/fixtures/suggest/column_signals_<case>.json`. The TS test builds the same signals via `buildColumnSignals(rows, clusters, config)` and asserts deep-equality (numeric fields within a tight epsilon, or exact if both round identically).

- [ ] **Step 2: Implement `buildColumnSignals`** field-by-field (see spec "Caller-built column signals"):
  - `identity_score`, `corruption_score` → `computeColumnPriors` (`indicators.ts`).
  - `col_type` → `profiler.ts` column classification (`ColumnProfile.colType`).
  - `cardinality_ratio` = distinct/non-null; `null_rate` = nulls/total — direct reductions.
  - `in_blocking` from the resolved config's blocking fields; `in_negative_evidence` from config negative-evidence fields; `scorer` from the config's matchkey field scorer.
  - `collision_rate` → port `_collision_rates` (fraction of multi-member clusters where the column's non-null values disagree).
  - `variant_rate` → `0.0` default (matches Python's goldencheck-absent path).
  Return `ColumnSignal[]` matching the Rust struct's JSON shape (snake_case keys).

- [ ] **Step 3: Run parity test — PASS.**

- [ ] **Step 4: Commit.**

```bash
git commit -am "feat(ts): suggestColumnSignals builder + Python parity fixture"
```

---

## Phase D — TS healer module + dedupe wiring

### Task 7: `suggest.ts` — the TS healer surface

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/suggest.ts`
- Reference: `packages/python/goldenmatch/goldenmatch/core/suggest/{surface.py,adapter.py}`
- Test: `packages/typescript/goldenmatch/tests/unit/suggest.test.ts`

Implements (mirroring Python names/behavior):
- `serializeSuggestions(suggestions, { verified }) -> SerializedSuggestion[]` — wire shape `{id, kind, target, rationale, verified, patch}`.
- `headroomSignal(postflightReport | undefined) -> HeadroomReason | null` — the score-distribution trigger (bimodal `scoreHistogram` OR a threshold `adjustment` fired); `undefined` report → null. (NOT the controller-health half — documented divergence.)
- `suggestFromResult(result, rows, { verify }) -> Suggestion[]` — build the 3 inputs (scoredPairs→`{score, n_pairs}`, clusters→`[{quality, oversized}]`, `buildColumnSignals`), pack config+priors, call the registered backend; **null backend → `[]`** (graceful-empty). `verify` path: see Task 8 cap note.
- `maybeSuggest(result, rows, { verify }) -> Suggestion[]` — kill-switch + trigger gate, then `suggestFromResult`.
- `reviewConfig(rows, config, { verify }) -> Suggestion[]` — run a dedupe, then `suggestFromResult`.
- `heal(rows, config, { stepCap }) -> { config, trail, result }` — bounded apply-and-re-run loop; cycle guard.

- [ ] **Step 1: Failing tests** — (a) `serializeSuggestions` shape + `verified` flag plumbed from the caller; (b) `headroomSignal` returns null on a healthy/undefined report and a reason on a bimodal one; (c) `suggestFromResult` returns `[]` when the backend registry is null (graceful-empty); (d) with a stub backend returning a known JSON, it parses to typed suggestions.
- [ ] **Step 2: Implement** `suggest.ts`.
- [ ] **Step 3: Run tests — PASS.**
- [ ] **Step 4: Commit.** `git commit -am "feat(ts): suggest.ts healer surface (serialize/headroom/maybeSuggest/heal, graceful-empty)"`

### Task 8: Wire into `dedupe()` (default-pipeline surface)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/api.ts` (`DedupeOptions`, `dedupe`, result type)
- Modify: `packages/typescript/goldenmatch/src/core/types.ts` (`DedupeResult` gains `suggestions`, `healTrail`)
- Test: `packages/typescript/goldenmatch/tests/unit/dedupe-suggest-wiring.test.ts` (mirror Python `test_dedupe_suggest_wiring.py`)

- [ ] **Step 1: Failing tests** (mirror the Python wiring tests):
  - default run, trigger forced off (stub `headroomSignal`→null) → `result.suggestions === []`, backend NOT called (cost guarantee), `healTrail` undefined.
  - default run, trigger forced on + stub backend → `result.suggestions` carries serialized candidates with `verified: false`.
  - `{ suggest: true }` → `verified: true`.
  - `{ heal: true }` → `result.config` is the healed config, `healTrail` populated (`verified: true`).
  - kill-switch: `GOLDENMATCH_SUGGEST_ON_DEDUPE=0` (or the `suggest:false` explicit option in edge) → no trigger, no backend call.

- [ ] **Step 2: Implement.** Add `suggest?: boolean`, `heal?: boolean` to `DedupeOptions`. After the pipeline result is built, in a try/catch advisory block (never throw):
  - `heal` → run `heal()`, set `config`/`healTrail`/serialized trail (`verified:true`).
  - else `suggest` → `maybeSuggest(result, rows, {verify:true})`, serialize `verified:true`.
  - else (default) → kill-switch check (`process.env.GOLDENMATCH_SUGGEST_ON_DEDUPE !== "0"`, guarded for non-Node) + explicit option; `maybeSuggest(result, rows, {verify:false})`, serialize `verified:false`.
  - Mirror Python's `_MAX_VERIFY_CANDIDATES = 8` cap inside the `verify` path; each verified candidate is a **TS pipeline re-run** (not a per-candidate wasm call).

- [ ] **Step 3: Run tests — PASS.**
- [ ] **Step 4: Commit.** `git commit -am "feat(ts): wire healer into dedupe() (default surface + suggest/heal + kill-switch)"`

### Task 9: Kernel golden-vector parity (TS == fixtures) + Python cross-surface check

**Files:**
- Test (TS): `packages/typescript/goldenmatch/tests/parity/suggest-wasm.parity.test.ts` (mirror `autoconfig-wasm-*.parity.test.ts`)
- Test (Python): `packages/python/goldenmatch/tests/test_suggest_wasm_crossparity.py` (native-gated)

- [ ] **Step 1 (TS):** For each `tests/parity/fixtures/suggest/<case>.json`, enable the wasm backend (`enableSuggestWasm()`), call `suggestReview(input)`, assert the output JSON equals `expected` (deep-equal). Skip gracefully if wasm can't init in the test env (but it should — autoconfig parity tests run wasm in vitest). This proves the TS/wasm binding faithfully exposes the kernel whose `expected` the Rust oracle authored.
- [ ] **Step 2 (Python cross-surface):** to back the spec's "Python == Rust == TS" claim, add a Python test (gated on the real `suggest_config` symbol, mirroring `test_healer_default_e2e_native.py`) that loads the SAME `tests/parity/fixtures/suggest/*.json`, feeds each `input` through the Python native path (arrow `suggest`/`suggest_config`), and asserts the result equals `expected`. (Runs in the native CI lane; the fixtures are the shared cross-surface contract.)
- [ ] **Step 3: Run — PASS** (TS in CI; Python in the native lane).
- [ ] **Step 4: Commit.** `git commit -am "test: suggest-wasm kernel parity (TS) + Python cross-surface parity on shared fixtures"`

---

## Phase E — Surfaces (CLI / MCP / A2A)

### Task 10: CLI `--suggest` / `--heal` + free default hint

**Files:**
- Modify: `packages/typescript/goldenmatch/src/cli.ts`
- Reference: `packages/python/goldenmatch/goldenmatch/cli/dedupe.py` (`_emit_healer_surface`)
- Test: `packages/typescript/goldenmatch/tests/unit/cli-suggest.test.ts`

- [ ] **Step 1: Failing tests** — `--suggest` prints serialized suggestions; `--heal` prints the trail + healed note; a default run prints the one-line hint ONLY when the free trigger fires, and does **not** trigger a second dedupe for the hint (cost guarantee).
- [ ] **Step 2: Implement** mirroring Python: default hint reads the free trigger off the already-produced result; `--suggest`/`--heal` call `dedupe` with the option.
- [ ] **Step 3: Run — PASS.** **Step 4: Commit.**

### Task 11: MCP `review_config` tool + A2A `review_config` skill

**Files:**
- Modify: `packages/typescript/goldenmatch/src/node/mcp/server.ts` (+ tool count if asserted in a test)
- Modify: `packages/typescript/goldenmatch/src/core/agent/skills.ts` + `src/node/a2a/server.ts`
- Reference: the Python MCP/A2A `review_config` added earlier in this stack (`mcp/server.py`, `a2a/{server,skills}.py`)
- Test: `packages/typescript/goldenmatch/tests/unit/mcp-review-config.test.ts`, `.../a2a-review-config.test.ts`

- [ ] **Step 1: Failing tests** — the MCP server lists a `review_config` tool and dispatches it to `reviewConfig`; the A2A agent card advertises a `review_config` skill. The MCP test asserts `TOOLS.length` dynamically (no hardcoded count), so no count to bump — BUT update the stale doc-comment tool count at `server.ts:7` ("44 tools" → 45).
- [ ] **Step 2: Implement.** MCP: a Tool def + a `switch` case in `handleTool` (`server.ts:477`) + add to the composed `TOOLS` array — no hidden plumbing. A2A: add the SkillDef to **`AGENT_SKILLS` only** (`core/agent/skills.ts`) — `buildCardSkills` (`a2a/server.ts:167`, which unions `BASE_SKILLS` + `AGENT_SKILLS` + memory + identity, deduped by name) auto-surfaces it and `SKILLS_BY_ID` (`skills.ts:486`) auto-dispatches; do NOT also add it to the separate hardcoded `BASE_SKILLS` list (`a2a/server.ts:85`) or it duplicates. Leave legacy `suggest_config` untouched. Graceful-empty when the backend is null.
- [ ] **Step 3: Run — PASS.** **Step 4: Commit.**

---

## Phase F — CI + docs

### Task 12: CI parity lane + regen-in-sync guard

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1:** Add a `suggest-wasm` parity lane mirroring the autoconfig-wasm CI path: a path filter on `packages/rust/extensions/suggest-{core,wasm}/**` + `scripts/build_suggest_wasm.mjs` + `src/core/suggest*.ts`; a job that (a) rebuilds the wasm via `build_suggest_wasm.mjs`, (b) asserts `git diff --exit-code` on the committed `_wasm/suggestWasm*` + fixtures (regen-in-sync: a kernel change can't skip the regen), (c) runs the TS suggest parity + unit tests.
- [ ] **Step 2:** Add the Rust `cargo test -p goldenmatch-suggest-core --features arrow` to the rust job (it may already be covered by `--workspace`; suggest-core is a standalone workspace, so add an explicit step like the other standalone cores).
- [ ] **Step 3: Validate** `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`. **Step 4: Commit.**

### Task 13: Docs sweep

**Files:**
- Modify: `docs-site/goldenmatch/config-suggestions.mdx` (add a "TypeScript / WASM" section: `dedupe({suggest, heal})`, the `enableSuggestWasm()` opt-in = the `[native]` analog, graceful-empty, kill-switch/option).
- Modify: `packages/typescript/goldenmatch/README.md` + `packages/typescript/goldenmatch/CLAUDE.md` (the suggest-wasm build/regen gotcha).
- Modify: `packages/python/goldenmatch/CHANGELOG.md` is Python-only; add a TS changelog entry if the TS package has one, else note in the TS README.
- Modify: `llms.txt` (note the healer is now on the TS/WASM surface too).
- Create: `context-network/decisions/0027-healer-wasm-ts.md` + discovery link + `context-network/meta/updates.md` entry.
- Use the `rollout-docs-sweep` skill against `.claude/doc-surfaces.md`.

- [ ] **Step 1:** Apply the doc edits. **Step 2:** Run `python scripts/check_docs_consistency.py` (ignore the known Windows backslash orphan false-positive). **Step 3: Commit.**

---

## Done criteria

- `cargo test -p goldenmatch-suggest-core --features arrow` green (incl. `json_path_matches_arrow_path`); no-arrow build compiles. **Python side unchanged** (its tests untouched).
- `build_suggest_wasm.mjs` produces committed glue/bytes/fixtures; CI regen-in-sync guard green.
- TS: kernel golden-vector parity (TS == Rust == Python fixtures), column-signal parity (TS == Python), dedupe wiring tests (no-op parity + suggest/heal + kill-switch), all surfaces (CLI/MCP/A2A) green — **in CI** (not local).
- Graceful-empty verified: no registered backend → every TS surface returns `[]`/undefined, never throws.
- Docs swept; ADR 0027 added.

## Execution notes

- **Base branch:** off `main` once `suggest-core` (PR #1267) has landed, or off the kernel branch if started sooner. This work does NOT depend on the Python default-pipeline branch (#1275).
- **Local vs CI:** Rust `suggest-core`/`suggest-wasm` are small — `cargo test`/`cargo build --target wasm32...` are fine locally. The `build_suggest_wasm.mjs` step needs `wasm-pack` + `wasm32-unknown-unknown`. **All TS `vitest`/`tsup`/`tsc` runs go to CI** (local OOMs the box); targeted single-file vitest only if necessary.
- Phases A→B→C→D are strictly ordered (each depends on the prior). E and F depend on D. Within a phase, tasks are ordered.
