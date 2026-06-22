/**
 * autoconfigWasm.ts — synchronous, edge-safe loader for the shared autoconfig
 * decision core (the `goldenmatch-autoconfig-core` Rust crate, compiled to wasm).
 *
 * This is the SAME core the Python `goldenmatch-native` wheel calls, so the
 * planner (Layer 1) and classifier (Layer 2) decisions are byte-identical across
 * Python / Rust / TS — proven by the shared golden vectors
 * (`tests/parity/autoconfig-core.parity.test.ts`).
 *
 * Edge-safe: no `node:*` imports. The wasm is inlined as base64 and instantiated
 * synchronously via wasm-bindgen's `initSync`, so the public API stays sync and
 * works in browsers / Workers / edge runtimes (no `fs`, no `fetch`).
 *
 * The core speaks snake_case serde JSON; this module exposes camelCase TS types
 * and adapts both ways at the boundary.
 */
import {
  initSync,
  autoconfig_decide_plan,
  autoconfig_classify_columns,
  autoconfig_extrapolate_pair_count,
  autoconfig_sparse_match_floor,
} from "./_wasm/autoconfigWasmBindings.js";
import { AUTOCONFIG_WASM_BASE64 } from "./_wasm/autoconfigWasmBytes.js";
import type {
  BackendName,
  ClusteringStrategy,
  ExecutionPlan,
  SpillThreshold,
} from "./executionPlan.js";
import {
  setAutoconfigWasmBackend,
  disableAutoconfigWasm,
} from "./autoconfigWasmBackend.js";

// ---------------------------------------------------------------------------
// Public types (camelCase mirror of the core's serde shapes)
// ---------------------------------------------------------------------------

/** Runtime environment probe — what the box can offer. */
export interface RuntimeEnv {
  readonly availableRamGb: number;
  readonly cpuCount: number;
  readonly diskFreeGb: number;
}

/** Backend capability flags the planner reasons over. */
export interface PlannerCaps {
  /** Native bucket kernel present (always false in TS — no Polars/native). */
  readonly bucketAvailable: boolean;
  readonly rayAvailable: boolean;
  readonly rayAutoSelect: boolean;
  /** User-forced backend override, or null for auto. */
  readonly userBackend: BackendName | null;
}

/** Layer-1 planner input. */
export interface PlannerInput {
  readonly nRowsFull: number;
  readonly estimatedPairCount: number;
  readonly runtime: RuntimeEnv;
  readonly caps: PlannerCaps;
}

/**
 * The core's 13-value column vocabulary. NOTE: this diverges from the TS
 * profiler's hand-written `ColumnType` (which uses `"id"`/`"text"` and lacks
 * `address`/`description`). Reconciling the profiler onto this vocabulary is the
 * E3 reroute; this loader exposes the core's vocabulary verbatim.
 */
export type CoreColumnType =
  | "email"
  | "name"
  | "phone"
  | "zip"
  | "address"
  | "geo"
  | "identifier"
  | "description"
  | "numeric"
  | "date"
  | "string"
  | "year"
  | "multi_name";

/** Layer-2 classifier input — per-column stats. */
export interface CoreColumnStats {
  readonly name: string;
  readonly dtype: string;
  readonly sampleValues: readonly string[];
  readonly nullRate: number;
  readonly cardinalityRatio: number;
  readonly avgLen: number;
}

/** Layer-2 classifier output — the core's lean per-column profile. */
export interface CoreColumnProfile {
  readonly name: string;
  readonly dtype: string;
  readonly colType: CoreColumnType;
  readonly confidence: number;
  readonly nullRate: number;
  readonly cardinalityRatio: number;
  readonly avgLen: number;
  readonly needsLlmEscalation: boolean;
}

/**
 * S1 pair-count extrapolation input — a blocking-profile summary measured on a
 * sample, plus the sample/full row counts. `chao1F1`/`chao1F2` are null when the
 * surface's measurement didn't count them (then n_blocks uses a linear fallback).
 * The TS profiler doesn't yet measure them, so it passes null and inherits the
 * ratio**2 pair-count fix immediately; the n_blocks Chao1 refinement lands when
 * the TS profiler gains the counts.
 */
