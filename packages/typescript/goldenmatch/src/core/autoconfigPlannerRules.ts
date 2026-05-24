/**
 * autoconfigPlannerRules.ts — concrete planner rules for controller v3.
 * Edge-safe: no `node:` imports.
 *
 * Ports goldenmatch/core/autoconfig_planner_rules.py. Decides the execution
 * plan (backend + tuning knobs). Distinct from autoconfigRules.ts which owns
 * the HeuristicRefitPolicy rules that mutate the config during iteration.
 *
 * Ray note: in the edge-safe TS core ray is never available (no Polars / no
 * ray binding) and the GOLDENMATCH_ENABLE_DISTRIBUTED_RAY soft-revert gate is
 * Node-only. So rule_ray's predicate is always false here and 50M+ inputs fall
 * through to rule_duckdb — identical to Python's "fail closed" behaviour when
 * ray isn't installed / the gate is off.
 */

import type { PlannerRule } from "./autoconfigPlanner.js";
import { makeExecutionPlan } from "./executionPlan.js";

// ── Rule 2/3/4/5 thresholds (mirror the Python module constants) ────────────
export const SIMPLE_PLAN_MAX_ROWS = 100_000;
export const SIMPLE_PLAN_MAX_PAIRS = 50_000_000;
export const FAST_BOX_MIN_RAM_GB = 32.0;
export const CHUNKED_MIN_PAIRS = SIMPLE_PLAN_MAX_PAIRS; // 50M
export const CHUNKED_MAX_PAIRS = 5_000_000_000; // 5B
export const CHUNKED_MIN_RAM_GB = 16.0;
export const CHUNKED_TARGET_RAM_USE_FRACTION = 0.6;
const CHUNKED_BYTES_PER_ROW = 1024;
export const RAY_MIN_ROWS = 50_000_000;
export const DUCKDB_MIN_PAIRS = 5_000_000_000; // 5B
export const DUCKDB_MAX_RAM_GB = 16.0;
export const DUCKDB_MAX_WORKERS = 8;

/**
 * Pick chunk_size targeting ~60% of available RAM. Estimated bytes/row is a
 * tuning lever; result clamped to [10_000, 1_000_000]. Mirrors Python
 * ``auto_chunk_size``.
 */
export function autoChunkSize(nRowsFull: number, availableRamGb: number): number {
  const estimatedGb = (nRowsFull * CHUNKED_BYTES_PER_ROW) / 1024 ** 3;
  let targetChunks = Math.ceil(
    estimatedGb / Math.max(availableRamGb * CHUNKED_TARGET_RAM_USE_FRACTION, 0.001),
  );
  targetChunks = Math.max(targetChunks, 1);
  const chunk = Math.floor(nRowsFull / targetChunks);
  return Math.max(10_000, Math.min(1_000_000, chunk));
}

// ── Rule 7: explicit user override (MUST be first) ──────────────────────────
const ruleUserOverride: PlannerRule = {
  name: "plan_user_override",
  predicate: (_p, _r, _n, ctx) =>
    ctx.userBackend !== undefined &&
    ctx.userBackend !== null &&
    ctx.userBackend !== "",
  action: (_p, runtime, nRowsFull, ctx) => {
    const userBackend = (ctx.userBackend ?? "polars-direct") as
      | "polars-direct"
      | "chunked"
      | "duckdb"
      | "ray";
    const chunk =
      userBackend === "chunked"
        ? autoChunkSize(nRowsFull, runtime.availableRamGb)
        : null;
    return makeExecutionPlan({
      backend: userBackend,
      chunkSize: chunk,
      maxWorkers: Math.min(16, runtime.cpuCount),
      clusteringStrategy: "in_memory",
      ruleName: "plan_user_override",
    });
  },
};

// ── Rule 1: pathological inputs (n_rows <= 1) ───────────────────────────────
const rulePathological: PlannerRule = {
  name: "plan_pathological",
  predicate: (_p, _r, nRowsFull) => nRowsFull <= 1,
  action: () =>
    makeExecutionPlan({
      backend: "polars-direct",
      maxWorkers: 1,
      ruleName: "plan_pathological",
    }),
};

