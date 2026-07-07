# goldenpipe brain — TS runtime wiring (Slice C2) design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** C1 (#1548) — the pure-TS brain (`planPipeline`/`applyScaleHints`/`bandOf` + structs in `autoconfigPlanner.ts`) and its Leg A/B vector parity. This slice wires that brain into TS `run()`.

## 1. Goal

Make TS `goldenpipe.run()` plan-first auto-config (default-on), so TS users get the same brain-decided pipeline as Python. Rust brain stays the source of truth; TS conforms via the C1 vectors (decision core, already gated) + new run-level parity (profiling + wiring) here.

## 2. What the profiling parity actually requires (de-risked)

The brain needs a `PlannerInput` built from the TS `Row[]`, matching Python's `build_planner_input`. The pieces and their parity status:

- **n_rows** = `rows.length` == Python `len(df)`. Trivial.
- **columns** = `Object.keys(rows[0])` (header order) == Python `df.columns`. `parseCsv` builds row objects in header order, so this matches. Empty input (`rows.length === 0`) → zeros (mirrors Python `df is None`).
- **domain / confidence** = `detectDomainDetailed({ columns }).score`. InferMap's `detect_domain` is **byte-identical across Python/native/TS/WASM** (its own cross-surface Rust cutover) and reads **only column names** — the exact columns-only input Python passes (`SimpleNamespace(columns=list(column_names))` → `[str(c) for c in df.columns]`). Pure-TS `detectDomainDetailed` is byte-identical to `infermap-core::detect_domain`. So domain parity is already solved; C2 just wires it (`{ columns }` input, `.score`, domain-null → confidence 0).
- **null density** = per-column null count / n_rows. Deterministic. **Null-semantics subtlety (documented, load-bearing):** `parseCsv` maps an empty CSV cell to `""`, NOT null/undefined (`csv.ts:23`). Polars `read_csv` maps it to null. So for a CSV-loaded row, an empty cell counts as null in Python but not in TS. This does not affect the person pipe_parity fixtures (they have zero empty cells → both see zero nulls → agree). It is exactly why a **null-heavy pipe_parity fixture is deferred** (§8) — it would need reconciling `parseCsv` `""` with Polars null. TS `profileComplexity` defines null as `value === null || value === undefined` (an absent key reads as `undefined`); a targeted unit test covers it with explicit-null `Row[]` (not CSV-derived).

Net: the only genuinely new parity risk (null density) is unobservable in the current pipe_parity path and is covered by a unit test with controlled inputs.

## 3. Components

### 3.1 `src/core/errors.ts` (new)
```ts
export class PipeNotConfidentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PipeNotConfidentError";
  }
}
```
Mirrors Python `errors.PipeNotConfidentError`. TS-local (no goldenmatch dep).

