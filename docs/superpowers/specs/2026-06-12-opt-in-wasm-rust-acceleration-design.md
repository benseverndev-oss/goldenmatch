# Opt-in WASM acceleration for the pyo3-free Rust cores

**Date:** 2026-06-12
**Status:** Approved (design)
**Author:** Ben Severn (with Claude)

## Problem

The Golden Suite TypeScript packages (`goldenmatch`, `goldencheck`,
`goldenanalysis`) are **pure TypeScript**. Their `src/core/**` is edge-safe
(no `node:*`), zero-dependency, and runs in browsers, Workers, and edge
runtimes. That edge-safety is the value proposition of the TS core.

The Rust acceleration in the repo is **Python-only** today: the pyo3-free
`*-core` crates (`score-core`, `graph-core`, `analysis-core`,
`fingerprint-core`, `goldencheck-core`) back the `*-native` abi3 wheels and
the DataFusion FFI UDFs. The TS port reimplements the same algorithms by hand
in pure TS and holds a 4-decimal parity contract against Python.

We want the TS side to **optionally** reach the same Rust kernels via
WebAssembly, **without** giving up the zero-dependency pure-TS default or the
edge-safety guarantee. WASM is opt-in; pure-TS stays the default and the
fallback.

## Goals

- A reusable pattern for compiling a pyo3-free `*-core` crate to WASM and
  wiring it behind the existing **sync** TS APIs as an **opt-in** backend.
- Pure-TS stays the default and the fallback. Default users download and parse
  **zero** wasm bytes.
- Runs in **Node + browser + Workers/edge** (the full set of targets the
  pure-TS core already runs in).
- A parity gate (WASM output matches pure-TS, which matches Python) and a
  benchmark gate (WASM must measurably beat pure-TS before a core ships
  acceleration).
- Reference implementation on **score-core → goldenmatch TS**, then a
  measured rollout to the other cores.

## Non-goals

- Replacing the pure-TS implementations. They remain the reference and the
  fallback forever.
- Shipping WASM for a core where it does not measurably beat pure-TS. Two
  cores are parked by default (see Rollout).
- A shared cross-package WASM-runtime npm package up front. That is a later
  extraction, not an upfront commitment (the TS packages are not a real npm
  workspace; the cross-package `.vendor/` tarball dance is painful on Windows —
  see `packages/typescript/CLAUDE.md`).
- Distributed / Ray / GPU / Polars paths (already declared Python-only).

## Decisions (settled during brainstorming)

1. **Scope:** all pyo3-free cores are the end goal, reached via a measured
   rollout (not all at once).
2. **Targets:** edge + browser + Node (full). This forces an **async** opt-in
   init (browsers ban synchronous instantiation of modules >4 KB).
3. **API shape:** async `enableWasm()` instantiates once and registers a
   backend behind the **existing sync** scorer APIs. Pure-TS is the default and
   the fallback.
4. **Crate architecture:** a per-core `*-wasm` wrapper crate (wasm-bindgen)
   that path-depends on the same pyo3-free `*-core` crate the `*-native` crate
   wraps. One source of truth.
5. **Byte loading:** ship `.wasm` as a package asset; `enableWasm()` lazily
   dynamic-imports a universal loader (fs in Node, `fetch(new URL(...,
   import.meta.url))` in browser/Workers/bundlers); `enableWasm({ wasmBytes })`
   / `{ wasmUrl }` override. The glue is behind a dynamic import so default
   users pay zero bytes.
6. **Delivery:** pattern-first vertical slice on score-core, then roll out the
   rest gated on a per-core benchmark.

## Architecture

### Crate layout (reference slice)

New crate `packages/rust/extensions/score-wasm/` — its own workspace, exactly
like `score-core` and `native`:

```
score-core (pyo3-free, canonical scorers)
  ← native        (pyo3 abi3 wheel — Python)
  ← datafusion-udf (FFI ScalarUDFs — DuckDB/DataFusion)
  ← score-wasm    (wasm-bindgen — TypeScript)        ← NEW
```

- `Cargo.toml`: `[lib] crate-type = ["cdylib"]`; deps `wasm-bindgen` +
  path-dep `goldenmatch-score-core`; optional `console_error_panic_hook`
  (dev only). **No pyo3** — it does not compile to `wasm32-unknown-unknown`.
- `src/lib.rs`: thin `#[wasm_bindgen]` shims that delegate to `score-core`.
  All scoring logic stays in `score-core`, so WASM, the Python native wheel,
  and the FFI UDFs are byte-identical by construction.

