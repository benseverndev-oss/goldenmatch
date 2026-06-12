# Opt-in WASM acceleration — Slice 2: analysis-core → goldenanalysis, with a shared WASM runtime

**Date:** 2026-06-12
**Status:** Draft (design)
**Author:** Ben Severn (with Claude)
**Parent design:** `2026-06-12-opt-in-wasm-rust-acceleration-design.md` (the rollout).
**Builds on:** `#878` (slice 1, score-core → goldenmatch, MERGED).

## Problem

`#878` shipped opt-in WASM for goldenmatch's scorer (score-core → `scoreMatrix`).
The rollout table lists the next bench-gated slices; **analysis-core →
goldenanalysis** (`histogram` / `quantile` in `aggregate.ts`) is chosen first
because it has the strongest measured win profile and forces the
shared-runtime extraction the rollout deferred.

**Why analysis-core wins where graph-core wouldn't (measure-first):** `histogram`
/ `quantile` take a numeric array — it marshals across the JS↔WASM boundary as a
**zero-copy `Float64Array`** (no per-element string encoding), and the compute is
real (`quantile` *sorts*; Rust's `sort_by(total_cmp)` crushes JS
`Array.sort((a,b)=>a-b)`, which boxes every element). This is the same shape that
gave the Python `analysis-core` native path a **measured 5.8–9.9x** (incl. Arrow
conversion). By contrast graph-core's `connected_components` marshals tuples and
does pointer-chasing union-find that is one O(N) step among several in
`buildClusters` — boundary-bound, a likely park. Bench still decides, but the a
priori case for analysis-core is strong.

## Decisions (settled during brainstorming)

1. **analysis-core first** (over graph-core) — strongest win profile + measured
   Python precedent.
