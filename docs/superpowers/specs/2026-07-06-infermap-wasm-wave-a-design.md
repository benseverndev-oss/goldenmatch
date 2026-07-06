# InferMap WASM/TS surface — Wave A (foundation + `detect_domain`) design

**Date:** 2026-07-06
**Status:** Approved (design)
**Depends on:** InferMap Rust cutover Waves 1–4 (`infermap-core` now exports 6 kernels). Wave A consumes only `detect_domain` (Wave 1).
**Branch:** `feat/infermap-wasm-wave-a` off fresh `origin/main`.

## 1. Goal

Stand up the *entire* opt-in-WASM pipeline for the TS `infermap` package — a new
`infermap-wasm` wasm-bindgen crate over `infermap-core`, the TS
`wasm/{backend,loader,index}.ts` plumbing, a build script, a CI lane, and a
byte-parity gate — and wire **one** kernel (`detect_domain`) through it end to
end as the proof.

The payoff is anti-drift: TS `infermap` currently carries a *separate*
hand-written reimplementation of every scorer + `detect`. WASM makes the TS
surface run the **same** `infermap-core` kernel that Python's native wheel runs,
collapsing three surfaces (Python, Rust FFI, TS) onto one reference. This is the
`project_wasm_acceleration_arc` pattern (score-core #878/#879, analysis-core
#880) applied to InferMap.

Wave A is the vertical slice. Waves B (name scorers) and C (profile +
pattern_type) reuse this foundation and are out of scope here.

## 2. Established patterns this mirrors

- **Crate:** `score-wasm` / `goldencheck-wasm` — standalone `[workspace]`,
  `crate-type = ["cdylib","rlib"]`, path-dep on the `-core`, `wasm-bindgen` under
  `[target.'cfg(target_arch="wasm32")'.dependencies]`, thin shims delegating to
  the core so behavior is byte-identical by construction.
- **Boundary = JSON string, crossed once per call:** `goldencheck-wasm` exposes
  every entry as `fn foo_json(input_json: &str) -> String` with `serde`/`serde_json`
  *in the wasm crate* (the `-core` stays serde-free). Correct for `detect_domain`,
  whose input is nested (`domains: [[name, hints[]]]`) — not a flat typed array.
- **Artifact model = CI-built, NOT committed:** `analysis-wasm` — the build script
  emits `src/core/wasm/artifacts/<crate>{_bg.wasm,.js}`; the parity test is
  `hasArtifact ? describe : describe.skip`; the CI `<pkg>_wasm` lane builds the
  artifact first, then runs the parity test un-skipped. This is what lets a box
  that cannot run `wasm-pack` ship the wave — local runs skip, CI proves it (same
  contract as the Python native-parity lanes).
- **Enable/loader plumbing:** `goldenmatch-wasm-runtime` provides
  `createBackendRegistry` + `enableWasmBackend` (byte resolution + env detection);
  `index.ts` owns the artifact URL + backend, `loader.ts` adapts the glue,
  `backend.ts` defines the interface + registry.

> Rejected: the `goldencheck-wasm` base64-embed-into-`.ts` artifact model (ships
> the wasm inside the npm package, edge-safe). It requires a local `wasm-pack`
> build to generate the committed bytes, which the box cannot do. Noted as a
> possible later enhancement if edge/browser shipping is wanted; Wave A uses the
> CI-built model so it is box-shippable.

## 3. The `infermap-wasm` crate (new)

`packages/rust/extensions/infermap-wasm/`

### `Cargo.toml`
```toml
[workspace]                      # standalone: neither infermap-core's nor a parent
                                 # workspace claims this wrapper

[package]
name = "infermap-wasm"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "wasm-bindgen wrapper over infermap-core for the infermap TS opt-in WASM backend"

[lib]
name = "infermap_wasm"
crate-type = ["cdylib", "rlib"]  # cdylib for wasm; rlib so host unit tests link

[dependencies]
infermap-core = { path = "../infermap-core" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"

[target.'cfg(target_arch = "wasm32")'.dependencies]
wasm-bindgen = "0.2"
```

### `src/lib.rs`
- Local serde DTOs (kept here, NOT in `infermap-core`):
  ```rust
  #[derive(Deserialize)]
  struct DetectInput {
      columns: Vec<String>,
      domains: Vec<(String, Vec<String>)>,   // [ [name, hints[]], ... ]
      min_score: f64,
  }
  #[derive(Serialize)]
  struct DetectOutput {
      domain: Option<String>,
      score: f64,
      runner_up: Option<String>,
      runner_up_score: f64,
      reason: String,
  }
  ```
- Host-testable impl:
  ```rust
  pub fn detect_domain_json_impl(input_json: &str) -> String {
      let inp: DetectInput = serde_json::from_str(input_json).expect("valid detect input json");
      let d = infermap_core::detect_domain(&inp.columns, &inp.domains, inp.min_score);
      let out = DetectOutput {
          domain: d.domain, score: d.score,
          runner_up: d.runner_up, runner_up_score: d.runner_up_score,
          reason: d.reason,
      };
      serde_json::to_string(&out).expect("serialize detect output")
  }
  ```
- wasm-only re-export (mirrors analysis-wasm's `#[cfg(target_arch="wasm32")] mod`):
  ```rust
  #[cfg(target_arch = "wasm32")]
  mod wasm {
      use wasm_bindgen::prelude::*;
      #[wasm_bindgen]
      pub fn detect_domain_json(input_json: &str) -> String {
          super::detect_domain_json_impl(input_json)
      }
  }
  ```
- Host `#[cfg(test)]` unit test on `detect_domain_json_impl` (a confident case + a
  no-data case), asserting the round-tripped JSON matches the expected `Detection`.

**`infermap-core` is untouched** — no serde, no new deps. `detect_domain`'s
signature (`&[String]`, `&[(String, Vec<String>)]`, `f64`) already matches the DTO.

### Boundary correctness
- One JSON crossing per `detect` call (perf-audit lesson: boundary cost dwarfs the
  kernel).
- `f64` scores round-trip bit-exactly through `serde_json` ↔ `JSON.parse`
  (both use round-trippable shortest-float formatting).
- `Option<String>` ↔ `null`; `reason` string verbatim. JSON transport is lossless
  for this shape; the parity test compares the parsed `DetectionResult` objects,
  never JSON text.

## 4. TS wiring — `packages/typescript/infermap/src/core/wasm/`

> **Dependency (devDependency, NOT a runtime dep):**
> `packages/typescript/infermap/package.json` must add
> `"goldenmatch-wasm-runtime": "workspace:^"` under **`devDependencies`** — the
> source of `createBackendRegistry` / `enableWasmBackend` / `EnableOptions`. Every
> real `*_wasm` package (goldenanalysis/goldenflow/goldenmatch/goldenpipe) carries
> it as a devDependency because tsup **inlines** the plumbing (`noExternal`) so it
> is never a published runtime dep (it is not on npm).
>
> **`tsup.config.ts` must gain the artifact-shipping keys** (mirror
> `goldenanalysis/tsup.config.ts`), or the loader's
> `new URL('./artifacts/infermap_wasm_bg.wasm', import.meta.url)` won't resolve in
> `dist` (parity in §7 runs vitest over `src/` so it would pass regardless — this
> is the consumer-facing shipping model):
> ```ts
> dts: { resolve: ["goldenmatch-wasm-runtime"] },   // roll bundled types into .d.ts
> loader: { ".wasm": "copy" },
> publicDir: false,
> onSuccess: "node scripts/copy_wasm_artifact.mjs",
> noExternal: ["goldenmatch-wasm-runtime"],          // inline plumbing, not a pub dep
> external: [/infermap_wasm\.js$/],                  // runtime-only glue; don't resolve at build
> ```
> (The existing infermap `tsup.config.ts` has `dts: true` and none of these; Wave A
> replaces `dts: true` with the `dts: { resolve: [...] }` form and adds the rest.)

### `backend.ts`
```ts
import { createBackendRegistry } from "goldenmatch-wasm-runtime";
import type { DetectionResult } from "goldencheck-types";

export interface InfermapBackend {
  /** Resolved-input detect scoring. Dictionary resolution stays host. */
  detectDomain(
    columns: string[],
    domains: Array<[string, string[]]>,
    minScore: number,
  ): DetectionResult;
}

const _registry = createBackendRegistry<InfermapBackend>();
export function setInfermapBackend(b: InfermapBackend | null): void { _registry.set(b); }
export function getInfermapBackend(): InfermapBackend | null { return _registry.get(); }
```

### `loader.ts`
`instantiateBackend(bytes)` dynamic-imports `./artifacts/infermap_wasm.js`,
`await glue.default({ module_or_path: bytes })`, returns an `InfermapBackend`
whose `detectDomain` JSON-stringifies `{columns, domains, min_score}`, calls
`glue.detect_domain_json(json)`, and `JSON.parse`s the result into a
`DetectionResult`.

### `index.ts`
`enableInfermapWasm(opts)` / `disableInfermapWasm()` mirroring
`enableAnalysisWasm` — lazy-import `loader.js`, call `enableWasmBackend(opts,
instantiateBackend, setInfermapBackend, new URL("./artifacts/infermap_wasm_bg.wasm",
import.meta.url))`; `disable` calls `setInfermapBackend(null)`.

## 5. Wire `detect.ts` (requires a hint-resolution hoist, not a drop-in)

**Reality of the current code:** `detectDomainDetailed` does NOT materialize a
`[name, hints[]][]` structure. At the scoring point `domains` is a `string[]` of
*names only* (`candidates ?? listDomains().filter(d => d !== "generic")`), and
hint expansion is **fused into the scoring loop** — for each name it calls
`loadDomain(d)`, builds an `allHints` Set inline, `continue`s if empty, and
scores in the same pass. So Wave A must **hoist** hint-resolution into a pre-pass
before the backend call, then let the backend (or the pure loop) score over that
hoisted structure. This is a real refactor of `detectDomainDetailed`, not an
insert.

The two input-shape `no_data` early returns (no columns / no records) stay host,
unchanged, before resolution. Then:
```ts
const domainNames = candidates ?? listDomains().filter((d) => d !== "generic");

// Hoist: resolve each candidate's hint-set into a flat [name, hints[]] list so
// the SAME resolved input feeds the kernel or the pure loop. Empty-hint domains
// are INCLUDED here (the kernel skips them via `hints.is_empty()`, and the pure
// loop skips them via `hints.length === 0`), so both paths score identically.
const resolved: Array<[string, string[]]> = domainNames.map((d) => {
  const pack = loadDomain(d);
  const allHints = new Set<string>();
  for (const spec of Object.values(pack.types)) {
    for (const h of spec.name_hints) allHints.add(h);
  }
  return [d, Array.from(allHints)];
});

const backend = getInfermapBackend();
if (backend) {
  // Kernel owns ALL scoring below, INCLUDING the scored-empty `no_data`
  // (Rust detect_domain returns reason "no_data" when nothing scores).
  return backend.detectDomain(columns, resolved, minScore);
}

// Pure-TS fallback — byte-identical to today, re-expressed over `resolved`:
const scored: Array<[string, number]> = [];
for (const [d, hints] of resolved) {
  if (hints.length === 0) continue;               // == Rust hints.is_empty() skip
  let hits = 0;
  for (const c of columns) {
    for (const h of hints) { if (hintMatches(h, c)) { hits++; break; } }
  }
  scored.push([d, hits / Math.max(columns.length, 1)]);
}
if (scored.length === 0) {
  return { domain: null, score: 0, runner_up: null, runner_up_score: 0, reason: "no_data" };
}
// ...existing sort / below-min / tie / confident tail unchanged...
```
Public API (`detectDomain`, `detectDomainDetailed`, `DEFAULT_MIN_SCORE`) is
**unchanged**. The pure path's output is byte-identical to the pre-refactor code
(same iteration order, same empty-hint skip, same `hits/max(cols,1)`, same
sort/tie/threshold tail). The backend path delegates the entire scored region —
including the scored-empty `no_data` — to the kernel.

> Byte-equivalence of "include empty-hint domains in `resolved`": a domain with
> an empty hint-set contributes nothing in the pure loop (`continue`) and is
> skipped by the kernel (`hints.is_empty()`), so passing all candidates to the
> kernel is equivalent to the pre-filter the pure loop did inline.

## 6. Build + artifact

**Build model = `cargo build` + pinned `wasm-bindgen-cli`, NOT `wasm-pack`.** The
§3 crate declares `wasm-bindgen` under a `[target.'cfg(target_arch="wasm32")']`
table (the score-wasm layout); `wasm-pack`'s dependency detection does not see
that table, so `wasm-pack build` fails. The established build for this crate
layout (score-wasm / analysis-wasm / goldenflow-wasm) is a cargo build + the
`wasm-bindgen` CLI:

- `packages/rust/extensions/infermap-wasm/build_wasm.sh` — mirrors
  `score-wasm/build_wasm.sh` (minus its base64 universal-loader step, which §2
  rejects for Wave A):
  1. `rustup target add wasm32-unknown-unknown`
  2. `cargo build --manifest-path <crate>/Cargo.toml --target wasm32-unknown-unknown --release`
  3. resolve the pinned `wasm-bindgen` version from the crate's **committed
     `Cargo.lock`** (`grep -A1 '^name = "wasm-bindgen"$' ... version`), and
     `cargo install wasm-bindgen-cli --version "=$WB_VER" --locked` if the on-PATH
     CLI doesn't already match. **A CLI/crate version skew produces broken glue
     that fails at runtime, not build time** — this pin is load-bearing, which is
     why `Cargo.lock` is committed for this crate.
  4. `wasm-bindgen <crate>/target/wasm32-unknown-unknown/release/infermap_wasm.wasm
     --target web --out-dir packages/typescript/infermap/src/core/wasm/artifacts
     --out-name infermap_wasm` → emits `infermap_wasm_bg.wasm` + `infermap_wasm.js`.
- `packages/typescript/infermap/scripts/copy_wasm_artifact.mjs` — mirrors
  goldenanalysis: fan the two artifact files out to every plausible
  `dist/**/artifacts/` parent (tsup bundling ambiguity); no-op with a warning when
  the artifact is absent.
- **Commit the crate's `Cargo.lock`** (so the pinned `wasm-bindgen` version
  resolves in CI). The `src/core/wasm/artifacts/*` outputs are **git-ignored /
  not committed** (CI-built model). A default checkout has no artifact → pure-TS
  default, parity test skips.

## 7. Parity gate — `tests/parity/infermap-wasm.parity.test.ts`

`hasArtifact ? describe : describe.skip` (artifact existence via
`existsSync(new URL(".../artifacts/infermap_wasm_bg.wasm"))`). The test:
1. `await enableInfermapWasm({ require: true })`.
2. For each corpus case, assert `detectDomainDetailed(input, candidates, minScore)`
   **with the backend enabled** deep-equals the same call **with the backend
   disabled** (pure TS). `afterAll(disableInfermapWasm)`.

Corpus mirrors the Wave 1 Python parity corpus (ASCII):
confident pick; exact 2-way tie (host stable-sort order); 3-way score tie;
below-min-score; empty columns → no_data; all-hint-less → no_data; multi-token
hint; hint longer than the column; case-insensitivity. Uses real dictionary
domains via `listDomains()` where practical plus synthetic `candidates` for the
tie/edge cases (so the corpus controls the exact `domains` structure).

> **The parity gate is also a drift audit.** TS `detect.ts` is a *separate*
> reimplementation of the Rust kernel; the corpus may surface pre-existing
> divergences (tie-break order, the `hintMatches` token-run logic, `str.lower`
> vs the Rust ASCII-lower edge). Where WASM (== the Rust kernel, == Python) and
> pure-TS disagree on an ASCII case, that is a **real drift finding**, and the
> resolution follows the thesis: WASM is the reference; the pure-TS path is the
> documented lossy fallback — OR the pure TS is corrected to match. Any such
> divergence is surfaced explicitly in the PR, never silently skipped. (Unicode
> `str.lower`/token edges stay out of the must-pass corpus, per the Wave 1/2
> documented edge.)

## 8. CI — `infermap_wasm` lane

New job in `.github/workflows/ci.yml`, mirroring the real `analysis_wasm` lane:
1. `dorny/paths-filter` entry `infermap_wasm` covering
   `packages/rust/extensions/infermap-wasm/**`,
   `packages/rust/extensions/infermap-core/**`,
   `packages/typescript/goldenmatch-wasm-runtime/**` (every `*_wasm` filter
   includes it — the parity test imports it),
   `packages/typescript/infermap/src/core/wasm/**`,
   `packages/typescript/infermap/src/core/detect.ts`,
   `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts`,
   `packages/typescript/infermap/scripts/copy_wasm_artifact.mjs`,
   `packages/typescript/infermap/tsup.config.ts`.
   (`build_wasm.sh` is already covered by the `infermap-wasm/**` entry above.)
2. Job (gated on that filter), mirroring `analysis_wasm` step-for-step:
   - `rustup target add wasm32-unknown-unknown`;
   - run `packages/rust/extensions/infermap-wasm/build_wasm.sh` (cargo build +
     pinned `wasm-bindgen-cli`, emits the artifact into the TS package);
   - `pnpm --filter goldenmatch-wasm-runtime build` (the parity test imports this
     package — the real `analysis_wasm` lane builds it first);
   - run the vitest parity test un-skipped (artifact now present);
   - (mirroring analysis) optionally `tsup` build + run `copy_wasm_artifact.mjs`
     to catch bundled-`dist` path breakage.
   Advisory lane (not in the required set), matching the other `*_wasm` lanes.

## 9. Out of scope

- The other 5 kernels (`exact_score`, `fuzzy_name_score`, `initialism_score`,
  `profile_score`, `pattern_match_types`) — Waves B/C.
- The base64-embed edge-shipping artifact model (§2, noted for later).
- Any change to `detect`'s public API, the dictionaries, or scoring semantics.
- Wiring the WASM backend into `engine.ts`/`map.ts` auto-enable — Wave A leaves it
  strictly opt-in (`enableInfermapWasm()`), like every other `*_wasm` surface.

## 10. Risk assessment

- **Almost entirely CI-verified.** The box can neither `cargo build`/`wasm-pack`
  the crate nor run vitest (OOM, per `feedback_box_memory_oom_ts`). Local
  verification is limited to hand/eye review + `tsc`-shape reasoning; the Rust
  host unit test, the wasm build, and the parity test all first run in CI. This
  raises write-against-spec care; there is no box smoke test. Mitigation: the
  crate shim is trivial (delegates to the already-parity-proven `detect_domain`),
  and the TS wiring is a line-for-line mirror of the goldenanalysis wasm module.
- **Drift audit may find work.** As in §7, the parity corpus can expose
  TS-vs-Rust `detect` divergences. That is a feature (it is exactly what this
  surface exists to eliminate), but it means Wave A's parity step may branch into
  "reconcile a divergence" rather than "green on first run." Handled by making
  WASM the reference and documenting/fixing the pure path — surfaced in the PR.
- **Boundary encoding.** JSON is lossless for this shape (§3); the only
  theoretical risk (float formatting) is eliminated by round-trippable f64
  serialization on both sides. Verified by the parity gate.
- **`detect.ts` hoist refactor must stay byte-identical.** §5 restructures the
  fused resolve+score loop into a resolve pre-pass + score loop. The pure-path
  output must be unchanged (same iteration order, empty-hint skip, `hits/max`,
  sort/tie/threshold tail). This is covered by the existing TS `detect` unit
  tests (which must stay green after the refactor, box-permitting via CI) AND the
  new parity corpus. The refactor is mechanical but is the one place a
  non-WASM regression could slip in, so the pure path is validated independently
  of the backend.

## 11. Build environment constraints

- **Box-runnable:** effectively only static review + `node -c` syntax checks of
  the `.mjs` scripts. No `cargo`, no `wasm-pack`, no `vitest` (all CI-only).
- **CI-only:** the Rust host unit test, the wasm build, `tsc`/`tsup`, and the
  vitest parity test (the `infermap_wasm` advisory lane).
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
