/**
 * Pipeline resolver — build an ExecutionPlan and validate wiring.
 * Port of goldenpipe/engine/resolver.py.
 *
 * Edge-safe: no `node:` imports.
 */

import type { PipelineConfig, Stage, StageSpec } from "../models.js";
import { makeStageSpec } from "../models.js";
import type { StageRegistry } from "./registry.js";
import { getPipeWasmBackend } from "../wasm/backend.js";
import { resolveViaWasm } from "../wasm/plannerJson.js";

/** A consumed artifact no stage (nor the `df` seed) produces (spec: MissingProducer).
 * Name retained for back-compat with host `catch (WiringError)` sites. */
export class WiringError extends Error {
  stage?: string;
  artifact?: string;
  /** legacy alias for {@link artifact}. */
  get missing(): string | undefined {
    return this.artifact;
  }
  constructor(message: string, extra?: { stage: string; artifact: string }) {
    super(message);
    this.name = "WiringError";
    if (extra) {
      this.stage = extra.stage;
      this.artifact = extra.artifact;
    }
  }
}

export class AmbiguousProducerError extends Error {
  constructor(public artifact: string, public producers: string[]) {
    super(`Artifact '${artifact}' has ambiguous producers: ${producers.join(", ")}`);
    this.name = "AmbiguousProducerError";
  }
}

export class CycleError extends Error {
  constructor(public stages: string[]) {
    super(`Dependency cycle among stages: ${stages.join(", ")}`);
    this.name = "CycleError";
  }
}

export class UnknownNeedError extends Error {
  constructor(public stage: string, public needs: string[]) {
    super(`Stage '${stage}' needs unknown stage(s): ${needs.join(", ")}`);
    this.name = "UnknownNeedError";
  }
}

export interface PlannedStage {
  name: string;
  stage: Stage;
  spec: StageSpec;
  config: Record<string, unknown>;
}

export interface ExecutionPlan {
  stages: PlannedStage[];
}

interface ResolveNode {
  pname: string;
  use: string;
  produces: string[];
  consumes: string[];
  needs: string[];
  planned: PlannedStage;
}

/**
 * Pure-TS core of {@link Resolver.resolve} — the dependency-DAG planner (spec §3.1),
 * guard-free so plannerJsonPure can call it without re-entering the WASM reroute guard.
 */
export function resolvePure(config: PipelineConfig, registry: StageRegistry): ExecutionPlan {
  // 1. Build the ordered node list (load prepended; registry.get throws -> unknown_stage).
  const nodes: ResolveNode[] = [];
  const hasLoad = registry.has("load");
  if (hasLoad) {
    const load = registry.get("load");
    nodes.push({
      pname: "load",
      use: "load",
      produces: [...load.info.produces],
      consumes: [],
      needs: [],
      planned: { name: "load", stage: load, spec: makeStageSpec("load"), config: {} },
    });
  }
  for (const rawSpec of config.stages) {
    const spec = makeStageSpec(rawSpec);
    const stageObj = registry.get(spec.use);
    const pname = spec.name ?? stageObj.info.name;
    nodes.push({
      pname,
      use: spec.use,
      produces: [...stageObj.info.produces],
      consumes: [...stageObj.info.consumes],
      needs: [...spec.needs],
      planned: { name: pname, stage: stageObj, spec, config: spec.config },
    });
  }
  const n = nodes.length;
  const seedDf = !hasLoad;

  const keyToIdx = (k: string): number => nodes.findIndex((nd) => nd.use === k);

  const edges = new Set<string>(); // "a>b"
  const addEdge = (a: number, b: number) => edges.add(`${a}>${b}`);
  const hasEdge = (a: number, b: number) => edges.has(`${a}>${b}`);

  // 2. needs edges (reported before missing/ambiguous).
  // (`!` on nodes[...] is compile-time only — indices are in-bounds by construction;
  //  required by tsconfig `noUncheckedIndexedAccess`. Runtime behavior is unchanged.)
  for (let i = 0; i < n; i++) {
    for (const need of nodes[i]!.needs) {
      const j = keyToIdx(need);
      if (j < 0) throw new UnknownNeedError(nodes[i]!.pname, [need]);
      addEdge(j, i); // self-edge -> Cycle in step 4
    }
  }

  // 3. Guarded sole-producer edges. First violation (by index, then consumes order) wins.
  const producedBefore = (i: number, x: string): boolean => {
    if (seedDf && x === "df") return true;
    for (let j = 0; j < i; j++) if (nodes[j]!.produces.includes(x)) return true;
    return false;
  };
  for (let i = 0; i < n; i++) {
    for (const dep of nodes[i]!.consumes) {
      if (producedBefore(i, dep)) continue;
      const later: number[] = [];
      for (let j = i + 1; j < n; j++) if (nodes[j]!.produces.includes(dep)) later.push(j);
      if (later.length === 0) {
        throw new WiringError(
          `Stage '${nodes[i]!.pname}' consumes '${dep}' but no prior stage produces it.`,
          { stage: nodes[i]!.pname, artifact: dep },
        );
      } else if (later.length === 1) {
        addEdge(later[0]!, i);
      } else {
        // pinned by ANY must-precede edge (needs OR an earlier-consumes sole-producer edge):
        // exactly one -> deterministic binding; else AmbiguousProducer.
        const pinned = later.filter((j) => hasEdge(j, i)).length;
        if (pinned !== 1) {
          throw new AmbiguousProducerError(dep, later.map((j) => nodes[j]!.use));
        }
      }
    }
  }

  // 4. Stable Kahn topo-sort keyed by config index (min-heap emulated by sorted array).
  const indeg = new Array(n).fill(0);
  const adj: number[][] = Array.from({ length: n }, () => []);
  for (const e of edges) {
    const [a, b] = e.split(">").map(Number) as [number, number];
    if (a === b) {
      indeg[b] += 1; // self-edge -> stuck node, reported by the ascending-index fall-through
      continue;
    }
    adj[a]!.push(b);
    indeg[b] += 1;
  }
  const ready: number[] = [];
  for (let i = 0; i < n; i++) if (indeg[i] === 0) ready.push(i);
  ready.sort((x, y) => x - y);
  const order: number[] = [];
  while (ready.length > 0) {
    const u = ready.shift() as number; // smallest config index
    order.push(u);
    for (const v of adj[u]!) {
      indeg[v] -= 1;
      if (indeg[v] === 0) {
        let lo = 0;
        while (lo < ready.length && ready[lo]! < v) lo++;
        ready.splice(lo, 0, v);
      }
    }
  }
  if (order.length !== n) {
    const cyc: string[] = [];
    for (let i = 0; i < n; i++) if (indeg[i] > 0) cyc.push(nodes[i]!.pname);
    throw new CycleError(cyc);
  }

  // 5. Emit in sorted order.
  return { stages: order.map((i) => nodes[i]!.planned) };
}

export const Resolver = {
  /**
   * Resolve a config + registry into an ordered ExecutionPlan. Auto-prepends
   * the built-in `load` stage when available and validates that every stage's
   * `consumes` is produced by an earlier stage.
   *
   * Routes through the registered WASM planner backend when one is enabled;
   * otherwise runs the pure-TS core.
   */
  resolve(config: PipelineConfig, registry: StageRegistry): ExecutionPlan {
    const b = getPipeWasmBackend();
    if (b) return resolveViaWasm(config, registry, b);
    return resolvePure(config, registry);
  },
};
