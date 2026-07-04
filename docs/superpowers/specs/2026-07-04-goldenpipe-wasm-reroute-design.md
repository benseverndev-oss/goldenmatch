# SP3 — goldenpipe-wasm + TS runtime reroute (the drift-kill)

**Date:** 2026-07-04
**Status:** Design (approved for spec review)
**Depends on:** SP1 (`goldenpipe-core` crate, MERGED #1418 — provides the `*_json` kernels and the golden vectors) and, conceptually, SP2 (`goldenpipe-native` Python parity gate, #1424 — established the parity-gate methodology this mirrors on TS). SP3 is code-independent of SP2 (different surface).

---

## 1. Goal

Make the edge-TS planner reach the **same `goldenpipe-core`** via WASM, and prove pure-TS is byte-identical to it — so the Python and TS planners can never diverge again. This is where the program's value lands (SP1/SP2 are the foundation).

Two things ship:

1. **A runtime reroute** (the user's explicit pick): under an opt-in `enableWasm()`, the TS planner's five pure functions route through the `goldenpipe-wasm` kernel (which is `goldenpipe-core` compiled to wasm). Pure-TS stays the **edge-safe default and permanent fallback** — the reroute is strictly opt-in.
2. **The anti-drift lock**: a cross-surface parity gate reusing the SP1 golden vectors — pure-TS == wasm == core — that locks pure-TS to the reference forever.

### Honesty (kept visible, not buried)

The runtime reroute adds a serialize → wasm → deserialize hop per planner call for **zero perf benefit** — a pipeline planner runs once over ~5 stages, no hot loop. Its value is cross-surface literalism ("TS executes the same Rust core"), not speed; the suite's measure-first perf gate is **waived** for this component exactly as it was for SP1/SP2. The reroute is gated behind `enableWasm()` so pure-TS stays the zero-cost default, and **the drift-kill (conform + parity gate) holds on both paths regardless of which one runs.** If the reroute were the only deliverable it would not be worth the hop; the parity gate + the `autoConfig` conformance fix are the real drift-kill and stand on their own.

---

## 2. Scope

**In scope**
- New crate `packages/rust/extensions/goldenpipe-wasm/` (wasm-bindgen over `goldenpipe-core::json`).
- New TS `packages/typescript/goldenpipe/src/core/wasm/{backend,loader,index}.ts` (opt-in backend + loader, mirror goldenflow).
- A `plannerJson` serialize/deserialize boundary (TS analogue of SP2's `_planner_json.py`) so the rich TS planner objects can cross the string kernel boundary.
- Reroute all **five** planner seams (the fully-literal option): `Resolver.resolve`, `Router.apply`, the three `decisions` gates, `autoConfig`, and the `skip_if` falsy predicate.
- `pipeline.ts:autoConfig` **conformance fix**: add the `identity_resolve` path so pure-TS matches the core.
- Cross-surface parity gate (Leg A pure-TS == vectors, Leg B wasm == vectors) + a CI `goldenpipe_wasm` lane.

**Out of scope**
- Rewriting the Runner loop / IO / adapters (stays host, by design — orchestration is boundary-bound).
- The `decisions` **adapter** severity drift (GoldenCheck-JS numeric `Finding.severity` → string label). The predicate *logic* already matches Python byte-for-byte (`decisions.ts:7-13` self-documents this); the no-op is upstream in the check adapter, which SP3 does not touch. Rerouting the predicates prevents FUTURE predicate drift only.
- DuckDB / Postgres surfaces (a planner has no row-wise SQL meaning — dropped in the program decomposition).

---

## 3. Components

### 3.1 The WASM crate — `packages/rust/extensions/goldenpipe-wasm/`

Mirror `goldenflow-wasm` exactly.

`Cargo.toml`:
- Standalone `[workspace]` (empty) so it can path-depend on `goldenpipe-core` without either crate's workspace claiming it.
- `[package]` name `goldenpipe-wasm`, version `0.1.0`.
- `[lib] crate-type = ["cdylib", "rlib"]` — `cdylib` for the wasm artifact, `rlib` so a host `cargo test` links.
- `[dependencies] goldenpipe-core = { path = "../goldenpipe-core" }`.
- `[target.'cfg(target_arch = "wasm32")'.dependencies] wasm-bindgen = "0.2"`.
- **No arrow, no serde** (the core owns serde; this crate only marshals `&str`/`String`).

`src/lib.rs`:
- `#[cfg(target_arch = "wasm32")] mod wasm { ... }` so a non-wasm `cargo build`/`cargo test` compiles clean (matches goldenflow-wasm / score-wasm).
- Inside: five `#[wasm_bindgen] pub fn <name>(input: &str) -> String` delegating 1:1 to `goldenpipe_core::json::{resolve_json, apply_decision_json, evaluate_builtin_json, auto_config_json, skip_if_falsy_json}`. Identical surface to the SP2 pyo3 crate.
- A `host-parity` note: because the fns take `&str`/`String`, they are byte-identical to what the native wheel returns — same core, same serde, same `preserve_order`.

**Artifact strategy:** build with `wasm-pack`/`wasm-bindgen --target web`, then generate the **base64 module** (`goldenpipe_wasm_base64`) — the universal edge-safe strategy `goldenmatch-wasm-runtime` already supports (no fetch/fs/`import.meta.url`; works in Workers + Deno + every bundler). The `.wasm`/glue artifacts are **built in CI, never committed** (per the suite convention).

### 3.2 TS opt-in backend + loader — `src/core/wasm/`

Mirror `goldenflow/src/core/wasm/`.

`backend.ts`:
- `export interface PipeWasmBackend` with the five string→string methods: `resolveJson(s: string): string`, `applyDecisionJson`, `evaluateBuiltinJson`, `autoConfigJson`, `skipIfFalsyJson`.
- `createBackendRegistry<PipeWasmBackend>()` singleton with `setPipeWasmBackend(b | null)` / `getPipeWasmBackend()` — edge-safe (no `node:*`), test-isolatable (mirrors goldenmatch's `setScorerBackend(null)`).

`loader.ts`:
- `resolveWasmBytes(opts)` pinning `new URL("./artifacts/goldenpipe_wasm_bg.wasm", import.meta.url)` **in this module** (so `import.meta.url` resolves to goldenpipe's own dist), delegating to the shared runtime.
- `instantiateBackend(bytes)` dynamic-imports the generated glue (`./artifacts/goldenpipe_wasm.js`, absent in a default checkout) and adapts the five snake_case exports to the camelCase `PipeWasmBackend`.

`index.ts`:
- `enableWasm(opts?)`: resolve bytes → instantiate → `setPipeWasmBackend(backend)`; on failure, keep pure-TS unless `{ require: true }` (mirror goldenflow). Re-export `backend`/`loader` types.

### 3.3 The `plannerJson` boundary — `src/core/wasm/plannerJson.ts`

The meat. The TS planner functions take rich objects (`StageRegistry` with live `Stage` instances, `PipeContext`, `PlannedStage[]`); the kernel speaks JSON strings. `plannerJson.ts` owns the serialize → `backend.<fn>` → deserialize round-trip for each seam, serializing to **exactly the core's JSON shapes** (verified in SP1):

- `resolveViaWasm(config, registry, backend)`: serialize `{ config, stages: [{ key, name, produces, consumes } ...] }` from `registry` **metadata only** (resolve needs no executable stages). Call `backend.resolveJson`. Parse the `ok`/`err` envelope → return `ExecutionPlan` (`PlannedStage[]`) or throw `WiringError` / unknown-stage error reconstructed from `{kind: wiring|unknown_stage, ...}`.
- `applyDecisionViaWasm(decision, remaining, backend)`: serialize `{ decision, remaining: [{name, use} ...] }` → `backend.applyDecisionJson` → parse `{ remaining, router_note? }`. The host maps inserted **names** → `Stage` objects (core returns names only, matching `router.rs`).
- `evaluateBuiltinViaWasm(name, ctx, backend)`: serialize `{ name, ctx: { artifacts, metadata } }` → `backend.evaluateBuiltinJson` → parse `Decision | null` (`"null"` → `null`).
- `autoConfigViaWasm(available, identityOpts, backend)`: serialize `{ available, identity_opts }` → `backend.autoConfigJson` → parse `PipelineConfig`.
- `skipIfFalsyViaWasm(value, backend)`: serialize the value → `backend.skipIfFalsyJson` → `JSON.parse` a boolean.

### 3.4 Reroute the five seams (pure-TS default)

Each pure-TS planner function grows a guard: `const b = getPipeWasmBackend(); if (b) return <viaWasm>(...); /* else pure-TS below */`.

- `engine/resolver.ts` → `Resolver.resolve`
- `engine/router.ts` → `Router.apply`
- `decisions.ts` → `severityGate` / `piiRouter` / `rowCountGate`
- `pipeline.ts` → `autoConfig`
- the `skip_if` falsy check (wherever the resolver evaluates it) → `skipIfFalsyViaWasm`

**Pure-TS runs whenever no backend is set** — the default and the permanent edge fallback.

### 3.5 The `autoConfig` conformance fix — `pipeline.ts`

Today `autoConfig` (`pipeline.ts:75`) filters `DEFAULT_STAGE_ORDER` and has **no identity path**; the core appends `identity_resolve` when identity opts are supplied. Fix pure-TS to append the identity stage under the same condition the core uses (truthy non-empty `identity_opts`) so pure-TS == core. This is a **real TS behavior change** to zero-config output when identity opts are given (divergence resolved in the reference's favor). An empty `{}` must stay falsy (no identity stage), matching the core / Python `if self._identity_opts`.

### 3.6 Parity gate — `tests/…/planner-parity.test.ts`

Reuse the SP1 golden vectors (`packages/rust/extensions/goldenpipe-core/tests/vectors/{resolve,apply_decision,evaluate_builtin,auto_config,skip_if}.json`) as the shared cross-surface truth (path resolved repo-relative from the test file).

- **Leg A (pure-TS == vectors)** — no wasm; for each `{input, expected}`, call the **pure-TS** planner (through `plannerJson`'s serialize-side, or a thin pure-TS `*_json` shim mirroring `_planner_json.py`) and assert deep-equal to `expected`. This is the drift-lock.
- **Leg B (wasm == vectors)** — mandatory in CI (skips only if the wasm artifact is absent, i.e. a default checkout): `enableWasm()` from the base64 artifact, run the same vectors through `getPipeWasmBackend()`, assert equal.

A backend-registry unit test mirrors goldenflow's (`getPipeWasmBackend()` null by default; `set`/`get`/reset isolation).

### 3.7 CI — `goldenpipe_wasm` lane

- `dorny/paths-filter` output `goldenpipe_wasm` on paths `goldenpipe-core/**`, `goldenpipe-wasm/**`, `packages/typescript/goldenpipe/**`. Editing `ci.yml` re-runs all jobs.
- Job: `cargo fmt --check` + `clippy -D warnings` on `goldenpipe-wasm` (host target) and `goldenpipe-core`; build the wasm artifact + base64; `pnpm` build goldenpipe TS; run the parity + backend tests (Leg A + Leg B).
- This gives `goldenpipe-wasm` its CI home; `goldenpipe-core` already gained CI coverage in SP2's native lane.

---

## 4. Data flow

```
enableWasm() [opt-in]                pure-TS [default, edge fallback]
      |                                       |
Resolver.resolve(config, registry)            |
  getPipeWasmBackend() -> set? ---- yes ----> plannerJson.resolveViaWasm
      |                                          serialize {config, registry-meta}
      | no                                       -> backend.resolveJson (wasm = core)
      v                                          -> parse ok/err -> ExecutionPlan
  pure-TS Resolver (unchanged)  <---- both paths produce byte-identical plans --->
      |
Runner loop (HOST — never moves): executes stages, calls Router.apply / decisions
  between stages (each also backend-guarded), maps inserted names -> Stage objects.
```

The **host owns the WHEN** (the loop, IO, calling into goldencheck/flow/match); the **core owns the WHAT** (order, wiring validation, routing, auto-config), computed identically whether pure-TS or wasm runs it.

## 5. Error handling

- `resolveJson` returns an `ok`/`err` envelope; `resolveViaWasm` throws the reconstructed `WiringError` (with `.stage/.missing/.available`) or an unknown-stage error, matching pure-TS raises so existing consumers (which read `.message` / `catch (WiringError)`) are unaffected.
- `enableWasm()` failure keeps pure-TS silently (default) unless `{ require: true }`.
- `evaluateBuiltinJson` `"null"` → `null` (no decision); the runner treats it as no routing, same as pure-TS returning `null`.

## 6. Testing

- Rust: host `cargo test` on `goldenpipe-wasm` (compiles the non-wasm path clean) + `goldenpipe-core`'s existing golden-vector suite.
- TS: backend-registry unit test + Leg A (pure-TS == vectors) + Leg B (wasm == vectors), CI-only (box OOMs vitest — exit 137).
- Byte-parity is enforced by value+key-order equality against the vectors (`preserve_order` in the core keeps key order; `JSON.stringify` insertion order matches on the TS side).

## 7. Box constraints

- TS build/vitest **OOM the box** (exit 137) — TS work is **CI-only**; do not run vitest locally.
- The Rust crate links locally on NTFS `D:` (toolchain 1.94.0, `CARGO_HOME=D:\.cargo`); `cargo test`/`fmt`/`clippy` the host target on the box is fine. The wasm artifact build runs in CI.
- `benzsevern` gh account; arm auto-merge + STOP.

## 8. Graduation

SP3 graduates on: (1) Leg A + Leg B green in CI (pure-TS == wasm == core on all five vector families), (2) the `autoConfig` identity conformance fix landed with pure-TS == core, (3) both crates fmt/clippy-clean. No perf gate (waived — see §1). Outcome: the Python and TS planners are both provably locked to the single Rust reference; the program's one-source-of-truth goal is met across its two real surfaces.