export interface ExtrapolationInput {
  readonly totalComparisons: number;
  readonly nBlocks: number;
  readonly singletonBlockCount: number;
  readonly chao1F1: number | null;
  readonly chao1F2: number | null;
  readonly nRowsSample: number;
  readonly nRowsFull: number;
}

/** S1 extrapolation output — the corrected full-data blocking signal. */
export interface ExtrapolationOutput {
  readonly nBlocks: number;
  readonly totalComparisons: number;
  readonly singletonBlockCount: number;
}

// ---------------------------------------------------------------------------
// wasm init (lazy, once)
// ---------------------------------------------------------------------------

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  // atob is available in browsers, Workers, and Node >= 18 — edge-safe.
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(AUTOCONFIG_WASM_BASE64) });
  initialized = true;
}

// ---------------------------------------------------------------------------
// snake_case <-> camelCase adapters (the core's serde JSON <-> TS types)
// ---------------------------------------------------------------------------

interface PlannerInputJson {
  n_rows_full: number;
  estimated_pair_count: number;
  runtime: { available_ram_gb: number; cpu_count: number; disk_free_gb: number };
  caps: {
    bucket_available: boolean;
    ray_available: boolean;
    ray_auto_select: boolean;
    user_backend: BackendName | null;
  };
}

interface ExecutionPlanJson {
  backend: BackendName;
  chunk_size: number | null;
  max_workers: number;
  pair_spill_threshold: SpillThreshold;
  clustering_strategy: ClusteringStrategy;
  rule_name: string | null;
}

interface ColumnStatsJson {
  name: string;
  dtype: string;
  sample_values: readonly string[];
  null_rate: number;
  cardinality_ratio: number;
  avg_len: number;
}

interface ColumnProfileJson {
  name: string;
  dtype: string;
  col_type: CoreColumnType;
  confidence: number;
  null_rate: number;
  cardinality_ratio: number;
  avg_len: number;
  needs_llm_escalation: boolean;
}

function plannerInputToJson(input: PlannerInput): PlannerInputJson {
  return {
    n_rows_full: input.nRowsFull,
    estimated_pair_count: input.estimatedPairCount,
    runtime: {
      available_ram_gb: input.runtime.availableRamGb,
      cpu_count: input.runtime.cpuCount,
      disk_free_gb: input.runtime.diskFreeGb,
    },
    caps: {
      bucket_available: input.caps.bucketAvailable,
      ray_available: input.caps.rayAvailable,
      ray_auto_select: input.caps.rayAutoSelect,
      user_backend: input.caps.userBackend,
    },
  };
}

function executionPlanFromJson(j: ExecutionPlanJson): ExecutionPlan {
  return {
    backend: j.backend,
    chunkSize: j.chunk_size,
    maxWorkers: j.max_workers,
    pairSpillThreshold: j.pair_spill_threshold,
    clusteringStrategy: j.clustering_strategy,
    ruleName: j.rule_name,
  };
}

function columnStatsToJson(c: CoreColumnStats): ColumnStatsJson {
  return {
    name: c.name,
    dtype: c.dtype,
    sample_values: c.sampleValues,
    null_rate: c.nullRate,
    cardinality_ratio: c.cardinalityRatio,
    avg_len: c.avgLen,
  };
}

function columnProfileFromJson(j: ColumnProfileJson): CoreColumnProfile {
  return {
    name: j.name,
    dtype: j.dtype,
    colType: j.col_type,
    confidence: j.confidence,
    nullRate: j.null_rate,
    cardinalityRatio: j.cardinality_ratio,
    avgLen: j.avg_len,
    needsLlmEscalation: j.needs_llm_escalation,
  };
}

interface ExtrapolationInputJson {
  total_comparisons: number;
  n_blocks: number;
  singleton_block_count: number;
  chao1_f1: number | null;
  chao1_f2: number | null;
  n_rows_sample: number;
  n_rows_full: number;
}

interface ExtrapolationOutputJson {
  n_blocks: number;
  total_comparisons: number;
  singleton_block_count: number;
}

