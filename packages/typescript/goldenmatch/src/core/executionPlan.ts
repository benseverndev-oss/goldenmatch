/**
 * executionPlan.ts — the six knobs the controller-v3 planner picks.
 * Edge-safe: no `node:` imports.
 *
 * Ports goldenmatch/core/execution_plan.py. Defaults match today's
 * polars-direct path so an unset plan preserves current behavior.
 */

export type BackendName = "polars-direct" | "chunked" | "duckdb" | "ray";
export type ClusteringStrategy =
  | "in_memory"
  | "partitioned_union_find"
  | "streaming_cc";
export type SpillThreshold = "ram" | "duckdb" | "disk_per_worker" | null;

export interface ExecutionPlan {
  readonly backend: BackendName;
  readonly chunkSize: number | null;
  readonly maxWorkers: number;
  readonly pairSpillThreshold: SpillThreshold;
  readonly clusteringStrategy: ClusteringStrategy;
  readonly ruleName: string | null;
}

/** Build an ExecutionPlan with Python-matching defaults. */
export function makeExecutionPlan(p: Partial<ExecutionPlan> = {}): ExecutionPlan {
  return {
    backend: p.backend ?? "polars-direct",
    chunkSize: p.chunkSize ?? null,
    maxWorkers: p.maxWorkers ?? 4,
    pairSpillThreshold: p.pairSpillThreshold ?? null,
    clusteringStrategy: p.clusteringStrategy ?? "in_memory",
    ruleName: p.ruleName ?? null,
  };
}
