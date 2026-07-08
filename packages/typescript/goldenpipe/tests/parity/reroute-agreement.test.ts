/**
 * reroute-agreement.test.ts — proves the WASM-reroute WIRING (plannerJson.ts)
 * round-trips to pure-TS-equal output. Uses a FAKE backend whose five JSON
 * methods delegate straight to the (guard-free) pure JSON shim
 * (plannerJsonPure). No real .wasm needed.
 *
 * The point: with the fake backend registered, Resolver.resolve / Router.apply
 * take the `*ViaWasm` path (serialize -> backend -> deserialize), and the result
 * must deep-equal the pure-TS path. Because the pure shim calls the *Pure cores
 * (never the guarded public entries), there is no infinite recursion even though
 * the backend delegates back into pure-TS.
 */
import { describe, it, expect, afterEach } from "vitest";
import { setPipeWasmBackend } from "../../src/core/wasm/backend.js";
import * as pure from "../../src/core/wasm/plannerJsonPure.js";
import { Resolver } from "../../src/core/engine/resolver.js";
import { Router } from "../../src/core/engine/router.js";
import type { PlannedStage } from "../../src/core/engine/resolver.js";
import { StageRegistry } from "../../src/core/engine/registry.js";
import {
  makePipeContext,
  makePipelineConfig,
  makeStageSpec,
  makeDecision,
  stage,
  StageStatus,
  type StageInfo,
} from "../../src/core/models.js";

const fakeBackend = {
  resolveJson: pure.resolveJsonPure,
  applyDecisionJson: pure.applyDecisionJsonPure,
  evaluateBuiltinJson: pure.evaluateBuiltinJsonPure,
  autoConfigJson: pure.autoConfigJsonPure,
  skipIfFalsyJson: pure.skipIfFalsyJsonPure,
  planPipelineJson: pure.planPipelineJsonPure,
  applyScaleHintsJson: pure.applyScaleHintsJsonPure,
  bandOfJson: pure.bandOfJsonPure,
  buildRepairPlanJson: pure.buildRepairPlanJsonPure,
};

/** A trivial stage with the given wiring; `run` is never invoked in these tests. */
function mk(info: StageInfo) {
  return stage(info, async () => ({ status: StageStatus.SUCCESS }));
}

function buildRegistry(): StageRegistry {
  const reg = new StageRegistry();
  reg.register(mk({ name: "load", produces: ["df"], consumes: [] }));
  reg.register(mk({ name: "a", produces: ["scanned"], consumes: ["df"] }));
  reg.register(mk({ name: "b", produces: [], consumes: ["scanned"] }));
  reg.register(mk({ name: "c", produces: [], consumes: [] }));
  return reg;
}

/** Project to serializable fields — avoids comparing live `.stage` identity. */
function project(stages: PlannedStage[]): Array<Record<string, unknown>> {
  return stages.map((p) => ({
    name: p.name,
    use: p.spec.use,
    config: p.config,
    onError: p.spec.onError,
    skipIf: p.spec.skipIf ?? null,
  }));
}

describe("reroute wiring == pure-TS", () => {
  afterEach(() => setPipeWasmBackend(null));

  it("resolve round-trips to a pure-TS-equal plan", () => {
    const registry = buildRegistry();
    const config = makePipelineConfig({
      pipeline: "test",
      stages: [makeStageSpec("a"), makeStageSpec("b")],
    });

    // Pure-TS path (no backend).
    const purePlan = project(Resolver.resolve(config, registry).stages);

    // Rerouted path (fake backend delegating to the pure shim).
    setPipeWasmBackend(fakeBackend);
    const wasmPlan = project(Resolver.resolve(config, registry).stages);

    expect(wasmPlan).toEqual(purePlan);
    // Sanity: the reroute actually produced a non-trivial plan (load + a + b).
    expect(purePlan.map((s) => s.name)).toEqual(["load", "a", "b"]);
  });

  it("apply_decision (skip + insert) round-trips to a pure-TS-equal remaining list", () => {
    const registry = buildRegistry();
    const config = makePipelineConfig({
      pipeline: "test",
      stages: [makeStageSpec("a"), makeStageSpec("b")],
    });
    const remaining = Resolver.resolve(config, registry).stages;
    const decision = makeDecision({
      skip: ["b"],
      insert: ["c"],
      reason: "reroute test",
    });

    // Pure-TS path.
    const pureCtx = makePipeContext();
    const pureNext = project(Router.apply(decision, remaining, pureCtx, registry));

    // Rerouted path.
    setPipeWasmBackend(fakeBackend);
    const wasmCtx = makePipeContext();
    const wasmNext = project(Router.apply(decision, remaining, wasmCtx, registry));

    expect(wasmNext).toEqual(pureNext);
    expect(wasmCtx.reasoning["_router"]).toEqual(pureCtx.reasoning["_router"]);
    // Sanity: c inserted at front, b skipped.
    expect(pureNext.map((s) => s.name)).toEqual(["c", "load", "a"]);
  });
});