// ── Rule 2: simple plan ─────────────────────────────────────────────────────
const ruleSimplePlan: PlannerRule = {
  name: "plan_selected_simple",
  predicate: (profile, _r, nRowsFull) =>
    nRowsFull < SIMPLE_PLAN_MAX_ROWS &&
    profile.blocking.totalComparisons < SIMPLE_PLAN_MAX_PAIRS,
  action: (_p, runtime) =>
    makeExecutionPlan({
      backend: "polars-direct",
      chunkSize: null,
      maxWorkers: Math.min(4, runtime.cpuCount),
      pairSpillThreshold: null,
      clusteringStrategy: "in_memory",
      ruleName: "plan_selected_simple",
    }),
};

// ── Rule 3: fast-box plan ───────────────────────────────────────────────────
const ruleFastBox: PlannerRule = {
  name: "plan_selected_fast_box",
  predicate: (profile, runtime, nRowsFull) =>
    nRowsFull >= SIMPLE_PLAN_MAX_ROWS &&
    profile.blocking.totalComparisons < SIMPLE_PLAN_MAX_PAIRS &&
    runtime.availableRamGb >= FAST_BOX_MIN_RAM_GB,
  action: (_p, runtime) =>
    makeExecutionPlan({
      backend: "polars-direct",
      maxWorkers: Math.min(16, runtime.cpuCount),
      clusteringStrategy: "in_memory",
      ruleName: "plan_selected_fast_box",
    }),
};

// ── Rule 4: chunked plan ────────────────────────────────────────────────────
const ruleChunked: PlannerRule = {
  name: "plan_selected_chunked",
  predicate: (profile, runtime) => {
    const pairs = profile.blocking.totalComparisons;
    return (
      pairs >= CHUNKED_MIN_PAIRS &&
      pairs < CHUNKED_MAX_PAIRS &&
      runtime.availableRamGb >= CHUNKED_MIN_RAM_GB
    );
  },
  action: (_p, runtime, nRowsFull) =>
    makeExecutionPlan({
      backend: "chunked",
      chunkSize: autoChunkSize(nRowsFull, runtime.availableRamGb),
      maxWorkers: Math.min(16, runtime.cpuCount),
      pairSpillThreshold: "ram",
      clusteringStrategy: "in_memory",
      ruleName: "plan_selected_chunked",
    }),
};

// ── Rule 6: Ray escape hatch (always fails closed in edge-safe TS) ──────────
const ruleRay: PlannerRule = {
  name: "plan_selected_ray",
  // Edge-safe core: ray is never importable and the env gate is Node-only, so
  // this predicate is always false (matches Python's fail-closed fallthrough).
  predicate: () => false,
  action: (_p, runtime) =>
    makeExecutionPlan({
      backend: "ray",
      maxWorkers: runtime.cpuCount,
      pairSpillThreshold: "disk_per_worker",
      clusteringStrategy: "streaming_cc",
      ruleName: "plan_selected_ray",
    }),
};

// ── Rule 5: DuckDB out-of-core regime (catch-all) ───────────────────────────
const ruleDuckdb: PlannerRule = {
  name: "plan_selected_duckdb",
  predicate: (profile, runtime) =>
    profile.blocking.totalComparisons >= DUCKDB_MIN_PAIRS ||
    runtime.availableRamGb < DUCKDB_MAX_RAM_GB,
  action: (_p, runtime) =>
    makeExecutionPlan({
      backend: "duckdb",
      maxWorkers: Math.min(DUCKDB_MAX_WORKERS, runtime.cpuCount),
      pairSpillThreshold: "duckdb",
      clusteringStrategy: "partitioned_union_find",
      ruleName: "plan_selected_duckdb",
    }),
};

/**
 * Default planner rule registry. Order matters (see Python module): user
 * override first, pathological before simple, ray before duckdb so 50M+ gets
 * first crack at ray then falls through.
 */
export const DEFAULT_PLANNER_RULES: readonly PlannerRule[] = [
  ruleUserOverride,
  rulePathological,
  ruleSimplePlan,
  ruleFastBox,
  ruleChunked,
  ruleRay,
  ruleDuckdb,
];
