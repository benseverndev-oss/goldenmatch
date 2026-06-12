# Opt-in WASM Slice 2 — analysis-core → goldenanalysis + shared runtime — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Ship an opt-in WASM backend for goldenanalysis's `histogram`/`quantile` wrapping `analysis-core`, and extract a shared `goldenmatch-wasm-runtime` package that both goldenmatch (#878) and goldenanalysis use.

**Architecture:** New zero-dep workspace package `goldenmatch-wasm-runtime` (resolveWasmBytes + LoadOptions + generic enableWasmBackend + createBackendRegistry). #878's goldenmatch wasm code refactors onto it (public surface byte-identical — #878 tests are the gate). New `analysis-wasm` wasm-bindgen crate over `analysis-core`. goldenanalysis gets `enableAnalysisWasm`; `aggregate.ts` swaps to the backend at the array boundary (one `Float64Array` crossing). Parity (WASM≈pure-TS, 4dp) + bench (graduation gate) in a new `analysis_wasm` CI lane.

**Tech Stack:** Rust + wasm-bindgen + `wasm32-unknown-unknown`; TypeScript (tsup/vitest strict); pnpm workspace (`workspace:^`); GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-12-analysis-wasm-acceleration-design.md`

---

## Pre-flight (read once)

- **Worktree:** `.worktrees/analysis-wasm`, branch `feat/analysis-wasm-slice`, off `origin/main` (includes #878).
- **No local node_modules** (CI validates TS, per `feedback_box_memory_oom_ts`). TS test/typecheck steps are authoritative in CI; push and confirm. Rust host `cargo test` for analysis-wasm IS runnable locally (Rust preamble below).
- **Rust preamble** (prepend to every cargo/rustup): `export CARGO_HOME="${CARGO_HOME:-C:/Users/bsevern/.cargo}" RUSTUP_HOME="${RUSTUP_HOME:-C:/Users/bsevern/.rustup}"; export PATH="$CARGO_HOME/bin:$PATH"`.
- **`.wasm` not committed** (built in CI). Artifacts dirs keep a `.gitignore`.
- **#878 templates to mirror (read at execution time):** `packages/rust/extensions/score-wasm/{Cargo.toml,build_wasm.sh,src/lib.rs}`, `packages/typescript/goldenmatch/{tsup.config.ts,scripts/copy_wasm_artifact.mjs,src/core/wasm/*}`, and the `wasm_score` job in `.github/workflows/ci.yml`.
- **Refactor safety:** goldenmatch's public `enableWasm`/`disableWasm`/`scoreMatrix`/`LoadOptions`/`EnableWasmOptions` shapes stay identical; `tests/unit/wasm-backend.test.ts`, `tests/unit/wasm-fallback.test.ts`, `tests/parity/wasm-scorer.test.ts` are the binding regression gate (CI).

---

## Task 1: `goldenmatch-wasm-runtime` shared package

**Files (all new under `packages/typescript/goldenmatch-wasm-runtime/`):**
`package.json`, `tsconfig.json`, `tsup.config.ts`, `src/index.ts`, `tests/runtime.test.ts`.

- [ ] **Step 1: `package.json`** (mirror `packages/typescript/goldencheck-types/package.json` shape; name `goldenmatch-wasm-runtime`, version `0.1.0`, `"type":"module"`, `main`/`module`/`types` → `dist/index.js`/`dist/index.d.ts`, `exports` map, scripts `build: tsup`, `test: vitest run`, `typecheck: tsc --noEmit`; devDeps `tsup`/`typescript`/`vitest` matching the versions in `goldenmatch/package.json`). Zero runtime deps.

- [ ] **Step 2: `tsconfig.json` + `tsup.config.ts`** — copy goldenmatch's (strict: `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`; module `NodeNext`/`Bundler`). tsup: single entry `src/index.ts`, `format: ["esm"]`, `dts: true`, `clean: true`.

- [ ] **Step 3: write `src/index.ts`**
```ts
/**
 * goldenmatch-wasm-runtime — shared opt-in WASM plumbing for the Golden Suite
 * TS packages. Edge-safe (the only node:* touch is the guarded dynamic
 * `import("node:fs/promises" as string)` idiom). Domain-agnostic: the byte
 * loader, the enable skeleton, and a backend singleton. Each consumer owns its
 * artifact URL (computed in ITS OWN module so import.meta.url resolves to its
 * dist), its wasm-bindgen glue import, and its backend interface.
 */

export interface LoadOptions {
  readonly wasmBytes?: Uint8Array;
  readonly wasmUrl?: string | URL;
}

/**
 * Resolve raw wasm bytes for the current environment. `fallbackUrl` is the
 * consumer's `new URL('./artifacts/<name>_bg.wasm', import.meta.url)` — it MUST
 * be evaluated in the consumer's module, never here.
 */
export async function resolveWasmBytes(
  opts: LoadOptions,
  fallbackUrl: URL,
): Promise<Uint8Array> {
  if (opts.wasmBytes !== undefined) {
    if (opts.wasmBytes.byteLength === 0) throw new Error("empty wasmBytes");
    return opts.wasmBytes;
  }
  const url = opts.wasmUrl ?? fallbackUrl;
  const isNode =
    typeof process !== "undefined" &&
    process.versions?.node !== undefined &&
    (url instanceof URL ? url.protocol === "file:" : String(url).startsWith("file:"));
  if (isNode) {
    const fs = await import("node:fs/promises" as string);
    const buf = await fs.readFile(url as URL);
    return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  const resp = await fetch(url as URL);
  if (!resp.ok) throw new Error(`fetch wasm failed: ${resp.status}`);
  return new Uint8Array(await resp.arrayBuffer());
}

/** A module-singleton backend registry (mirrors setSyncEmbedder(null)). */
export interface BackendRegistry<B> {
  get(): B | null;
  set(b: B | null): void;
}
export function createBackendRegistry<B>(): BackendRegistry<B> {
  let backend: B | null = null;
  return { get: () => backend, set: (b) => { backend = b; } };
}

export interface EnableOptions extends LoadOptions {
  /** Throw instead of falling back to pure-TS when the module can't load. */
  readonly require?: boolean;
}

/**
 * Generic opt-in enable skeleton. `instantiate` does the per-domain glue import
 * + adapt; `register` installs the backend (usually a registry's `set`). Returns
 * true on success; on failure returns false (pure-TS stays) unless require:true.
 * The `enabled` guard is per-call-site (pass your module's flag via the closure
 * — see the goldenmatch/goldenanalysis index.ts wrappers).
 */
export async function enableWasmBackend<B>(
  opts: EnableOptions,
  instantiate: (bytes: Uint8Array) => Promise<B>,
  register: (b: B) => void,
  fallbackUrl: URL,
): Promise<boolean> {
  try {
    const bytes = await resolveWasmBytes(opts, fallbackUrl);
    const backend = await instantiate(bytes);
    register(backend);
    return true;
  } catch (err) {
    if (opts.require) throw err;
    console.debug("[goldenmatch-wasm-runtime] enable fell back to pure-TS:", err);
    return false;
  }
}
```
> Note: `enableWasmBackend` does NOT own the `_enabled` idempotency flag (that stays in each consumer's `index.ts`, which already guards `if (_enabled) return true` before calling — keeps the lazy-import-of-the-loader behavior per-package). The shared helper assumes the caller already decided to (re)load.

- [ ] **Step 4: `tests/runtime.test.ts`** — unit tests with no artifact:
  - `resolveWasmBytes({wasmBytes:new Uint8Array(0)}, url)` throws "empty wasmBytes".
  - `createBackendRegistry<number>()` get/set round-trips + defaults null.
  - `enableWasmBackend({wasmBytes:new Uint8Array(0)}, ...)` returns false (no throw); with `{require:true}` rejects.

- [ ] **Step 5: commit**
```bash
git add -f docs/superpowers/plans/2026-06-12-analysis-wasm-slice.md
git add packages/typescript/goldenmatch-wasm-runtime
git commit -m "feat(ts): goldenmatch-wasm-runtime shared package (loader + enable skeleton + registry)"
```

---

## Task 2: refactor goldenmatch (#878) onto the shared runtime

**Files:** modify `packages/typescript/goldenmatch/src/core/wasm/{backend,loader,index}.ts` + `package.json`.

- [ ] **Step 1: add the dep** — in `goldenmatch/package.json` dependencies: `"goldenmatch-wasm-runtime": "workspace:^"`.

- [ ] **Step 2: `backend.ts`** — keep `SCORER_ID`, `WASM_COVERED_SCORERS`, `ScorerBackend`; replace the `_backend`/`setScorerBackend`/`getScorerBackend` trio with the shared registry, preserving the function names:
```ts
import { createBackendRegistry } from "goldenmatch-wasm-runtime";
const _registry = createBackendRegistry<ScorerBackend>();
export function setScorerBackend(b: ScorerBackend | null): void { _registry.set(b); }
export function getScorerBackend(): ScorerBackend | null { return _registry.get(); }
```

- [ ] **Step 3: `loader.ts`** — replace the local `resolveWasmBytes` body with a re-export/wrapper over the shared one, keeping the same exported name + signature `resolveWasmBytes(opts: LoadOptions)`:
```ts
import { resolveWasmBytes as sharedResolve, type LoadOptions } from "goldenmatch-wasm-runtime";
export type { LoadOptions };
export function resolveWasmBytes(opts: LoadOptions): Promise<Uint8Array> {
  return sharedResolve(opts, new URL("./artifacts/score_wasm_bg.wasm", import.meta.url));
}
```
Keep `instantiateBackend` exactly as-is (glue import + scoreMatrix adapter).

- [ ] **Step 4: `index.ts`** — re-point `enableWasm` at the shared skeleton, KEEPING the `_enabled` guard + the `EnableWasmOptions`/exports:
```ts
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setScorerBackend } from "./backend.js";
// EnableWasmOptions = EnableOptions (re-export shape unchanged)
export interface EnableWasmOptions extends EnableOptions {}
let _enabled = false;
export async function enableWasm(opts: EnableWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  const { instantiateBackend, resolveWasmBytes } = await import("./loader.js"); // keep lazy
  void resolveWasmBytes; // resolution now happens inside enableWasmBackend via fallbackUrl
  const ok = await enableWasmBackend(
    opts,
    instantiateBackend,
    setScorerBackend,
    new URL("./artifacts/score_wasm_bg.wasm", import.meta.url),
  );
  if (ok) _enabled = true;
  return ok;
}
export function disableWasm(): void { setScorerBackend(null); _enabled = false; }
```
> The lazy `import("./loader.js")` is preserved so default users still never load the glue. `enableWasmBackend` calls `resolveWasmBytes(opts, fallbackUrl)` internally; we pass goldenmatch's artifact URL. (If keeping the lazy-loader import while also passing instantiate is awkward, the simpler equivalent: keep `index.ts`'s try/catch shape but call shared `resolveWasmBytes`; either way the #878 tests are the gate.)

- [ ] **Step 5: typecheck + the #878 wasm tests (CI; locally if installed)**
Run: `npx tsc --noEmit && npx vitest run tests/unit/wasm-backend.test.ts tests/unit/wasm-fallback.test.ts`
Expected: clean + pass (public surface unchanged). In CI: the `typescript` + `wasm_score` lanes are the gate.
> **Fallback escape hatch (per spec):** if the `index.ts` refactor can't stay byte-identical against the #878 tests, revert `index.ts` to its original local skeleton and keep only the `backend.ts` registry + `loader.ts` resolveWasmBytes extraction — goldenanalysis still gets the full shared helper. Note the deviation in the commit.

- [ ] **Step 6: commit**
```bash
git add packages/typescript/goldenmatch/src/core/wasm packages/typescript/goldenmatch/package.json
git commit -m "refactor(ts): goldenmatch wasm onto shared goldenmatch-wasm-runtime (surface unchanged)"
```

---

## Task 3: `analysis-wasm` crate

**Files (new under `packages/rust/extensions/analysis-wasm/`):** `Cargo.toml`, `src/lib.rs`, `.gitignore`, `build_wasm.sh`.

- [ ] **Step 1: `Cargo.toml`** — mirror `score-wasm/Cargo.toml`: `[workspace]`, name `goldenmatch-analysis-wasm`, `[lib] name="goldenmatch_analysis_wasm" crate-type=["cdylib","rlib"]`, dep `goldenmatch-analysis-core = { path = "../analysis-core" }`, `[target.'cfg(target_arch="wasm32")'.dependencies] wasm-bindgen="0.2"`. (Confirm the analysis-core crate's package name via `packages/rust/extensions/analysis-core/Cargo.toml` and use it verbatim.)

- [ ] **Step 2: write `src/lib.rs`** (host-testable impl + wasm shim)
```rust
//! wasm-bindgen wrapper over `goldenmatch-analysis-core`. TS analogue of the
//! native crate: thin shims delegating to analysis-core, so histogram/quantile
//! are byte-identical across Python, the native wheel, and TS WASM.
//!
//! Boundary: numeric arrays cross as Float64Array (zero-copy contiguous), once
//! per call. histogram is returned FLATTENED as [edge0,count0,edge1,count1,...].

use goldenmatch_analysis_core::{histogram, quantile};

/// Flatten analysis-core's Vec<(f64,i64)> histogram to [edge,count,...]. `bins`
/// is i32 (a JS number may be 0/negative; keep signed so analysis-core's
/// `bins < 1 => []` guard fires instead of wrapping).
pub fn histogram_flat_impl(values: &[f64], bins: i32) -> Vec<f64> {
    let pairs = histogram(values, bins as i64);
    let mut out = Vec::with_capacity(pairs.len() * 2);
    for (edge, count) in pairs {
        out.push(edge);
        out.push(count as f64);
    }
    out
}

pub fn quantile_impl(values: &[f64], q: f64) -> f64 {
    quantile(values, q)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn histogram_flat_matches_pairs() {
        // 0,1,2,3 into 2 bins -> edges 0 and 2, counts 2 and 2.
        let f = histogram_flat_impl(&[0.0, 1.0, 2.0, 3.0], 2);
        assert_eq!(f, vec![0.0, 2.0, 2.0, 2.0]);
    }
    #[test]
    fn histogram_bins_lt_1_is_empty() {
        assert!(histogram_flat_impl(&[1.0, 2.0], 0).is_empty());
        assert!(histogram_flat_impl(&[1.0, 2.0], -3).is_empty());
    }
    #[test]
    fn quantile_median_interpolates() {
        assert_eq!(quantile_impl(&[1.0, 2.0, 3.0, 4.0], 0.5), 2.5);
    }
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::{histogram_flat_impl, quantile_impl};
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn histogram(values: &[f64], bins: i32) -> Vec<f64> {
        histogram_flat_impl(values, bins)
    }
    #[wasm_bindgen]
    pub fn quantile(values: &[f64], q: f64) -> f64 {
        quantile_impl(values, q)
    }
}
```

- [ ] **Step 3: run host tests** (Rust preamble): `cd packages/rust/extensions/analysis-wasm && cargo test`. Expected `3 passed`. Then `cargo build && cargo clippy -- -D warnings && cargo fmt --check`.

- [ ] **Step 4: `.gitignore`** (`/target`, `/pkg`, `Cargo.lock`) — but NOTE: `build_wasm.sh` reads the pinned wasm-bindgen from `Cargo.lock`, so **commit `Cargo.lock`** (do NOT ignore it) — same as score-wasm. Verify score-wasm's `.gitignore` and mirror it (it ignores `/target` `/pkg` but commits `Cargo.lock`).

- [ ] **Step 5: `build_wasm.sh`** — copy `score-wasm/build_wasm.sh`, changing: `OUT_DIR` → `.../typescript/goldenanalysis/src/core/wasm/artifacts`, the wasm filename → `goldenmatch_analysis_wasm.wasm`, `--out-name analysis_wasm`. `chmod +x`.

- [ ] **Step 6: commit**
```bash
git add packages/rust/extensions/analysis-wasm
git commit -m "feat(rust): analysis-wasm crate wrapping analysis-core (histogram/quantile)"
```

---

## Task 4: goldenanalysis wasm wiring

**Files:** new `packages/typescript/goldenanalysis/src/core/wasm/{backend,loader,index}.ts` + `artifacts/.gitignore`; modify `aggregate.ts`, `src/core/index.ts`, `src/index.ts`, `package.json`.

- [ ] **Step 1: dep** — `goldenanalysis/package.json` deps: `"goldenmatch-wasm-runtime": "workspace:^"`.

- [ ] **Step 2: `wasm/backend.ts`**
```ts
import { createBackendRegistry } from "goldenmatch-wasm-runtime";
export interface AnalysisBackend {
  /** Equal-width histogram as [leftEdge, count] pairs (general path only). */
  histogram(values: Float64Array, bins: number): Array<[number, number]>;
  /** Linear-interpolation quantile. */
  quantile(values: Float64Array, q: number): number;
}
const _registry = createBackendRegistry<AnalysisBackend>();
export function setAnalysisBackend(b: AnalysisBackend | null): void { _registry.set(b); }
export function getAnalysisBackend(): AnalysisBackend | null { return _registry.get(); }
```

- [ ] **Step 3: `wasm/loader.ts`**
```ts
import { resolveWasmBytes, type LoadOptions } from "goldenmatch-wasm-runtime";
import type { AnalysisBackend } from "./backend.js";
export type { LoadOptions };
export function loadBytes(opts: LoadOptions): Promise<Uint8Array> {
  return resolveWasmBytes(opts, new URL("./artifacts/analysis_wasm_bg.wasm", import.meta.url));
}
export async function instantiateBackend(bytes: Uint8Array): Promise<AnalysisBackend> {
  const glue = (await import("./artifacts/analysis_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    histogram: (values: Float64Array, bins: number) => Float64Array;
    quantile: (values: Float64Array, q: number) => number;
  };
  await glue.default({ module_or_path: bytes });
  return {
    histogram(values, bins) {
      const flat = glue.histogram(values, bins); // [edge,count,...]
      const out: Array<[number, number]> = [];
      for (let i = 0; i < flat.length; i += 2) out.push([flat[i]!, flat[i + 1]!]);
      return out;
    },
    quantile(values, q) { return glue.quantile(values, q); },
  };
}
```

- [ ] **Step 4: `wasm/index.ts`**
```ts
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setAnalysisBackend } from "./backend.js";
export type { AnalysisBackend } from "./backend.js";
export interface EnableAnalysisWasmOptions extends EnableOptions {}
let _enabled = false;
export async function enableAnalysisWasm(opts: EnableAnalysisWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  const { instantiateBackend } = await import("./loader.js");
  const ok = await enableWasmBackend(
    opts, instantiateBackend, setAnalysisBackend,
    new URL("./artifacts/analysis_wasm_bg.wasm", import.meta.url),
  );
  if (ok) _enabled = true;
  return ok;
}
export function disableAnalysisWasm(): void { setAnalysisBackend(null); _enabled = false; }
```

- [ ] **Step 5: `wasm/artifacts/.gitignore`** — ignore `analysis_wasm_bg.wasm`, `analysis_wasm.js`, `analysis_wasm.d.ts`, `analysis_wasm_bg.wasm.d.ts` (keep dir tracked with the `.gitignore`).

- [ ] **Step 6: wire `aggregate.ts`** — make `histogram`/`quantile` backend-aware. Keep the null-filter + edge handling in pure-TS; delegate the GENERAL path to the backend if registered:
```ts
import { getAnalysisBackend } from "./wasm/index.js"; // or ./wasm/backend.js
// histogram:
export function histogram(values: ReadonlyArray<number | null | undefined>, bins: number): Array<[number, number]> {
  const vals = values.filter((v): v is number => v !== null && v !== undefined);
  if (vals.length === 0 || bins < 1) return [];
  const lo = Math.min(...vals); const hi = Math.max(...vals);
  if (hi === lo) return [[lo, vals.length]];
  const backend = getAnalysisBackend();
  if (backend !== null) return backend.histogram(Float64Array.from(vals), bins);
  const width = (hi - lo) / bins;
  const counts = new Array<number>(bins).fill(0);
  for (const v of vals) { let idx = Math.floor((v - lo) / width); if (idx >= bins) idx = bins - 1; counts[idx] = (counts[idx] ?? 0) + 1; }
  return counts.map((c, i) => [lo + i * width, c]);
}
// quantile:
export function quantile(values: ReadonlyArray<number | null | undefined>, q: number): number {
  const vals = values.filter((v): v is number => v !== null && v !== undefined);
  if (vals.length === 0) return 0;
  if (vals.length === 1) return vals[0]!;
  const backend = getAnalysisBackend();
  if (backend !== null) return backend.quantile(Float64Array.from(vals), q);
  const sorted = vals.slice().sort((a, b) => a - b);
  const pos = q * (sorted.length - 1); const loIdx = Math.floor(pos); const frac = pos - loIdx;
  const lo = sorted[loIdx]!; const hi = sorted[loIdx + 1];
  return hi === undefined ? lo : lo + (hi - lo) * frac;
}
```
> `getAnalysisBackend` is imported from the edge-safe `wasm/backend.ts` (NOT index.ts, to avoid pulling the loader). Import from `./wasm/backend.js`.

- [ ] **Step 7: barrel exports** — `src/core/index.ts`: `export { enableAnalysisWasm, disableAnalysisWasm } from "./wasm/index.js"; export type { AnalysisBackend } from "./wasm/index.js";`. Confirm `src/index.ts` re-exports `./core/index.js` (it does, wholesale) so the public API surfaces them.

- [ ] **Step 8: typecheck (CI; local if installed)** `npx tsc --noEmit`. Commit.
```bash
git add packages/typescript/goldenanalysis/src packages/typescript/goldenanalysis/package.json
git commit -m "feat(ts): goldenanalysis opt-in WASM histogram/quantile backend"
```

---

## Task 5: tsup artifact copy (goldenanalysis)

- [ ] **Step 1: `tsup.config.ts`** — mirror goldenmatch's: add `loader: { ".wasm": "copy" }`, `publicDir: false`, `onSuccess: "node scripts/copy_wasm_artifact.mjs"`, and ensure the generated glue (`analysis_wasm.js`) isn't bundled (mirror goldenmatch's `external`/exclude for `score_wasm.js` → use `analysis_wasm.js`).
- [ ] **Step 2: `scripts/copy_wasm_artifact.mjs`** — copy goldenmatch's, changing `src/core/wasm/artifacts` files to `analysis_wasm_bg.wasm` + `analysis_wasm.js`, dst `dist/core/wasm/artifacts`.
- [ ] **Step 3: commit** `build(ts): goldenanalysis tsup wasm artifact copy`.

---

## Task 6: parity test (CI-gated)

**File:** `packages/typescript/goldenanalysis/tests/parity/wasm-aggregate.test.ts`

- [ ] **Step 1: write it** — skip-guarded on the artifact (mirror `goldenmatch/tests/parity/wasm-scorer.test.ts`): `existsSync` the `analysis_wasm_bg.wasm`; `describe` vs `describe.skip`. For each case capture pure-TS first (`disableAnalysisWasm()`), `enableAnalysisWasm()`, capture WASM, `disableAnalysisWasm()`, assert `toBeCloseTo(_, 4)` (expect exact). Corpus: random finite arrays (seeded) at sizes 10/1k; a multi-bin case with a zero-count interior bin; the right-edge-inclusive max case; q ∈ {0,0.25,0.5,0.9,1}; plus a few Python-literal anchors (hard-coded `[values, bins/q, expected]`). Include empty/all-equal/single (expect identical, defense-in-depth).
- [ ] **Step 2: commit** `test(ts): CI-gated goldenanalysis WASM aggregate parity`.

---

## Task 7: bench (graduation gate)

**File:** `packages/typescript/goldenanalysis/scripts/bench_wasm_aggregate.mjs`

- [ ] **Step 1: write it** — mirror `goldenmatch/scripts/bench_wasm_scorer.mjs`: import histogram/quantile + enable/disable from `../dist/core/index.js`; 1M seeded values; 5-run median wall pure-TS vs WASM for histogram(256 bins) + quantile(0.9); print speedups. Build-first note.
- [ ] **Step 2: commit** `bench(ts): goldenanalysis pure-TS vs WASM histogram/quantile`.

---

## Task 8: CI `analysis_wasm` lane

**File:** `.github/workflows/ci.yml`

- [ ] **Step 1: filter entry** — add `analysis_wasm` to the `changes` job `filters:` (paths: `packages/rust/extensions/analysis-wasm/**`, `packages/rust/extensions/analysis-core/**`, `packages/typescript/goldenanalysis/src/core/wasm/**`, `packages/typescript/goldenmatch-wasm-runtime/**`, `.github/workflows/ci.yml`) AND to the `outputs:` map.
- [ ] **Step 2: job** — copy the `wasm_score` job, gate `if: needs.changes.outputs.analysis_wasm == 'true' || needs.changes.outputs.ci_workflow == 'true'`, `working-directory: packages/typescript/goldenanalysis`, run `bash packages/rust/extensions/analysis-wasm/build_wasm.sh`, `pnpm install --frozen-lockfile && pnpm --filter goldenanalysis build`, `pnpm --filter goldenanalysis exec vitest run tests/parity/wasm-aggregate.test.ts`, then the bench as `continue-on-error: true` (informational until the dist path is validated on a real run, then flip — mirrors the open #878 item-4).
- [ ] **Step 3: validate YAML** `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`. Commit.

---

## Task 9: docs + changelog

- [ ] **Step 1: `goldenanalysis/CHANGELOG.md`** — Unreleased entry (opt-in WASM histogram/quantile; pure-TS default+fallback; bench-gated; new `analysis_wasm` lane).
- [ ] **Step 2: `goldenanalysis/CLAUDE.md`** (+ a line in `packages/typescript/CLAUDE.md` about the shared `goldenmatch-wasm-runtime`) — document `enableAnalysisWasm`, the build step (`build_wasm.sh`), the covered ops, and the shared runtime.
- [ ] **Step 3: commit** `docs(ts): document goldenanalysis opt-in WASM + shared runtime`.

---

## Done-when

- `goldenmatch-wasm-runtime` builds; goldenmatch refactored, #878 wasm tests green unchanged (CI).
- `analysis-wasm` host `cargo test`/clippy/fmt clean; builds for wasm32 in CI.
- `analysis_wasm` CI lane: builds artifact, `wasm-aggregate.test.ts` un-skipped + passes (WASM≈pure-TS 4dp), bench prints a speedup.
- Default goldenanalysis build + vitest unaffected (parity skips).
- PR off `feat/analysis-wasm-slice` (base main); merge-on-green.

## Out of scope

graph-core / fingerprint-core / goldencheck-core slices; token_sort WASM (item 2) + #878 dist-validation (item 4); covering aggregate ops beyond histogram/quantile.