### 3.2 `src/core/autoconfigGlue.ts` (new) — the impure host bracket, mirroring `autoconfig_glue.py`
```ts
import type { Row } from "./index.js";
import {
  planPipeline, applyScaleHints, bandOf,
  type PipeProfile, type ComplexityProfile, type PlannerInput, type PipePlan,
} from "./autoconfigPlanner.js";
import { detectDomainDetailed } from "infermap";
import { PipeNotConfidentError } from "./errors.js";
import { makePipelineConfig, makeStageSpec, type PipelineConfig } from "./models.js";
import type { StageRegistry } from "./engine/registry.js";

export const REFUSE_ROW_THRESHOLD = 100_000;

export function profileContext(rows: readonly Row[]): PipeProfile {
  if (rows.length === 0) {
    return { nRows: 0, nCols: 0, columnNames: [], dtypes: [],
             inferredDomain: null, domainConfidence: 0 };
  }
  const columnNames = Object.keys(rows[0]!);
  const det = detectDomainDetailed({ columns: columnNames });
  return {
    nRows: rows.length,
    nCols: columnNames.length,
    columnNames,
    dtypes: [],                            // dtypes unused by any rule / never emitted
    inferredDomain: det.domain,
    domainConfidence: det.domain !== null ? det.score : 0,
  };
}

export function profileComplexity(rows: readonly Row[]): ComplexityProfile {
  const nRows = rows.length;
  if (nRows === 0) return { maxNullDensity: 0, meanNullDensity: 0 };
  const columnNames = Object.keys(rows[0]!);
  if (columnNames.length === 0) return { maxNullDensity: 0, meanNullDensity: 0 };
  const fractions = columnNames.map((c) => {
    let nulls = 0;
    for (const r of rows) {
      const v = r[c];
      if (v === null || v === undefined) nulls++;
    }
    return nulls / nRows;
  });
  // Loop-max (NOT Math.max(...spread)) — the repo's `ts-no-spread-math-min-max`
  // ast-grep rule is error-severity and runs on every PR (see infer.ts for the
  // same pattern). fractions is column-count-sized so the spread would be
  // runtime-safe, but the lint fires regardless.
  let maxNullDensity = 0;
  for (const f of fractions) if (f > maxNullDensity) maxNullDensity = f;
  return {
    maxNullDensity,
    meanNullDensity: fractions.reduce((a, b) => a + b, 0) / fractions.length,
  };
}

export function buildPlannerInput(rows: readonly Row[]): PlannerInput {
  return { runtime: profileContext(rows), complexity: profileComplexity(rows) };
}

export function enforceConfidence(plan: PipePlan, runtime: PipeProfile): void {
  if (bandOf(plan.confidence) !== "red") return;
  if (runtime.nRows >= REFUSE_ROW_THRESHOLD) {
    throw new PipeNotConfidentError(
      `auto-config not confident (rule=${plan.ruleName}, confidence=${plan.confidence}) ` +
      `on ${runtime.nRows} rows; supply an explicit pipeline config or reduce the input size. ` +
      `evidence=${JSON.stringify(plan.evidence)}`,
    );
  }
  // Low confidence below the threshold: proceed on the safe default plan.
  // (console.warn parity with Python's logger.warning is optional.)
}

export function planToConfig(
  plan: PipePlan,
  available: Record<string, unknown>,
  identityOpts: Record<string, unknown>,
): PipelineConfig {
  const stages = plan.stages
    .filter((s) => s.name in available)
    .map((s) => makeStageSpec({ use: s.name, config: s.config }));
  const IDENTITY = "goldenmatch.identity_resolve";
  if (Object.keys(identityOpts).length > 0 && IDENTITY in available) {
    stages.push(makeStageSpec({ use: IDENTITY, config: identityOpts }));
  }
  return makePipelineConfig({ pipeline: "auto", stages });
}
```
Notes:
- Null density uses a **loop-max**, not `Math.max(...fractions)` — the `ts-no-spread-math-min-max` ast-grep rule is error-severity and runs on every PR (see `infer.ts` for the same loop pattern).
- `dtypes: []` — no rule reads dtypes and they never appear in any emitted output (plan/evidence/pipe_parity), so an empty array is faithful. (Python fills real Polars dtypes but they are equally unused downstream.)

### 3.3 `pipeline.ts` — `planConfig` + `run()` switch
```ts
// NEW: the brain path (pure-TS; mirrors Python _plan_config).
export function planConfig(
  rows: readonly Row[],
  registry: StageRegistry,
  identityOpts: Record<string, unknown>,
): PipelineConfig {
  const inp = buildPlannerInput(rows);
  let plan = planPipeline(inp);
  plan = applyScaleHints(plan, inp.runtime);
  enforceConfidence(plan, inp.runtime);       // may throw PipeNotConfidentError
  return planToConfig(plan, registry.listAll(), identityOpts);
}
```
In `run()`, change the config line from `computeAutoConfig(...)` to `planConfig(...)`, keeping it **before** the `Resolver.resolve` try-block so a `PipeNotConfidentError` propagates out of `run()` (rejects the promise) rather than becoming a FAILED result — matching Python (raise out of `run()`):
```ts
const config = this.config ?? planConfig([...rows], this.registry, this.identityOpts);
```
- **`computeAutoConfigPure` stays live** — the `auto_config` Leg A/B parity calls it **directly** via `autoConfigJsonPure` (`plannerJsonPure.ts`), independent of `run()`. `DEFAULT_STAGE_ORDER` (the static scan/flow/dedupe list) is unchanged. This mirrors Python keeping `_auto_config` alongside the new `_plan_config`.
- **`computeAutoConfig` (the WASM-guarded wrapper) + `autoConfigViaWasm`**: after `run()` switches to `planConfig`, `computeAutoConfig`'s only caller is gone, so it (and transitively `autoConfigViaWasm`) become orphaned exported functions. **Leave them exported** (public API surface; unused exports are not a lint failure) — do NOT delete them in this slice. Just don't claim they back the parity.
- **Pure-TS runtime** — `planConfig` calls `planPipeline` + `detectDomainDetailed` directly (both byte-identical to core), NOT the WASM backend. WASM-routed runtime is deferred (§8); it would add async + backend plumbing for zero behavioral difference.

### 3.4 `runDf` parity
`runDf(rows, config?)` (the in-memory entry the pipe_parity test drives) constructs a `Pipeline` and calls `run()`, so it inherits the brain automatically. Confirm `runDf`'s zero-config path reaches `planConfig` (it should, via `run()`).

## 4. pipe_parity rework

