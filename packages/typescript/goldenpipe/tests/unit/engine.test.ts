/**
 * Unit tests for the engine layer: registry, resolver, router, runner, reporter,
 * plus the public Pipeline / runStages.
 */

import { describe, it, expect } from "vitest";
import {
  StageRegistry,
  Resolver,
  WiringError,
  Router,
  Runner,
  Reporter,
  makeDecision,
  makePipeContext,
  makePipelineConfig,
  makeStageSpec,
  stage,
  runStages,
  StageStatus,
  PipeStatus,
  type Stage,
  type PipeContext,
} from "../../src/core/index.js";

function makeStage(
  name: string,
  produces: string[],
  consumes: string[],
  fn: (ctx: PipeContext) => Promise<void> | void = () => {},
): Stage {
  return stage({ name, produces, consumes }, async (ctx) => {
    await fn(ctx);
    return { status: StageStatus.SUCCESS };
  });
}

describe("StageRegistry", () => {
  it("registers and retrieves stages", () => {
    const reg = new StageRegistry();
    const s = makeStage("a", ["x"], []);
    reg.register(s);
    expect(reg.has("a")).toBe(true);
    expect(reg.get("a")).toBe(s);
    expect(reg.listAll()["a"]).toEqual({ name: "a", produces: ["x"], consumes: [] });
  });

  it("throws on unknown stage", () => {
    const reg = new StageRegistry();
    expect(() => reg.get("nope")).toThrow(/not found/);
  });
});

describe("Resolver", () => {
  it("auto-prepends load when registered and validates wiring", () => {
    const reg = new StageRegistry();
    reg.register(makeStage("load", ["df"], []));
    reg.register(makeStage("consumer", ["out"], ["df"]));
    const cfg = makePipelineConfig({ pipeline: "t", stages: ["consumer"] });
    const plan = Resolver.resolve(cfg, reg);
    expect(plan.stages.map((s) => s.name)).toEqual(["load", "consumer"]);
  });

  it("raises WiringError when a consume is unsatisfied", () => {
    const reg = new StageRegistry();
    reg.register(makeStage("consumer", ["out"], ["missing"]));
    const cfg = makePipelineConfig({ pipeline: "t", stages: ["consumer"] });
    expect(() => Resolver.resolve(cfg, reg)).toThrow(WiringError);
  });

  it("treats df as available when no load stage is registered", () => {
    const reg = new StageRegistry();
    reg.register(makeStage("consumer", ["out"], ["df"]));
    const cfg = makePipelineConfig({ pipeline: "t", stages: ["consumer"] });
    const plan = Resolver.resolve(cfg, reg);
    expect(plan.stages.map((s) => s.name)).toEqual(["consumer"]);
  });
});

describe("Router", () => {
  const reg = new StageRegistry();
  reg.register(makeStage("inserted", ["z"], []));

  it("aborts on decision.abort", () => {
    const ctx = makePipeContext();
    const remaining = [
      { name: "x", stage: makeStage("x", [], []), spec: makeStageSpec("x"), config: {} },
    ];
    const out = Router.apply(makeDecision({ abort: true, reason: "stop" }), remaining, ctx, reg);
    expect(out).toEqual([]);
    expect(ctx.reasoning["_router"]).toBe("ABORT: stop");
  });

  it("skips named stages", () => {
    const ctx = makePipeContext();
    const remaining = [
      { name: "x", stage: makeStage("x", [], []), spec: makeStageSpec("x"), config: {} },
      { name: "y", stage: makeStage("y", [], []), spec: makeStageSpec("y"), config: {} },
    ];
    const out = Router.apply(makeDecision({ skip: ["x"] }), remaining, ctx, reg);
    expect(out.map((s) => s.name)).toEqual(["y"]);
  });

  it("inserts stages at the front", () => {
    const ctx = makePipeContext();
    const remaining = [
      { name: "y", stage: makeStage("y", [], []), spec: makeStageSpec("y"), config: {} },
    ];
    const out = Router.apply(makeDecision({ insert: ["inserted"] }), remaining, ctx, reg);
    expect(out.map((s) => s.name)).toEqual(["inserted", "y"]);
  });
});

