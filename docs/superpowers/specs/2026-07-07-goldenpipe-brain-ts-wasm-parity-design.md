# goldenpipe brain — TS + WASM parity (Slice C1) design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** the goldenpipe-core Rust brain port (#1545) — `plan_pipeline`/`apply_scale_hints`/`band_of` + the hand-authored vectors (`plan_pipeline.json`, `apply_scale_hints.json`, `band_of.json`) already committed and gated by the Rust `golden_vectors.rs` + Python Leg A.
**Sequenced before:** Slice C2 (TS runtime wiring + profiling parity + pipe_parity rework), which is a separate spec.

## 1. Goal & scope

Port the auto-config **brain decision core** to TypeScript and add the WASM faces, so the pure-TS brain **and** the same Rust core compiled to WASM both reproduce the shared vectors — mirroring the Rust port and the Python Leg A. Rust stays the source of truth; TS conforms.

**In scope (C1):** pure-TS brain (`planPipeline`/`applyScaleHints`/`bandOf` + structs), pure JSON bridges, the 3 `goldenpipe-wasm` faces, the backend interface + loader wiring, and Leg A/Leg B parity cases.

**Explicitly deferred to C2:** wiring TS `run()` to the brain, the TS profiling-parity layer (row count / InferMap domain / null density that must match Python's Polars/InferMap byte-for-byte), `enforce_confidence`, and the `pipe_parity` rework. **TS `run()` is unchanged** in C1 (still the static engine config). The typed `*ViaWasm` runtime wrappers (like `autoConfigViaWasm`) are also C2 — Leg B parity calls the backend's `*Json` methods directly, so C1 does not need them.

## 2. Architecture

Extend the existing two-leg TS parity harness (which already covers the engine layer) with the brain:

- **Leg A** (`planner-parity.test.ts`, CI TS job) — the pure-TS bridge fns must reproduce the vectors.
- **Leg B** (`planner-wasm-parity.test.ts`, `goldenpipe_wasm` CI lane) — the Rust-core-via-WASM `*Json` backend methods must reproduce the same vectors.

Both replay the **same committed vectors** the Rust `golden_vectors.rs` and Python Leg A already meet. **TS is CI-only** (vitest OOMs the box) — the TS + wasm-Rust is written against the spec + vectors and validated in CI. `rustfmt` on the wasm `lib.rs` is runnable locally (per the #1545/#1546 lesson).

**Numeric-type de-risk:** JS has a single `number` type, so the serde_json `1 != 1.0` trap does not apply — `JSON.parse("1.0")` and `JSON.parse("1")` both yield `1`, and vitest `toEqual` compares structurally. **Key-order** is NOT separately gated on the TS side (`toEqual` is order-insensitive); acceptable because TS is a consumer, not the source of truth — the Rust `json.rs` order test remains the canonical order guard. (Order matters only when TS *emits* config back to Python, which is C2's concern.)

## 3. Pure-TS brain — `src/core/autoconfigPlanner.ts` (new)

Mirror `autoconfig_planner.py` / `planner.rs` exactly. TS internal fields may be camelCase (idiomatic; the existing engine bridge maps camel→snake, e.g. `onError`→`on_error`), but **the serialized JSON MUST use the exact snake_case vector keys** (`n_rows`, `n_cols`, `column_names`, `dtypes`, `inferred_domain`, `domain_confidence`, `max_null_density`, `mean_null_density`, `rule_name`, `_dedupe_hints`, `scale_hinted`).

```ts
export interface PipeProfile {
  nRows: number; nCols: number; columnNames: string[]; dtypes: string[];
  inferredDomain: string | null; domainConfidence: number;
}
export interface ComplexityProfile { maxNullDensity: number; meanNullDensity: number; }
export interface PlannerInput { runtime: PipeProfile; complexity: ComplexityProfile; }
export interface PlannedStage { name: string; config: Record<string, unknown>; }
export interface PipePlan {
  stages: PlannedStage[]; ruleName: string; confidence: number;
  evidence: Record<string, unknown>;
}

export const SCALE_ROUTE_MIN_ROWS = 1_000_000;
// RED_NULL_DENSITY=0.6, CONFIDENT_DOMAIN_THRESHOLD=0.5, THROUGHPUT_RECALL_TARGET=0.95,
// GREEN_THRESHOLD=0.7, AMBER_THRESHOLD=0.4

export function bandOf(confidence: number): "green" | "amber" | "red" { /* >=0.7 / >=0.4 / else */ }
export function planPipeline(inp: PlannerInput): PipePlan { /* pathological -> confident_schema -> low_confidence -> default */ }
export function applyScaleHints(plan: PipePlan, runtime: PipeProfile): PipePlan { /* >= min rows && has dedupe -> hint */ }
```

Rules mirror Python/Rust: `pathological` (nRows<=1 → `[scan, transform]`, conf 1.0); `confident_schema` (`inferredDomain!==null && domainConfidence>=0.5` → `[infer_schema{domain}, scan, transform, dedupe]`, conf=domainConfidence); `low_confidence` (`inferredDomain===null && maxNullDensity>0.6` → `[scan, transform, dedupe]`, conf 0.3); `default` (conf 0.7). `defaultEvidence` fills the six snake_case keys in order. `applyScaleHints`: below `SCALE_ROUTE_MIN_ROWS` or no `goldenmatch.dedupe` stage → return the plan unchanged (a copy is fine; TS objects are mutable, so build a NEW plan and NEW stage/config/evidence objects — never mutate the input); else the dedupe stage's config gains `{"_dedupe_hints":{"throughput":{"recall_target":0.95}}}` and evidence gains `scale_hinted:true`.

## 4. Pure JSON bridges — `src/core/wasm/plannerJsonPure.ts`

Add three string→string fns (mirroring `skipIfFalsyJsonPure`/`autoConfigJsonPure`). They parse the vector `input`, call the typed brain, and serialize to the exact vector shape (snake_case keys). A small `planToJson(plan)` helper emits `{stages:[{name,config}], rule_name, confidence, evidence}`; a `profileFromJson`/`planFromJson` parse the snake_case input.

```ts
export function planPipelineJsonPure(inputStr: string): string { /* PlannerInput -> PipePlan */ }
export function applyScaleHintsJsonPure(inputStr: string): string { /* {plan, runtime} -> PipePlan */ }
export function bandOfJsonPure(inputStr: string): string { return JSON.stringify(bandOf(JSON.parse(inputStr) as number)); }
```

`planPipelineJsonPure`: `const { runtime, complexity } = JSON.parse(inputStr)` (snake_case) → build `PlannerInput` → `planPipeline` → `planToJson`. `applyScaleHintsJsonPure`: parse `{plan, runtime}`, reconstruct typed `PipePlan` + `PipeProfile` from snake_case → `applyScaleHints` → `planToJson`.

**Name-collision caveat:** `plannerJsonPure.ts` already imports `PlannedStage` from
`../engine/resolver.js` (the ENGINE type: `{name, spec, config}`), which differs from the brain's
`PlannedStage` (`{name, config}`). Do NOT import the brain's `PlannedStage` into this module under
the same name — type the bridges on `PipePlan` only (they never need to name the brain's
`PlannedStage`), or import it under an alias.

## 5. WASM Rust faces — `goldenpipe-wasm/src/lib.rs`

Add three `#[wasm_bindgen]` faces inside the `cfg(target_arch = "wasm32")` module, delegating to the core (mirroring the existing 5):
```rust
#[wasm_bindgen]
pub fn plan_pipeline_json(input: &str) -> String { json::plan_pipeline_json(input) }
#[wasm_bindgen]
pub fn apply_scale_hints_json(input: &str) -> String { json::apply_scale_hints_json(input) }
#[wasm_bindgen]
pub fn band_of_json(input: &str) -> String { json::band_of_json(input) }
```
Run `rustfmt` on this file locally before pushing.

## 6. Backend interface + loader

- **`src/core/wasm/backend.ts`** — add to `interface PipeWasmBackend`:
  ```ts
  planPipelineJson(input: string): string;
  applyScaleHintsJson(input: string): string;
  bandOfJson(input: string): string;
  ```
- **`src/core/wasm/loader.ts`** — wire the glue (snake_case wasm exports):
  ```ts
  planPipelineJson: (s) => glue.plan_pipeline_json(s),
  applyScaleHintsJson: (s) => glue.apply_scale_hints_json(s),
  bandOfJson: (s) => glue.band_of_json(s),
  ```
  **Also extend the inline `glue` cast type** in `loader.ts` (it enumerates the current 5 exports)
  with the 3 new snake_case members — otherwise `glue.plan_pipeline_json` is a `tsc` error:
  ```ts
  plan_pipeline_json: (s: string) => string;
  apply_scale_hints_json: (s: string) => string;
  band_of_json: (s: string) => string;
  ```

## 7. Parity tests

- **`tests/parity/planner-parity.test.ts`** (Leg A) — add to the `FAMILIES` list (the actual
  identifier; a `[name, pureFn]` tuple array): `["plan_pipeline", planPipelineJsonPure]`,
  `["apply_scale_hints", applyScaleHintsJsonPure]`, `["band_of", bandOfJsonPure]` (import the three
  from `plannerJsonPure.js`).
- **`tests/parity/planner-wasm-parity.test.ts`** (Leg B) — add `plan_pipeline: b.planPipelineJson`,
  `apply_scale_hints: b.applyScaleHintsJson`, `band_of: b.bandOfJson` to the `dispatch`
  (family→fn) map, and the three family names to the replay loop
  (`["resolve", ..., "plan_pipeline", "apply_scale_hints", "band_of"]`).

Both load vectors from `../../../../rust/extensions/goldenpipe-core/tests/vectors/${name}.json` (the same committed files) and assert `toEqual`.

## 8. Box constraints & gate

- **Box CANNOT run vitest** (OOM) or `tsc` — TS + wasm-Rust written against spec + vectors, validated in CI (Leg A = TS test job; Leg B = `goldenpipe_wasm` lane). Verify TS by eye against the Python brain / vectors.
- **`rustfmt` the wasm `lib.rs` locally** (the only locally-runnable Rust check; box can't `cargo build`).
- The three vectors already exist + are Python/Rust-validated, so TS conforming is a pure CI check.

## 9. Non-goals (C2 and beyond)

- Wiring TS `run()` to the brain; the TS profiling-parity layer; `enforce_confidence`; the `pipe_parity` rework — all C2.
- Typed `*ViaWasm` runtime wrappers in `plannerJson.ts` — C2 (runtime consumption).
- Making TS the runtime path (it stays a parity-checked port; Rust is the source of truth).

## 10. File touch list

- `packages/typescript/goldenpipe/src/core/autoconfigPlanner.ts` — **new** (typed brain + logic).
- `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts` — 3 pure bridge fns + helpers.
- `packages/typescript/goldenpipe/src/core/wasm/backend.ts` — 3 interface methods.
- `packages/typescript/goldenpipe/src/core/wasm/loader.ts` — 3 glue wirings.
- `packages/rust/extensions/goldenpipe-wasm/src/lib.rs` — 3 `#[wasm_bindgen]` faces.
- `packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts` — 3 Leg A cases.
- `packages/typescript/goldenpipe/tests/parity/planner-wasm-parity.test.ts` — 3 Leg B families.
