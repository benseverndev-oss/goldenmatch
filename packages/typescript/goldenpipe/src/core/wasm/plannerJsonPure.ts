/**
 * plannerJsonPure.ts — the JSON face of the pure-TS planner. The Leg A parity
 * surface: each fn CALLS the real Resolver/Router/decisions/computeAutoConfig/
 * isFalsy and serializes to goldenpipe-core's exact JSON shapes. Does NOT run
 * at pipeline runtime. TS analogue of goldenpipe/core/_planner_json.py.
 */
import {
  resolvePure, WiringError, AmbiguousProducerError, CycleError, UnknownNeedError,
  type PlannedStage,
} from "../engine/resolver.js";
import { applyPure } from "../engine/router.js";
import { isFalsy } from "../engine/runner.js";
import { severityGatePure, piiRouterPure, rowCountGatePure } from "../decisions.js";
import {
  makePipeContext,
  makePipelineConfig,
  makeStageSpec,
  makeDecision,
  type PipeContext,
  type StageInfo,
  type Stage,
  type StageSpec,
} from "../models.js";
import type { StageRegistry } from "../engine/registry.js";
import { computeAutoConfigPure } from "../pipeline.js";

interface StubStage {
  info: StageInfo;
}

class StubRegistry {
  private stages = new Map<string, StubStage>();
  add(key: string, info: StageInfo): void {
    this.stages.set(key, { info });
  }
  has(key: string): boolean {
    return this.stages.has(key);
  }
  get(key: string): Stage {
    const s = this.stages.get(key);
    if (!s) throw new Error(`Stage '${key}' not found in registry`);
    return s as unknown as Stage;
  }
  listAll(): Record<string, StageInfo> {
    const out: Record<string, StageInfo> = {};
    for (const [k, s] of this.stages) out[k] = s.info;
    return out;
  }
  asRegistry(): StageRegistry {
    return this as unknown as StageRegistry;
  }
}

function info(d: { name: string; produces: string[]; consumes: string[] }): StageInfo {
  return { name: d.name, produces: [...d.produces], consumes: [...d.consumes] };
}

function plannedToDict(p: PlannedStage): Record<string, unknown> {
  const out: Record<string, unknown> = {
    name: p.name,
    use: p.spec.use,
    config: p.config ?? {},
    on_error: p.spec.onError,
  };
  if (p.spec.skipIf !== undefined && p.spec.skipIf !== null) out.skip_if = p.spec.skipIf;
  return out;
}

export function resolveJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    config: { pipeline: string; stages: unknown[]; decisions?: string[] };
    stages: Array<{ key: string; name: string; produces: string[]; consumes: string[] }>;
  };
  const reg = new StubRegistry();
  for (const s of arg.stages) reg.add(s.key, info(s));
  const config = makePipelineConfig(arg.config as never);
  try {
    const plan = resolvePure(config, reg.asRegistry());
    return JSON.stringify({ ok: { stages: plan.stages.map(plannedToDict) } });
  } catch (e) {
    if (e instanceof WiringError) {
      return JSON.stringify({ err: { kind: "missing_producer", stage: e.stage, artifact: e.artifact } });
    }
    if (e instanceof AmbiguousProducerError) {
      return JSON.stringify({ err: { kind: "ambiguous_producer", artifact: e.artifact, producers: e.producers } });
    }
    if (e instanceof CycleError) {
      return JSON.stringify({ err: { kind: "cycle", stages: e.stages } });
    }
    if (e instanceof UnknownNeedError) {
      return JSON.stringify({ err: { kind: "unknown_need", stage: e.stage, needs: e.needs } });
    }
    // unknown `use`: registry.get threw a plain Error
    for (const raw of config.stages) {
      const use = typeof raw === "string" ? raw : (raw as StageSpec).use;
      if (!reg.has(use)) return JSON.stringify({ err: { kind: "unknown_stage", use } });
    }
    throw e;
  }
}

export function applyDecisionJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    decision: { skip?: string[]; abort?: boolean; insert?: string[]; reason?: string };
    remaining: Array<{
      name: string;
      use: string;
      config?: Record<string, unknown>;
      on_error?: string;
      skip_if?: string;
    }>;
  };
  const decision = makeDecision({
    skip: arg.decision.skip ?? [],
    abort: arg.decision.abort ?? false,
    insert: arg.decision.insert ?? [],
    reason: arg.decision.reason ?? "",
  });
  const remaining: PlannedStage[] = arg.remaining.map((r) => ({
    name: r.name,
    stage: null as unknown as Stage,
    spec: makeStageSpec({
      use: r.use,
      name: r.name,
      onError: (r.on_error ?? "continue") as StageSpec["onError"],
      ...(r.skip_if !== undefined ? { skipIf: r.skip_if } : {}),
    }),
    config: r.config ?? {},
  }));
  const reg = new StubRegistry();
  for (const name of decision.insert) reg.add(name, { name, produces: [], consumes: [] });
  const ctx: PipeContext = makePipeContext();
  const next = applyPure(decision, remaining, ctx, reg.asRegistry());
  const out: Record<string, unknown> = { remaining: next.map(plannedToDict) };
  const note = ctx.reasoning["_router"];
  if (note !== undefined) out.router_note = note;
  return JSON.stringify(out);
}

const BUILTINS: Record<string, (ctx: PipeContext) => unknown> = {
  severity_gate: severityGatePure,
  pii_router: piiRouterPure,
  row_count_gate: rowCountGatePure,
};

export function evaluateBuiltinJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    name: string;
    ctx?: { artifacts?: Record<string, unknown>; metadata?: Record<string, unknown> };
  };
  const fn = BUILTINS[arg.name];
  if (!fn) return "null";
  const ctx = makePipeContext({
    artifacts: arg.ctx?.artifacts ?? {},
    metadata: arg.ctx?.metadata ?? {},
  });
  const d = fn(ctx) as
    | { skip: string[]; abort: boolean; insert: string[]; reason: string }
    | null;
  if (d === null) return "null";
  return JSON.stringify({ skip: d.skip, abort: d.abort, insert: d.insert, reason: d.reason });
}

export function autoConfigJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    available: string[];
    identity_opts?: Record<string, unknown>;
  };
  const reg = new StubRegistry();
  for (const name of arg.available) reg.add(name, { name, produces: [], consumes: [] });
  const cfg = computeAutoConfigPure(reg.asRegistry(), arg.identity_opts ?? {});
  return JSON.stringify({
    pipeline: cfg.pipeline,
    stages: (cfg.stages as StageSpec[]).map((s) => ({
      use: s.use,
      needs: s.needs,
      on_error: s.onError,
      config: s.config,
    })),
    decisions: cfg.decisions,
  });
}

export function skipIfFalsyJsonPure(inputStr: string): string {
  return JSON.stringify(isFalsy(JSON.parse(inputStr)));
}