The new divergence risks are bigger than just "WASM vs hand-rolled jaro"
(`goldenmatch/src/core/scorer.ts` reimplements Jaro/Jaro-Winkler/Indel by hand
and does **not** use rapidfuzz). Two specific traps the plan must resolve:

- **token_sort preprocessing + scaling.** `score-core::score_one(2, a, b)`
  returns the **raw `fuzz::ratio` on [0,1]** and `score-core::token_sort_string`
  only splits/sorts tokens — it does **not** lowercase or strip punctuation.
  The pure-TS `tokenSortRatio` **does** lowercase + strip non-alphanumerics
  before the Indel ratio, and the Python goldens depend on that
  (`"John SMITH"` / `"smith john"` → 1.0). `score-core` also deliberately keeps
  `score_one(id=2)` **unscaled** vs `token_sort_ratio`'s ×100 form (a pinned,
  load-bearing asymmetry). So a `score-wasm` shim that naively delegates
  `score_one(2, …)` will **fail parity** on token_sort. Resolution options for
  the plan: (a) replicate the TS/Python normalization in the Rust batch entry
  before the Indel ratio, or (b) **exclude token_sort from the WASM path in
  slice 1** and let it stay pure-TS — see "WASM-covered scorers" below.
- **codepoint vs UTF-16 code unit.** rapidfuzz operates on Unicode codepoints
  (`a.chars()`); the pure-TS impl indexes UTF-16 code units in places. The
  parity corpus must include non-BMP / combining-character cases.

### WASM-covered scorers (explicit)

`score-core` implements **only** `jaro_winkler` / `levenshtein` / `token_sort` /
`exact` (`score_one` ids 0–3). But `buildScoreMatrix` in `scorer.ts` also
dispatches `ensemble`, `dice`, `jaccard`, `soundex_match`,
`given_name_aliased_jw`, `name_freq_weighted_jw`, and `embedding` — none of
which exist in `score-core`. So the WASM matrix path can only accelerate the
scorers `score-core` covers; **every other scorer always routes to pure-TS,
even when WASM is enabled.** The backend swap in `buildScoreMatrix` must
therefore be **per-scorer**: route to the WASM backend only for a covered
scorer id, else fall through to the existing pure-TS branch. For slice 1, the
covered set is `jaro_winkler` / `levenshtein` / `exact` at minimum
(`jaro_winkler` is the dominant scorer in practice, so the win is still
demonstrated); `token_sort` is included only if option (a) above lands cleanly,
otherwise deferred. The bench MUST exercise a covered scorer (`jaro_winkler`).

### The batch boundary — the load-bearing perf decision

The JS↔WASM boundary costs per call (string encoding + crossing). Per the
`#688` rayon-park lesson and the performance-audit lesson
(`docs/superpowers/specs/2026-05-02-performance-audit-checklist.md`:
"always measure wall-clock with the workload of interest before designing"),
accelerating **per-pair** `scoreField` would likely be **slower** than
pure-TS because the boundary cost dwarfs a single Jaro-Winkler. So the WASM
surface is **batch-first**:

- `score-wasm` exports a batch entry, e.g.
  `score_matrix(values_joined: &str, sep: &str, scorer_id: u8) -> Box<[f64]>`
  — values arrive as one delimiter-joined string (one allocation across the
  boundary, split in Rust), the NxN (or upper-triangle) matrix is computed in
  Rust, and a flat `Float64Array` returns in one crossing. A single-pair
  `score_one(scorer_id, a, b) -> f64` also exists, used by the parity test and
  available for callers, but it is **not** the acceleration path.
- **Reuse the proven kernel shape:** the Python native crate already has an
  NxM cdist primitive with a self-cdist upper-triangle optimization
  (`score_field_matrix` in `packages/rust/extensions/native/src/score.rs`).
  The `score-wasm` batch entry should mirror that signature/optimization
  rather than inventing a new one.
- The backend swap happens at `buildScoreMatrix` / `scoreMatrix` in
  `scorer.ts` (per-block, NxN), **not** at per-pair `scoreField`. One boundary
  crossing per block, not O(n²).

### TypeScript wiring (goldenmatch)

New edge-safe module `src/core/wasm/`:

- `backend.ts` — `ScorerBackend` interface (`scoreMatrix(values, scorerId)`,
  `scoreOne(...)`), a module-level `activeBackend` (default `null` ⇒ pure-TS),
  `setScorerBackend(b | null)`. `scorer.ts`'s matrix builders consult
  `activeBackend` if set, else run pure-TS.
