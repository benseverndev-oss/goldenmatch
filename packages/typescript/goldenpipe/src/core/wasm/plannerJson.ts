/**
 * plannerJson.ts — the serialize -> backend -> deserialize side of the five
 * planner seams. Each `*ViaWasm` fn serializes the live TS inputs to
 * goldenpipe-core's JSON shapes, calls the registered WASM backend, and
 * reconstructs the SAME rich TS objects the pure-TS planner returns (rehydrating
 * live `Stage` handles from the registry, WiringError, router notes, etc.).
 *
 * Called ONLY from the guarded public entries (Resolver.resolve, Router.apply,
 * the decision gates, computeAutoConfig, the runner's isFalsy call site) when a
 * backend is registered. The pure-core (`*Pure`) bodies never route here, so
 * there is no recursion.
 *
 * NOTE on the import cycle: resolver.ts imports `resolveViaWasm` from here, and
 * here we import the `WiringError` VALUE from resolver.ts. This is a 2-module
 * ESM value cycle, but every reference is inside a function body (never at
 * module-evaluation time), so ESM live bindings resolve it safely. The type
 * deps (PlannedStage/ExecutionPlan) use `import type` so they carry zero runtime
 * weight.
 *
 * Edge-safe: no `node:` imports.
 */

import { WiringError, AmbiguousProducerError, CycleError, UnknownNeedError } from "../engine/resolver.js";
import type { PlannedStage, ExecutionPlan } from "../engine/resolver.js";
import { makeStageSpec, makeDecision, makePipelineConfig } from "../models.js";
import type { Decision, PipeContext, PipelineConfig, StageSpec } from "../models.js";
import type { StageRegistry } from "../engine/registry.js";
import type { PipeWasmBackend } from "./backend.js";

/** Reconstruct the pure-TS error surface from goldenpipe-core's `err` payload. */
function throwFromErr(err: { kind: string; [k: string]: unknown }): never {
  if (err.kind === "missing_producer") {
    throw new WiringError(
      `Stage '${String(err.stage)}' consumes '${String(err.artifact)}' but no prior stage produces it.`,
      { stage: String(err.stage), artifact: String(err.artifact) },
    );
  }
  if (err.kind === "ambiguous_producer") {
    throw new AmbiguousProducerError(String(err.artifact), (err.producers as string[]) ?? []);
  }
  if (err.kind === "cycle") {
    throw new CycleError((err.stages as string[]) ?? []);
  }
  if (err.kind === "unknown_need") {
    throw new UnknownNeedError(String(err.stage), (err.needs as string[]) ?? []);
  }
  if (err.kind === "unknown_stage") {
    throw new Error(`Stage '${String(err.use)}' not found in registry`);
  }
  // Includes "parse" and any future kind.
  throw new Error(`goldenpipe-wasm error kind '${err.kind}': ${JSON.stringify(err)}`);
}

interface SerStage {
  name: string;
  use: string;
  config?: Record<string, unknown>;
  on_error?: string;
  skip_if?: string;
}

export function resolveViaWasm(
  config: PipelineConfig,
  registry: StageRegistry,
  b: PipeWasmBackend,
): ExecutionPlan {
  const all = registry.listAll(); // Record<name, StageInfo> — real registry keys by name, so key===name.
  const stages = Object.entries(all).map(([name, i]) => ({
    key: name,
    name: i.name,
    produces: i.produces,
    consumes: i.consumes,
  }));
  const out = JSON.parse(b.resolveJson(JSON.stringify({ config, stages }))) as
    | { ok: { stages: SerStage[] } }
    | { err: { kind: string; [k: string]: unknown } };
  if ("err" in out && out.err) throwFromErr(out.err);
  const okStages = (out as { ok: { stages: SerStage[] } }).ok.stages;
  const planned: PlannedStage[] = okStages.map((s) => ({
    name: s.name,
    stage: registry.get(s.use), // re-correlate to the live stage
    spec: makeStageSpec({
      use: s.use,
      name: s.name,
      onError: (s.on_error ?? "continue") as StageSpec["onError"],
      ...(s.skip_if !== undefined ? { skipIf: s.skip_if } : {}),
    }),
    config: s.config ?? {},
  }));
  return { stages: planned };
}

export function applyDecisionViaWasm(
  decision: Decision,
  remaining: PlannedStage[],
  ctx: PipeContext,
  registry: StageRegistry,
  b: PipeWasmBackend,
): PlannedStage[] {
  const remSer: SerStage[] = remaining.map((p) => ({
    name: p.name,
    use: p.spec.use,
    config: p.config,
    on_error: p.spec.onError,
    ...(p.spec.skipIf !== undefined ? { skip_if: p.spec.skipIf } : {}),
  }));
  const out = JSON.parse(
    b.applyDecisionJson(JSON.stringify({ decision, remaining: remSer })),
  ) as { remaining: SerStage[]; router_note?: string };
  const byName = new Map(remaining.map((p) => [p.name, p]));
  const next: PlannedStage[] = out.remaining.map((s) => {
    const orig = byName.get(s.name);
    if (orig) return orig; // kept stage: preserve the ORIGINAL live PlannedStage
    // inserted stage: rehydrate a live handle from the registry
    return {
      name: s.name,
      stage: registry.get(s.use),
      spec: makeStageSpec({
        use: s.use,
        name: s.name,
        onError: (s.on_error ?? "continue") as StageSpec["onError"],
      }),
      config: s.config ?? {},
    };
  });
  if (out.router_note !== undefined) ctx.reasoning["_router"] = out.router_note;
  return next;
}

export function evaluateBuiltinViaWasm(
  name: string,
  ctx: PipeContext,
  b: PipeWasmBackend,
): Decision | null {
  const out = JSON.parse(
    b.evaluateBuiltinJson(
      JSON.stringify({ name, ctx: { artifacts: ctx.artifacts, metadata: ctx.metadata } }),
    ),
  ) as { skip: string[]; abort: boolean; insert: string[]; reason: string } | null;
  if (out === null) return null;
  return makeDecision({ skip: out.skip, abort: out.abort, insert: out.insert, reason: out.reason });
}

export function autoConfigViaWasm(
  registry: StageRegistry,
  identityOpts: Record<string, unknown>,
  b: PipeWasmBackend,
): PipelineConfig {
  const available = Object.keys(registry.listAll());
  const out = JSON.parse(
    b.autoConfigJson(JSON.stringify({ available, identity_opts: identityOpts })),
  ) as {
    pipeline: string;
    stages: Array<{ use: string; needs?: string[]; on_error?: string; config?: Record<string, unknown> }>;
    decisions?: string[];
  };
  const stages: StageSpec[] = out.stages.map((s) =>
    makeStageSpec({
      use: s.use,
      needs: s.needs ?? [],
      onError: (s.on_error ?? "continue") as StageSpec["onError"],
      config: s.config ?? {},
    }),
  );
  return makePipelineConfig({ pipeline: out.pipeline, stages, decisions: out.decisions ?? [] });
}

export function skipIfFalsyViaWasm(value: unknown, b: PipeWasmBackend): boolean {
  return JSON.parse(b.skipIfFalsyJson(JSON.stringify(value))) as boolean;
}
