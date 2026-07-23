/**
 * sensitivity.ts — Parameter sweep engine for GoldenMatch.
 * Edge-safe: no Node.js imports, pure TypeScript only.
 *
 * Ports goldenmatch/core/sensitivity.py.
 */

import type { Row, GoldenMatchConfig, MatchkeyConfig } from "./types.js";
import { getMatchkeys, makeBlockingConfig } from "./types.js";
import { runDedupePipeline } from "./pipeline.js";
import { compareClusters } from "./compare-clusters.js";
import type { CCMSResult } from "./compare-clusters.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SweepParam {
  /** Dot-path into the config, e.g. "threshold", "blocking.maxBlockSize". */
  readonly path: string;
  readonly values: readonly unknown[];
}

export interface SweepPoint {
  readonly params: Readonly<Record<string, unknown>>;
  readonly stats: Readonly<Record<string, number>>;
  readonly twi?: number;
  readonly error?: string;
}

export interface SensitivityResult {
  readonly baseline: SweepPoint;
  readonly points: readonly SweepPoint[];
  readonly stable: boolean;
}

// ---------------------------------------------------------------------------
// Dot-path config override
// ---------------------------------------------------------------------------

/** Set a nested property by dot-path, returning a new object (shallow-cloned chain). */
function setPath(
  root: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  // Simple dot path; array indices via [n] not supported in this edge-safe port
  const parts = path.split(".").filter((p) => p.length > 0);
  if (parts.length === 0) return root;
  const clone: Record<string, unknown> = { ...root };
  let cursor: Record<string, unknown> = clone;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]!;
    const child = cursor[key];
    const childObj =
      child !== null && typeof child === "object" && !Array.isArray(child)
        ? { ...(child as Record<string, unknown>) }
        : {};
    cursor[key] = childObj;
    cursor = childObj;
  }
  cursor[parts[parts.length - 1]!] = value;
  return clone;
}

// ---------------------------------------------------------------------------
// Stats extraction
// ---------------------------------------------------------------------------

function statsFrom(result: Awaited<ReturnType<typeof runDedupePipeline>>): Record<string, number> {
  return {
    totalRecords: result.stats.totalRecords,
    totalClusters: result.stats.totalClusters,
    matchedRecords: result.stats.matchedRecords,
    uniqueRecords: result.stats.uniqueRecords,
    matchRate: result.stats.matchRate,
    scoredPairs: result.scoredPairs.length,
  };
}

// ---------------------------------------------------------------------------
// Cartesian product of sweep values
// ---------------------------------------------------------------------------

function cartesianPoints(
  params: readonly SweepParam[],
): Readonly<Record<string, unknown>>[] {
  if (params.length === 0) return [];
  let acc: Record<string, unknown>[] = [{}];
  for (const p of params) {
    const next: Record<string, unknown>[] = [];
    for (const base of acc) {
      for (const v of p.values) {
        next.push({ ...base, [p.path]: v });
      }
    }
    acc = next;
  }
  return acc;
}

// ---------------------------------------------------------------------------
// runSensitivity
// ---------------------------------------------------------------------------

/**
 * Run a parameter sweep.
 *
 * Each point in the Cartesian product of `params` is applied to
 * `baselineConfig`, the dedupe pipeline runs, and the resulting clusters are
 * compared against the baseline via CCMS. A `stable` flag is set when every
 * point's TWI is within 0.05 of 1.0.
 *
 * Per-point errors are caught and stored on the point so that partial
 * results are preserved.
 */