function extrapolationInputToJson(i: ExtrapolationInput): ExtrapolationInputJson {
  return {
    total_comparisons: i.totalComparisons,
    n_blocks: i.nBlocks,
    singleton_block_count: i.singletonBlockCount,
    chao1_f1: i.chao1F1,
    chao1_f2: i.chao1F2,
    n_rows_sample: i.nRowsSample,
    n_rows_full: i.nRowsFull,
  };
}

function extrapolationOutputFromJson(
  j: ExtrapolationOutputJson,
): ExtrapolationOutput {
  return {
    nBlocks: j.n_blocks,
    totalComparisons: j.total_comparisons,
    singletonBlockCount: j.singleton_block_count,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Layer 1 — pick the execution plan from dataset size + runtime + capabilities.
 * Routes through the shared wasm core (byte-parity with Python/Rust).
 */
export function decidePlan(input: PlannerInput): ExecutionPlan {
  ensureInit();
  const out = autoconfig_decide_plan(JSON.stringify(plannerInputToJson(input)));
  return executionPlanFromJson(JSON.parse(out) as ExecutionPlanJson);
}

/**
 * Layer 2 — classify columns from per-column stats. Routes through the shared
 * wasm core (byte-parity with Python/Rust).
 */
export function classifyColumns(
  cols: readonly CoreColumnStats[],
): CoreColumnProfile[] {
  ensureInit();
  const json = JSON.stringify(cols.map(columnStatsToJson));
  const out = autoconfig_classify_columns(json);
  return (JSON.parse(out) as ColumnProfileJson[]).map(columnProfileFromJson);
}

/**
 * S1 — project a sample's blocking signal to full-data scale. Pairs scale by
 * ratio**2 (the corrected, scale-invariant estimate); routes through the shared
 * wasm core (byte-parity with Python/Rust).
 */
export function extrapolatePairCount(
  input: ExtrapolationInput,
): ExtrapolationOutput {
  ensureInit();
  const out = autoconfig_extrapolate_pair_count(
    JSON.stringify(extrapolationInputToJson(input)),
  );
  return extrapolationOutputFromJson(JSON.parse(out) as ExtrapolationOutputJson);
}

/**
 * Escape hatches for the parity harness: call the core with the raw serde JSON
 * string (snake_case in, snake_case out), bypassing the camelCase adapters so
 * the test compares wasm output against the golden vectors verbatim.
 */
export function decidePlanRawJson(inputJson: string): string {
  ensureInit();
  return autoconfig_decide_plan(inputJson);
}

export function classifyColumnsRawJson(colsJson: string): string {
  ensureInit();
  return autoconfig_classify_columns(colsJson);
}

export function extrapolatePairCountRawJson(inputJson: string): string {
  ensureInit();
  return autoconfig_extrapolate_pair_count(inputJson);
}

/**
 * S2b — adaptive sparse-match floor: `min(50, estimatedPairs / 100)`. Routes
 * through the shared wasm core (byte-parity with Python/Rust).
 */
export function sparseMatchFloor(estimatedPairs: number): number {
  ensureInit();
  const out = autoconfig_sparse_match_floor(
    JSON.stringify({ estimated_pairs: estimatedPairs }),
  );
  return (JSON.parse(out) as { floor: number }).floor;
}

export function sparseMatchFloorRawJson(inputJson: string): string {
  ensureInit();
  return autoconfig_sparse_match_floor(inputJson);
}

// ---------------------------------------------------------------------------
// Opt-in enable: register this wasm core as the backend for the always-on
// planner/classifier. Importing THIS module is what pays the ~1.7 MB wasm cost
// (it statically embeds the base64); the main `goldenmatch/core` graph only
// touches the lean registry, so default bundles carry no wasm. Sync because the
// bytes are inlined + `initSync` is synchronous (no async enable needed, unlike
// the runtime-loaded score-wasm backend).
// ---------------------------------------------------------------------------

/**
 * Enable the shared wasm decision core for `applyPlannerRules` (and, once it's
 * rerouted, the column classifier). After this call, auto-config planning is
 * byte-parity with Python/Rust. Pure-TS stays the default until this is called;
 * `disableAutoconfigWasm()` reverts.
 */
export function enableAutoconfigWasm(): void {
  ensureInit();
  setAutoconfigWasmBackend({ decidePlan, classifyColumns });
}

export { disableAutoconfigWasm };
