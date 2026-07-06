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

## 5. Wire `detect.ts`

The **dictionary resolution stays host** (JS `listDomains()`/`loadDomain()`) —
identical to Python's `detect.py` split, where only the resolved-input scoring
core is the kernel. In `detectDomainDetailed`, after `columns` and the resolved
`domains: Array<[string, string[]]>` are built and the early `no_data` guards
pass, consult the backend:
```ts
const backend = getInfermapBackend();
if (backend) {
  return backend.detectDomain(columns, resolvedDomains, minScore);
}
// ...existing pure-TS scoring below unchanged (the lossy fallback)...
```
Public API (`detectDomain`, `detectDomainDetailed`, `DEFAULT_MIN_SCORE`) is
**unchanged**; only an internal fast-path is inserted. The exact insertion point
is after the `columns`/candidate resolution and the two `no_data` early returns,
so the backend sees the same resolved inputs the pure path would score.

> The `domains` value passed to the backend must be the SAME resolved
> `[name, hints[]]` list the pure path scores over (post `listDomains()` filter +
> `loadDomain()` hint expansion) — the kernel does no dictionary work.

## 6. Build + artifact

- `scripts/build_infermap_wasm.mjs` — mirrors `build_goldencheck_wasm.mjs` /
  analysis: `wasm-pack build packages/rust/extensions/infermap-wasm --target web`,
  then place `infermap_wasm_bg.wasm` + `infermap_wasm.js` glue into
  `src/core/wasm/artifacts/`. Requires `wasm-pack` + `wasm32-unknown-unknown`
  (CI-only; box lacks the toolchain).
- `scripts/copy_wasm_artifact.mjs` — mirrors analysis: fan the two artifact files
  out to every plausible `dist/**/artifacts/` parent (tsup bundling ambiguity);
  no-op with a warning when the artifact is absent.
- Artifacts are **git-ignored / not committed** (CI-built model). A default
  checkout has no artifact → pure-TS default, parity test skips.

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

New job in `.github/workflows/ci.yml`, mirroring the `analysis_wasm` lane:
1. `dorny/paths-filter` entry `infermap_wasm` covering
   `packages/rust/extensions/infermap-wasm/**`,
   `packages/rust/extensions/infermap-core/**`,
   `packages/typescript/infermap/src/core/wasm/**`,
   `packages/typescript/infermap/src/core/detect.ts`,
   `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts`,
   `scripts/build_infermap_wasm.mjs`.
2. Job (gated on that filter): install `wasm-pack` + `wasm32-unknown-unknown`,
   run `node scripts/build_infermap_wasm.mjs`, then run the vitest parity test
   (un-skipped, artifact now present). Advisory lane (not in the required set),
   matching the other `*_wasm` lanes.

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

## 11. Build environment constraints

- **Box-runnable:** effectively only static review + `node -c` syntax checks of
  the `.mjs` scripts. No `cargo`, no `wasm-pack`, no `vitest` (all CI-only).
- **CI-only:** the Rust host unit test, the wasm build, `tsc`/`tsup`, and the
  vitest parity test (the `infermap_wasm` advisory lane).
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