`scripts/emit_ts_parity_fixtures.py` currently emits from `Pipeline(config=static_config).run(source=path)` (slice-1's static-engine aim). **Re-aim it at `goldenpipe.run(path)`** (the brain default). Effect on the 3 committed fixtures:
- `single_row` (1 row) → `pathological` → `[load, scan, flow]` (drops dedupe). **Golden changes.**
- `people_dupes` / `all_unique` (person columns → domain None, low null) → `default` → `[load, scan, flow, dedupe]`. **Unchanged.**

All three are **box-emittable** (person columns → no infer_schema, no domain, and they dedupe cleanly). Regenerate `pipe_parity.json` on the box via the emitter and eyeball the single_row change. The TS `pipe-parity.test.ts` mechanism is unchanged (`runDf(parseCsv(input_csv))` now goes through the brain); its goldens update from the regenerated fixture.

The CI `ts_parity_freshness` gate re-runs the emitter and compares — so the committed fixture must match what a fresh-install emitter produces. Person fixtures have no infer_schema/entry-point dependency, so box-regeneration == CI-regeneration here.

## 5. TS unit tests (`tests/unit/autoconfig-glue.test.ts`, new)

The brain paths pipe_parity can't observe:
- **`profileContext`** — person-column rows → `inferredDomain: null`; finance-column rows with **exactly `["account_number", "currency"]`** (verified on the box: `detectDomainDetailed({columns}).score === 1.0`, well above the 0.5 confident threshold) → `inferredDomain: "finance"`, `domainConfidence === 1.0`. Use these exact columns so this test and the `planConfig` confident_schema test below cannot disagree (a weaker finance column set could score between 0 and 0.5 and land on `default`).
- **`profileComplexity`** — a `Row[]` with explicit `null`/`undefined` values → hand-computed `maxNullDensity` / `meanNullDensity`. (Covers the null-density definition directly, since pipe_parity can't.)
- **`enforceConfidence`** — a RED plan (`confidence: 0.3`) at `nRows >= 100_000` throws `PipeNotConfidentError`; below the threshold returns; green/amber returns.
- **`planConfig` confident_schema** — rows with columns `["account_number", "currency"]` (score 1.0) through `planConfig(rows, buildDefaultRegistry(), {})` → stages `[infer_schema, goldencheck.scan, goldenflow.transform, goldenmatch.dedupe]` (`buildDefaultRegistry` registers `InferSchemaStage`, so it survives `planToConfig`'s availability filter). Use ≥2 rows so `pathological` doesn't fire first.
- **`planConfig` pathological** — a single-row `Row[]` → stages `[goldencheck.scan, goldenflow.transform]` (dedupe dropped).

## 6. Testing summary

- **Box-runnable:** the Python emitter rework + `pipe_parity.json` regeneration + eyeballing (person fixtures, no infer_schema/dedupe wrinkle). Verify `goldenpipe.run(path)` on each fixture CSV produces the expected brain stages on the box.
- **CI-only:** all TS (vitest OOMs the box) — the unit tests + the reworked pipe-parity test. The `ts_parity_freshness` gate re-verifies the fixture.

## 7. Parity story (complete picture after C2)

| Layer | Gate |
|-------|------|
| Decision core (planPipeline/applyScaleHints/bandOf) | C1 vectors (Rust `golden_vectors.rs` + TS Leg A/B + Python Leg A) |
| Domain detection | InferMap cross-surface detect parity (byte-identical) |
| Null density | TS `profileComplexity` unit test (controlled inputs) |
| Run-level (brain-decided stages) | pipe_parity: Python `run()` == TS `run()` |

## 8. Non-goals / deferred

- **Null-heavy pipe_parity fixture** — needs reconciling `parseCsv` `""` vs Polars null (§2). Deferred; `profileComplexity` unit test covers the logic meanwhile.
- **WASM-routed runtime brain** — `planConfig` is pure-TS; routing `planPipeline`/detect through the WASM backend when enabled is deferred (zero behavioral difference; adds async plumbing).
- **`_last_plan` introspection surface** on the TS `Pipeline` — optional; not required for parity. (Add only if a consumer needs it.)
- Making TS the runtime source of truth — Rust stays canonical.

## 9. File touch list

- `packages/typescript/goldenpipe/src/core/errors.ts` — **new** (`PipeNotConfidentError`).
- `packages/typescript/goldenpipe/src/core/autoconfigGlue.ts` — **new** (profiling + enforce + planToConfig).
- `packages/typescript/goldenpipe/src/core/pipeline.ts` — add `planConfig`; `run()` calls it (before the resolve try); `computeAutoConfig` untouched.
- `packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py` — re-aim from static config to `goldenpipe.run()`.
- `packages/typescript/goldenpipe/tests/fixtures/pipe_parity.json` — regenerated (single_row golden changes).
- `packages/typescript/goldenpipe/tests/unit/autoconfig-glue.test.ts` — **new** (unit coverage).
- (Verify) `packages/typescript/goldenpipe/tests/parity/pipe-parity.test.ts` — mechanism unchanged; goldens update from the fixture.