2. **Fuller shared runtime.** Extract `resolveWasmBytes` + `LoadOptions` **and** a
   generic `enableWasmBackend<B>(...)` helper + a backend-singleton helper into a
   new workspace package; **refactor both goldenmatch (#878) and goldenanalysis
   onto it.** (Alternatives considered: minimal `resolveWasmBytes`-only extract;
   duplicate-and-don't-touch-#878. User chose fuller for cross-core DRY.)
3. **Covered ops:** `histogram` + `quantile` only (the two analysis-core ops).
   Everything else in `aggregate.ts` stays pure-TS.
4. **Bench is the graduation gate** — ship acceleration only if WASM measurably
   beats pure-TS on a realistic large-array workload (expected, given the Python
   5.8–9.9x).

## Architecture

### New package: `goldenmatch-wasm-runtime` (workspace package)

`packages/typescript/goldenmatch-wasm-runtime/` — edge-safe, zero-dependency,
the genuinely-shared WASM plumbing. The TS packages are now a **real pnpm
workspace** (post-fold; `workspace:^`), so cross-package deps resolve cleanly
(the old `.vendor` tarball pain is retired — see `packages/typescript/CLAUDE.md`).

Exports:
- `LoadOptions` (`{ wasmBytes?, wasmUrl? }`).
- `resolveWasmBytes(opts: LoadOptions, fallbackUrl: URL): Promise<Uint8Array>` —
  the env-detection + fs(Node)/fetch(browser/Workers) byte loader, using the
  documented edge-safe `await import("node:fs/promises" as string)` idiom. **The
  caller passes `fallbackUrl`** because `new URL('./artifacts/X.wasm',
  import.meta.url)` MUST be evaluated in the consuming module (so `import.meta.url`
  resolves to that package's own `dist`), not inside the shared package.
- `enableWasmBackend<B>(opts, instantiate, register): Promise<boolean>` — the
  generic opt-in skeleton: idempotency guard, lazy work, try → `instantiate(bytes)`
  → `register(backend)` → `true`; catch → `require?` rethrow : `console.debug` +
  `false`. `instantiate` and `register` are the per-domain closures (they own the
  glue import + the backend singleton). The `_enabled` guard lives **per registry**
  (returned by the singleton helper) so goldenmatch and goldenanalysis are
  independent.
- `createBackendRegistry<B>()` → `{ get(): B | null; set(b: B | null): void }`
  plus an `enabled` flag, the module-singleton pattern `#878`'s `backend.ts` uses
  (mirrors `setSyncEmbedder(null)`), made generic.

### goldenmatch refactor onto the shared runtime (#878)

- `src/core/wasm/backend.ts` — keep `ScorerBackend`, `SCORER_ID`,
  `WASM_COVERED_SCORERS`; replace the hand-rolled `_backend`/`set`/`get` with
  `createBackendRegistry<ScorerBackend>()`.
- `src/core/wasm/loader.ts` — `resolveWasmBytes` body becomes a call to the shared
  `resolveWasmBytes(opts, new URL('./artifacts/score_wasm_bg.wasm', import.meta.url))`.
  `instantiateBackend` (glue import + `scoreMatrix` adapter) stays here.
- `src/core/wasm/index.ts` — `enableWasm` delegates to shared `enableWasmBackend`
  (passing `instantiateBackend` + the registry's `set`). `disableWasm` unchanged
  in behavior.
- **#878's existing `wasm-backend` / `wasm-fallback` / `wasm-scorer` tests are the
  regression gate for this refactor** — they must stay green unchanged (the public
  `enableWasm`/`disableWasm`/`scoreMatrix` surface is byte-identical).
- Add `goldenmatch-wasm-runtime` as a `workspace:^` dep.

### New crate: `analysis-wasm`

`packages/rust/extensions/analysis-wasm/` — own workspace, mirroring
`score-wasm`: `[lib] crate-type=["cdylib","rlib"]`, path-dep on
`goldenmatch-analysis-core`, `wasm-bindgen` gated to `cfg(target_arch="wasm32")`.

`src/lib.rs` — host-testable `*_impl` + wasm shims delegating to analysis-core:
- `histogram(values: &[f64], bins: u32) -> Vec<f64>` — returns the histogram
  **flattened** as `[edge0, count0, edge1, count1, ...]` (wasm-bindgen marshals
  `Vec<f64>` ↔ `Float64Array`; counts are exact integers well within f64). The TS
  side un-flattens to `[number, number][]`.
- `quantile(values: &[f64], q: f64) -> f64`.
Both delegate to `analysis-core::histogram`/`quantile`, which are already
**line-for-line ports of `aggregate.ts`** (same `lo + i*width` edges, same
truncate/`floor` binning with right-edge-inclusive clamp, same linear-interp
quantile via `total_cmp` sort) — so parity is structural.

### goldenanalysis wiring

New `packages/typescript/goldenanalysis/src/core/wasm/`:
- `backend.ts` — `AnalysisBackend` interface (`histogram(values: Float64Array,
  bins): Array<[number, number]>`, `quantile(values: Float64Array, q): number`) +
  `createBackendRegistry<AnalysisBackend>()` from the shared runtime.
- `loader.ts` — `instantiateBackend(bytes)`: imports the analysis-wasm glue,
  adapts to `AnalysisBackend` (calls `histogram`/`quantile`, un-flattens the
  histogram pairs). Byte loading via shared `resolveWasmBytes(opts, new
  URL('./artifacts/analysis_wasm_bg.wasm', import.meta.url))`.
- `index.ts` — `enableAnalysisWasm(opts?)` / `disableAnalysisWasm()` via shared
  `enableWasmBackend`. Exported from the goldenanalysis barrel.
- `artifacts/.gitignore` — ignore the built `.wasm` + glue (keep dir tracked).

`aggregate.ts` — `histogram`/`quantile` consult the registry: filter nulls to a
`Float64Array` (filtering stays TS-side, the backend never sees nulls — mirrors
`scoreMatrix`'s null masking), and if a backend is registered, delegate; else run
pure-TS. Edge cases (empty, `bins < 1`, all-equal, single) are cheap and handled
**before** the boundary in pure-TS (so the WASM call only ever sees the
general-case path) — keeps the boundary contract simple and avoids marshaling
for trivial inputs.

### Data flow

```
caller: await enableAnalysisWasm()
        → shared enableWasmBackend(opts, instantiate, registry.set)
        → resolveWasmBytes → instantiate(analysis-wasm glue) → registry.set(backend)

caller: histogram(values, bins)            (unchanged signature, sync)
        → filter nulls; handle empty/all-equal/single in pure-TS
        → registry.get() ? backend.histogram(f64arr, bins)   [one Float64Array crossing]
                          : pure-TS binning loop
```

## Build script + tsup wiring

- `packages/rust/extensions/analysis-wasm/build_wasm.sh` — mirror score-wasm:
  build `wasm32-unknown-unknown`, `wasm-bindgen --target web`, copy
  `analysis_wasm_bg.wasm` + glue into goldenanalysis's
  `src/core/wasm/artifacts/`.
- goldenanalysis `tsup.config.ts` — add the `.wasm` copy (mirror #878's
  `loader: {".wasm":"copy"}` + an `onSuccess` `copy_wasm_artifact.mjs`), so the
  loader's `import.meta.url` artifact path resolves in `dist`.
- The `.wasm` is **not committed** (built in CI, bundled at publish), exactly
  like #878.

## Parity gate

`packages/typescript/goldenanalysis/tests/parity/wasm-aggregate.test.ts`:
- Corpus of `(values, bins)` and `(values, q)` cases — random finite arrays
  (varied size + range), plus edge cases that exercise the general path
  (multi-bin, repeated values, negative + positive, large N). Goldens from a
  Python emitter generating `analysis-core`-equivalent results (or directly from
  `aggregate.py`/rapidfuzz-free numpy) — but since pure-TS already == Python ==
  analysis-core by construction, the binding assertion is **WASM ≈ pure-TS** (4dp,
  expect exact) over the corpus; a few anchors are cross-checked against Python.
- **NaN/Infinity out of scope** (the Rust contract assumes finite; aggregate.ts
  filters only null/undefined). Corpus stays finite.
- **Skip-guarded** on the artifact (like #878's `wasm-scorer.test.ts`): skips
  without the built `.wasm`; the CI lane builds it and runs un-skipped.
- A fallback unit test: `enableAnalysisWasm()` with empty bytes returns `false`,
  histogram/quantile still produce pure-TS results.

## Bench gate (measure-first graduation)

`packages/typescript/goldenanalysis/scripts/bench_wasm_aggregate.mjs`:
- 5-run median wall, pure-TS vs WASM `histogram` (e.g. 1M values, 256 bins) and
  `quantile` (1M values) — the realistic large-column shape.
- Prints the ratio. Graduates to shipped acceleration only on a meaningful win
  (expected 5–9x per the Python precedent). Honors
  `feedback_verify_perf_not_just_ship`: the bench is informational
  (`continue-on-error`) until validated on a real CI run, then can be flipped to
  gating (mirrors the open item-4 work on #878's lane).

## CI

New `analysis_wasm` path-filtered job in `.github/workflows/ci.yml` (mirror the
`wasm_score` lane):
- Filter on `packages/rust/extensions/analysis-wasm/**`,
  `packages/rust/extensions/analysis-core/**`,
  `packages/typescript/goldenanalysis/src/core/wasm/**`,
  `packages/typescript/goldenmatch-wasm-runtime/**`, and the workflow file.
- Steps: Rust + `wasm32` target + wasm-bindgen-cli → `build_wasm.sh` →
  `pnpm install` (workspace) + build → run `wasm-aggregate.test.ts` un-skipped +
  the bench.
- New job ⇒ add the `changes` filter entry AND the `if:` gate (root CLAUDE.md).
- The shared-runtime refactor of goldenmatch means **the existing `wasm_score`
  lane + the normal `typescript` lane (which runs goldenmatch's wasm unit tests)
  are the regression gate** for the #878 refactor — both must stay green.

## Risks

- **Refactoring just-merged #878.** Mitigated: the public surface
  (`enableWasm`/`disableWasm`/`scoreMatrix`) is unchanged; #878's `wasm-backend`,
  `wasm-fallback`, and `wasm-scorer` tests are the binding regression gate. If the
  refactor can't stay byte-identical, fall back to the "minimal extract" scope
  (resolveWasmBytes-only) for goldenmatch and keep the generic helper goldenanalysis-only.
- **`import.meta.url` resolution.** The artifact URL + glue import MUST stay in
  each consuming package's own module — encoded in the design (caller passes
  `fallbackUrl`; `instantiate` lives per-package). A shared-package `import.meta.url`
  would resolve to the wrong `dist`.
- **pnpm workspace wiring on this box.** Windows/exFAT symlink friction is retired
  by the pnpm workspace (`workspace:^`), but the new package must be globbed by
  `pnpm-workspace.yaml` (it already globs `packages/typescript/*`) and added as a
  dep in both consumers. CI `pnpm install --frozen-lockfile` requires the lockfile
  updated in the same PR.
- **Float parity.** Structural (analysis-core == aggregate.ts arithmetic), but the
  histogram-flatten round-trip (`Vec<f64>` of interleaved edges+counts) must
  un-flatten correctly; counts are exact in f64 (well under 2^53). Bench/parity
  corpus avoids NaN/Inf.
- **goldenanalysis is pre-acceleration on the TS side.** This is its first wasm
  lane; the loader/tsup/artifact pattern is copied wholesale from #878 (proven).

## Non-goals

- graph-core / fingerprint-core / goldencheck-core slices (separate, each
  bench-gated; graph-core likely parks per the measure-first analysis).
- Covering `aggregate.ts` ops beyond `histogram`/`quantile`.
- Flipping #878's own bench off `continue-on-error` (the open item-4; can ride
  along or stay separate).

## Done-when (slice acceptance)

- `goldenmatch-wasm-runtime` package builds; goldenmatch refactored onto it with
  #878's wasm tests green unchanged.
- `analysis-wasm` crate builds for `wasm32`; host `cargo test`/clippy/fmt clean.
- `enableAnalysisWasm()` registers a backend; default + failure paths stay pure-TS;
  `disableAnalysisWasm()` resets.
- `analysis_wasm` CI lane: builds the artifact, `wasm-aggregate.test.ts` runs
  un-skipped and passes (WASM ≈ pure-TS, 4dp), bench prints a speedup.
- Default-checkout build + full goldenanalysis vitest unaffected (parity test
  skips; pure-TS default).
- PR off `feat/analysis-wasm-slice` (base main); merge-on-green per
  `feedback_branch_merge_sop`.