- `loader.ts` — the universal byte loader + instantiation. Edge-safe: uses the
  documented `await import("node:fs/promises" as string)` idiom so tsup does
  not statically resolve `node:*`. Env detection picks fs (Node) vs `fetch`
  (browser/Workers/bundler). Resolves the artifact via
  `new URL('./score_wasm_bg.wasm', import.meta.url)`.
- `index.ts` — public `enableWasm(opts?)` / `disableWasm()`:
  - `enableWasm()` is **async**. It dynamic-imports `./loader.js` (so default
    users never load it), detects env, loads bytes, `WebAssembly.instantiate`s
    once, wires the wasm-bindgen glue into a `ScorerBackend`, and registers it.
    Returns `true` on success.
  - On **any** failure (no artifact, fetch error, instantiate error) it leaves
    pure-TS active and returns `false`. `enableWasm({ require: true })`
    re-throws instead, for callers who must guarantee acceleration.
  - `enableWasm({ wasmBytes })` / `{ wasmUrl }` bypass env detection.
  - Idempotent: a second call is a no-op while a backend is active.
  - `disableWasm()` resets `activeBackend` to `null` (pure-TS) for test
    isolation — mirrors `setSyncEmbedder(null)`.

The compiled `.wasm` ships inside the package (e.g. `dist`-adjacent asset
emitted by tsup as a file asset, resolved at runtime by the loader). It is
**not** committed to git (see CI).

### Data flow

```
caller: await enableWasm()
        → dynamic import('./wasm/loader.js')
        → detect env → load bytes (fs | fetch | override)
        → WebAssembly.instantiate(bytes)
        → build ScorerBackend over the wasm exports
        → setScorerBackend(backend)

caller: dedupe(...) / scoreMatrix(...)            (unchanged, sync)
        → buildScoreMatrix(values, scorer)
        → activeBackend ? backend.scoreMatrix(values, id)   [one WASM crossing per block]
                        : pure-TS NxN loop
```

## Parity gate

`packages/typescript/goldenmatch/tests/parity/wasm-scorer.test.ts`:

- For a corpus of string pairs, assert the `enableWasm()`-backed `scoreMatrix`
  agrees with the pure-TS `scoreMatrix` to the existing **4-decimal**
  tolerance, and with the Python goldens. The existing scorer ground-truth
  corpus is the inline `CASES` array in
  `tests/parity/scorer-ground-truth.test.ts` (NOT a JSON file under
  `tests/parity/fixtures/`, which holds aggregation/config/pprl/resolve
  fixtures only). Reuse that case list (extract to a shared fixture in the
  plan if convenient), and extend it with non-BMP / combining-character cases
  per the codepoint-vs-code-unit risk above.
- **Skipped** when the wasm artifact is absent (opt-in, exactly like Python
  selecting the native path only under `GOLDENMATCH_*` / `GOLDENCHECK_NATIVE`).
  A dedicated CI lane builds the wasm and runs this test **un-skipped**.
- A small unit test asserts the **fallback**: with no artifact and no override,
  `enableWasm()` returns `false` and scoring still produces pure-TS results.

Rust side: rely on `score-core`'s existing unit tests for the scoring logic
(the `score-wasm` shim is trivial). Optionally a `wasm-bindgen-test` smoke in
the CI lane to prove the boundary marshals correctly.

## Benchmark gate (measure-first)

`packages/typescript/goldenmatch/scripts/bench_wasm_scorer.mjs`:

- 5-run **median wall** comparing pure-TS vs WASM `scoreMatrix` on a realistic
  block workload (NxN over a block of ~500–2000 strings, the shape that
  actually runs in `findFuzzyMatches`).
- Prints the ratio. A core **graduates to shipped acceleration only if WASM
  wins by a meaningful margin** on this workload. The number is recorded in the
  per-core rollout spec.
- This is the gate that honors `feedback_verify_perf_not_just_ship` and the
  performance-audit lesson: measure the wall on the real shape before
  declaring a win — do not ship on a static "Rust is faster" assumption.

## CI

New path-filtered job in `.github/workflows/ci.yml`:

- Triggers (via a new `changes` filter entry, per the post-2026-05-06
  `dorny/paths-filter@v3` convention in the root CLAUDE.md):
  `packages/rust/extensions/score-wasm/**`,
  `packages/rust/extensions/score-core/**`,
  `packages/typescript/goldenmatch/src/core/wasm/**`, and the workflow file
  itself.
- Steps: install Rust + `wasm32-unknown-unknown` target + `wasm-bindgen-cli`
  (or `wasm-pack`) → build `score-wasm` → emit `score_wasm_bg.wasm` + glue →
  copy the artifact into the TS package → `pnpm --filter goldenmatch` run the
  wasm parity test (un-skipped) + the bench.