describe("Runner", () => {
  it("runs stages in order and records timing", async () => {
    const reg = new StageRegistry();
    const order: string[] = [];
    reg.register(makeStage("a", ["x"], [], () => void order.push("a")));
    reg.register(makeStage("b", ["y"], ["x"], () => void order.push("b")));
    const cfg = makePipelineConfig({ pipeline: "t", stages: ["a", "b"] });
    const plan = Resolver.resolve(cfg, reg);
    const ctx = makePipeContext();
    const results = await new Runner(reg).run(plan, ctx);
    expect(order).toEqual(["a", "b"]);
    expect(results["a"]!.status).toBe(StageStatus.SUCCESS);
    expect(ctx.timing["a"]).toBeGreaterThanOrEqual(0);
  });

  it("captures a thrown error as a FAILED stage and continues", async () => {
    const reg = new StageRegistry();
    reg.register(
      stage({ name: "boom", produces: ["x"], consumes: [] }, () => {
        throw new Error("kaboom");
      }),
    );
    reg.register(makeStage("after", ["y"], ["x"]));
    const cfg = makePipelineConfig({ pipeline: "t", stages: ["boom", "after"] });
    const plan = Resolver.resolve(cfg, reg);
    const ctx = makePipeContext();
    const results = await new Runner(reg).run(plan, ctx);
    expect(results["boom"]!.status).toBe(StageStatus.FAILED);
    expect(results["boom"]!.error).toBe("kaboom");
    expect(results["after"]!.status).toBe(StageStatus.SUCCESS);
  });

  it("aborts the run when on_error=abort", async () => {
    const reg = new StageRegistry();
    reg.register(
      stage({ name: "boom", produces: ["x"], consumes: [] }, () => {
        throw new Error("kaboom");
      }),
    );
    reg.register(makeStage("after", ["y"], ["x"]));
    const cfg = makePipelineConfig({
      pipeline: "t",
      stages: [makeStageSpec({ use: "boom", onError: "abort" }), "after"],
    });
    const plan = Resolver.resolve(cfg, reg);
    const ctx = makePipeContext();
    const results = await new Runner(reg).run(plan, ctx);
    expect(results["boom"]!.status).toBe(StageStatus.FAILED);
    expect(results["after"]).toBeUndefined();
  });

  it("skips a stage when skipIf artifact is falsy", async () => {
    const reg = new StageRegistry();
    reg.register(makeStage("a", ["x"], []));
    reg.register(makeStage("gated", ["y"], ["x"]));
    const cfg = makePipelineConfig({
      pipeline: "t",
      stages: ["a", makeStageSpec({ use: "gated", skipIf: "never_set" })],
    });
    const plan = Resolver.resolve(cfg, reg);
    const ctx = makePipeContext();
    const results = await new Runner(reg).run(plan, ctx);
    expect(results["gated"]!.status).toBe(StageStatus.SKIPPED);
  });

  it("applies a routing decision returned by a stage", async () => {
    const reg = new StageRegistry();
    reg.register(
      stage({ name: "router", produces: ["x"], consumes: [] }, () => ({
        status: StageStatus.SUCCESS,
        decision: makeDecision({ skip: ["victim"], reason: "drop it" }),
      })),
    );
    reg.register(makeStage("victim", ["y"], ["x"]));
    const cfg = makePipelineConfig({ pipeline: "t", stages: ["router", "victim"] });
    const plan = Resolver.resolve(cfg, reg);
    const ctx = makePipeContext();
    const results = await new Runner(reg).run(plan, ctx);
    expect(results["victim"]).toBeUndefined();
    expect(ctx.reasoning["_router"]).toBe("drop it");
  });
});

describe("Reporter", () => {
  it("derives SUCCESS / PARTIAL / FAILED status", () => {
    const ctx = makePipeContext();
    expect(
      Reporter.build(ctx, {
        a: { status: StageStatus.SUCCESS },
        b: { status: StageStatus.SUCCESS },
      }).status,
    ).toBe(PipeStatus.SUCCESS);

    expect(
      Reporter.build(ctx, {
        a: { status: StageStatus.SUCCESS },
        b: { status: StageStatus.FAILED, error: "x" },
      }).status,
    ).toBe(PipeStatus.PARTIAL);

    expect(
      Reporter.build(ctx, {
        a: { status: StageStatus.FAILED, error: "x" },
      }).status,
    ).toBe(PipeStatus.FAILED);

    // Skipped-only counts as success.
    expect(
      Reporter.build(ctx, { a: { status: StageStatus.SKIPPED } }).status,
    ).toBe(PipeStatus.SUCCESS);
  });

  it("collects errors and skipped lists", () => {
    const ctx = makePipeContext();
    const result = Reporter.build(ctx, {
      ok: { status: StageStatus.SUCCESS },
      bad: { status: StageStatus.FAILED, error: "boom" },
      gone: { status: StageStatus.SKIPPED },
    });
    expect(result.errors).toEqual(["bad: boom"]);
    expect(result.skipped).toEqual(["gone"]);
  });
});

describe("runStages", () => {
  it("runs supplied stages against rows (load removed)", async () => {
    const tagged = stage({ name: "tagger", produces: ["out"], consumes: ["df"] }, (ctx) => {
      ctx.artifacts["tagged"] = (ctx.df ?? []).length;
      return { status: StageStatus.SUCCESS };
    });
    const result = await runStages([tagged], [{ a: 1 }, { a: 2 }]);
    expect(result.status).toBe(PipeStatus.SUCCESS);
    expect(result.artifacts["tagged"]).toBe(2);
    expect(Object.keys(result.stages)).toEqual(["tagger"]);
  });
});