export async function runSensitivity(
  rows: readonly Row[],
  baselineConfig: GoldenMatchConfig,
  params: readonly SweepParam[],
): Promise<SensitivityResult> {
  // Baseline run
  const baselineRun = await runDedupePipeline(rows, baselineConfig);
  const baseline: SweepPoint = {
    params: {},
    stats: statsFrom(baselineRun),
    twi: 1.0,
  };

  const points: SweepPoint[] = [];
  const combos = cartesianPoints(params);

  let stable = true;
  for (const combo of combos) {
    let cfg: GoldenMatchConfig = baselineConfig;
    for (const [path, value] of Object.entries(combo)) {
      cfg = setPath(
        cfg as Record<string, unknown>,
        path,
        value,
      ) as GoldenMatchConfig;
    }

    try {
      const runResult = await runDedupePipeline(rows, cfg);
      let twi: number | undefined;
      try {
        twi = compareClusters(baselineRun.clusters, runResult.clusters).twi;
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(
          `TWI comparison failed for sweep point ${JSON.stringify(combo)}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
        twi = undefined;
      }
      if (twi === undefined || Math.abs(1 - twi) > 0.05) stable = false;
      points.push({
        params: combo,
        stats: statsFrom(runResult),
        ...(twi !== undefined ? { twi } : {}),
      });
    } catch (err) {
      stable = false;
      points.push({
        params: combo,
        stats: {},
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return { baseline, points, stable };
}

// ---------------------------------------------------------------------------
// stabilityReport
// ---------------------------------------------------------------------------

/** Render a human-readable stability report for a sensitivity result. */
export function stabilityReport(result: SensitivityResult): string {
  const lines: string[] = [];
  lines.push("Sensitivity sweep:");
  lines.push(`  Baseline: ${JSON.stringify(result.baseline.stats)}`);
  lines.push(`  Points:   ${result.points.length}`);
  lines.push(`  Stable:   ${result.stable ? "yes" : "no"}`);
  for (const p of result.points) {
    const twiStr = p.twi !== undefined ? p.twi.toFixed(4) : "n/a";
    const errStr = p.error !== undefined ? ` error=${p.error}` : "";
    lines.push(
      `  - params=${JSON.stringify(p.params)} twi=${twiStr} clusters=${
        p.stats["totalClusters"] ?? "?"
      }${errStr}`,
    );
  }
  return lines.join("\n");
}

// ===========================================================================
// Python-faithful sweep engine (ports goldenmatch/core/sensitivity.py)
// ===========================================================================
//
// The types + engine above are the original TS design (Cartesian sweep + a
// `stable` flag). The block below is a FAITHFUL port of Python's
// `run_sensitivity` / `SensitivityResult.stability_report`, used by the MCP
// `sensitivity` tool so its wire shape matches Python exactly. Each parameter
// is swept INDEPENDENTLY across a start:stop:step range and every run is
// compared against ONE baseline clustering via CCMS; per-point errors are
// caught so partial results survive.

const SUPPORTED_FIELDS = new Set<string>([
  "threshold",
  "blocking.max_block_size",
  // matchkey.<name>.threshold handled dynamically
]);

/** Definition of a parameter to sweep. Mirrors Python `SweepParam`. */
export interface SweepSpec {
  readonly field: string;
  readonly start: number;
  readonly stop: number;
  readonly step: number;
}

/** Result of a single sweep value. Mirrors Python `SweepPoint`. */
export interface SweepPointResult {
  readonly paramValue: number;
  readonly comparison: CCMSResult;
}

/** Result of sweeping one parameter across a range. Mirrors Python `SensitivityResult`. */
export interface SweepResult {
  readonly param: SweepSpec;
  readonly baselineValue: number;
  readonly points: readonly SweepPointResult[];
}

/** Python round(x, 4). */
function roundN(x: number, digits: number): number {
  const f = Math.pow(10, digits);
  return Math.round(x * f) / f;
}

/** Validate that a sweep field is supported and exists in config. */
function validateField(field: string, config: GoldenMatchConfig): void {
  if (SUPPORTED_FIELDS.has(field)) return;
  if (field.startsWith("matchkey.") && field.endsWith(".threshold")) {
    const name = field.split(".")[1];
    const matchkeys = getMatchkeys(config);
    if (!matchkeys.some((mk) => mk.name === name)) {
      const available = matchkeys.map((mk) => mk.name);
      throw new Error(
        `Matchkey '${name}' not found in config. Available: ${JSON.stringify(available)}`,
      );
    }
    return;
  }
  throw new Error(
    `Unsupported sweep field '${field}'. ` +
      `Supported: ${JSON.stringify([...SUPPORTED_FIELDS].sort())} or 'matchkey.<name>.threshold'`,
  );
}

/** Get the current value of a sweep field from the config. */
function getCurrentValue(field: string, config: GoldenMatchConfig): number {
  if (field === "threshold") {
    const fuzzy = getMatchkeys(config).filter(
      (mk) => (mk as { threshold?: number }).threshold !== undefined,
    );
    const first = fuzzy[0] as { threshold?: number } | undefined;
    if (first !== undefined && first.threshold !== undefined) return first.threshold;
    return 0.85; // default
  }
  if (field === "blocking.max_block_size") {
    if (config.blocking) return config.blocking.maxBlockSize;
    return 5000.0;
  }
  if (field.startsWith("matchkey.") && field.endsWith(".threshold")) {
    const name = field.split(".")[1];
    for (const mk of getMatchkeys(config)) {
      if (mk.name === name) {
        const t = (mk as { threshold?: number }).threshold;
        return t !== undefined ? t : 0.85;
      }
    }
  }
  throw new Error(`Cannot read current value for field '${field}' -- no handler defined`);
}

/** Return a NEW config with `matchkeys` set (dropping matchSettings so getMatchkeys sees them). */
function withMatchkeys(
  config: GoldenMatchConfig,
  matchkeys: readonly MatchkeyConfig[],
): GoldenMatchConfig {
  const out: Record<string, unknown> = { ...config };
  out["matchkeys"] = matchkeys;
  delete out["matchSettings"];
  return out as GoldenMatchConfig;
}

/** Apply a sweep value to the config, returning a NEW config (no in-place mutation). */
function applyValue(
  field: string,
  value: number,
  config: GoldenMatchConfig,
): GoldenMatchConfig {
  if (field === "threshold") {
    const mks = getMatchkeys(config).map((mk) =>
      (mk as { threshold?: number }).threshold !== undefined
        ? ({ ...mk, threshold: value } as MatchkeyConfig)
        : mk,
    );
    return withMatchkeys(config, mks);
  }
  if (field === "blocking.max_block_size") {
    const blocking = config.blocking
      ? { ...config.blocking, maxBlockSize: Math.trunc(value) }
      : { ...makeBlockingConfig(), maxBlockSize: Math.trunc(value) };
    return { ...config, blocking };
  }
  if (field.startsWith("matchkey.") && field.endsWith(".threshold")) {
    const name = field.split(".")[1];
    const mks = getMatchkeys(config).map((mk) =>
      mk.name === name ? ({ ...mk, threshold: value } as MatchkeyConfig) : mk,
    );
    return withMatchkeys(config, mks);
  }
  throw new Error(`Cannot apply sweep value for field '${field}' -- no handler defined`);
}

/** Generate the list of values to sweep. Mirrors Python `_generate_values`. */
function generateValues(param: SweepSpec): number[] {
  const values: number[] = [];
  let v = param.start;
  while (v <= param.stop + 1e-9) {
    // epsilon for float comparison
    values.push(roundN(v, 6));
    v += param.step;
  }
  return values;
}

/** Deterministic seeded sample of `n` rows (mirror Python's sample-once-reuse intent). */
function sampleRows(rows: readonly Row[], n: number, seed = 42): readonly Row[] {
  if (n >= rows.length) return rows;
  const idx = rows.map((_, i) => i);
  let s = seed >>> 0;
  const rand = (): number => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 0x100000000;
  };
  for (let i = idx.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const tmp = idx[i]!;
    idx[i] = idx[j]!;
    idx[j] = tmp;
  }
  return idx.slice(0, n).map((i) => rows[i]!);
}

/**
 * Run parameter sensitivity analysis. Sweeps each parameter independently,
 * comparing each run's clusters against a single baseline run via CCMS.
 *
 * Ports Python `run_sensitivity`. Per-point errors are caught so partial
 * results are preserved (a failing sweep point is skipped, not fatal).
 */
export async function runSensitivitySweep(
  rows: readonly Row[],
  config: GoldenMatchConfig,
  sweepParams: readonly SweepSpec[],
  sampleSize?: number,
): Promise<SweepResult[]> {
  for (const param of sweepParams) validateField(param.field, config);

  // Sample data once, reuse for all runs.
  const effectiveRows =
    sampleSize !== undefined ? sampleRows(rows, sampleSize) : rows;

  // Run baseline.
  const baselineResult = await runDedupePipeline(effectiveRows, config);
  const baselineClusters = baselineResult.clusters;

  const results: SweepResult[] = [];

  for (const param of sweepParams) {
    const baselineValue = getCurrentValue(param.field, config);
    const values = generateValues(param);
    const points: SweepPointResult[] = [];

    for (const value of values) {
      const sweepConfig = applyValue(param.field, value, config);
      try {
        const sweepResult = await runDedupePipeline(effectiveRows, sweepConfig);
        const comparison = compareClusters(
          baselineClusters,
          sweepResult.clusters,
        );
        points.push({ paramValue: value, comparison });
      } catch (exc) {
        // Per-point failure preserves partial results (Python catches + continues).
        // eslint-disable-next-line no-console
        console.warn(
          `Sweep point ${param.field}=${value} failed: ${
            exc instanceof Error ? exc.message : String(exc)
          }`,
        );
      }
    }

    results.push({ param, baselineValue, points });
  }

  return results;
}

/**
 * Identify the value range with the most unchanged clusters.
 * Ports Python `SensitivityResult.stability_report` — the MCP `sensitivity`
 * tool's per-parameter wire shape.
 */
export function sweepStabilityReport(result: SweepResult): {
  best_value: number;
  best_unchanged_pct: number;
  points: Array<{
    value: number;
    unchanged: number;
    merged: number;
    partitioned: number;
    overlapping: number;
    twi: number;
  }>;
} {
  if (result.points.length === 0) {
    return { best_value: result.baselineValue, best_unchanged_pct: 1.0, points: [] };
  }

  let best = result.points[0]!;
  for (const p of result.points) {
    if (p.comparison.unchanged > best.comparison.unchanged) best = p;
  }
  const total = best.comparison.cc1 || 1;

  return {
    best_value: best.paramValue,
    best_unchanged_pct: roundN(best.comparison.unchanged / total, 4),
    points: result.points.map((p) => ({
      value: p.paramValue,
      unchanged: p.comparison.unchanged,
      merged: p.comparison.merged,
      partitioned: p.comparison.partitioned,
      overlapping: p.comparison.overlapping,
      twi: roundN(p.comparison.twi, 4),
    })),
  };
}