- A new job means adding the `changes` filter entry **and** the `if:
  needs.changes.outputs.<area> == 'true'` gate (root CLAUDE.md rule).
- The `.wasm` is **not committed** — built in CI, and bundled into the npm
  tarball by `publish-goldenmatch-js.yml` at publish time. This mirrors the
  native pattern: built in CI, pure path is the default + fallback. Local
  opt-in testing requires running `scripts/build_wasm.sh` first (documented;
  devs without the Rust/wasm toolchain simply get the skipped parity test and
  the pure-TS default).

## Local build script

`packages/rust/extensions/score-wasm/build_wasm.sh` (or a repo `scripts/`
entry): builds the crate for `wasm32-unknown-unknown`, runs `wasm-bindgen`,
and copies `score_wasm_bg.wasm` + the JS glue into the goldenmatch TS package's
wasm asset location. Uses the repo's standard Rust bash preamble
(`PATH`/`RUSTUP_HOME`/`CARGO_HOME`, per `packages/rust/extensions/CLAUDE.md`).

## Rollout (pattern-first, measured)

| Slice | Core → TS consumer | Status |
|---|---|---|
| 1 (this spec) | `score-core` → `goldenmatch/scorer.ts` | reference implementation, full pattern |
| 2 | `graph-core` → `goldenmatch/cluster.ts` (batch `connectedComponents` / `dedupPairs`) | own spec, **bench-gated** |
| 3 | `analysis-core` → `goldenanalysis/aggregate.ts` (batch `histogram` / `quantile`) | own spec, **bench-gated** |
| parked | `fingerprint-core` → `record-fingerprint.ts` | Web Crypto SHA-256 already native **and async**; value is canonicalization, not the hash. Revisit only with a bench showing a win. |
| parked | `goldencheck-core` → `goldencheck/*` | TS port mirrors the **sampled, already-vectorized** scan path, explicitly "not a native target". Revisit only with a bench showing a win. |

Each follow-on slice reuses the slice-1 artifacts: the `*-wasm` crate
template, the universal loader, the parity + bench gate shape, and the CI lane
template. The loader stays in goldenmatch TS for slice 1; if slice 2's
duplication actually hurts, extract a shared TS wasm-runtime then.

## Error handling

- `enableWasm()` never throws by default — it returns `false` and leaves
  pure-TS active. `{ require: true }` opts into hard failure.
- A malformed / wrong-arch / truncated artifact surfaces as an instantiate
  error caught by `enableWasm()` ⇒ pure-TS fallback.
- The backend is process-global (module-level singleton), matching the
  existing `setSyncEmbedder` pattern. `disableWasm()` guarantees test
  isolation.

## Testing summary

- **Rust:** `score-core` unit tests (logic) + optional `wasm-bindgen-test`
  boundary smoke (CI lane).
- **TS parity:** `wasm-scorer.test.ts` — WASM ≈ pure-TS ≈ Python goldens, 4dp;
  skipped without the artifact, un-skipped in the CI lane.
- **TS fallback:** `enableWasm()` returns `false` and scoring stays correct
  when no artifact / no override is available.
- **Bench:** `bench_wasm_scorer.mjs` — 5-run median wall, the graduation gate.

## Risks and open questions

- **Boundary overhead could still dominate** even at the matrix level for
  small blocks. The bench must use realistic block sizes; if WASM only wins
  above some N, the swap can be N-gated (fall back to pure-TS for tiny
  blocks). Decided empirically from the bench, not assumed.
- **tsup wasm asset emission** across all five target environments
  (Node ESM, browser, Workers, bundler) is the fiddliest part — the universal
  loader plus `new URL(..., import.meta.url)` is the portable approach, but
  exact tsup config (file-loader vs copy-asset) is an implementation detail to
  validate in the plan.
- **wasm-bindgen target choice** (`bundler` vs `web` vs hand-written glue over
  raw `WebAssembly.instantiate`): no single wasm-bindgen `--target` covers
  Node + browser + Workers + bundlers, so the loader owns environment
  detection and feeds bytes to a target-agnostic init. Validate in the plan.
- **token_sort and string-encoding parity** are the two concrete divergence
  traps — both detailed under Architecture ("token_sort preprocessing +
  scaling" and "codepoint vs UTF-16 code unit"). The plan must resolve
  token_sort (replicate normalization in Rust vs defer to pure-TS for slice 1)
  and seed the parity corpus with non-BMP / combining-character cases.
