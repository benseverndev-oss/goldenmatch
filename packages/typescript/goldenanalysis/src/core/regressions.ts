/**
 * Cross-run regression decision logic + models (edge-safe; no node: imports).
 *
 * Parity with the Python reference
 * `packages/python/goldenanalysis/goldenanalysis/_regressions.py`. The two decisions
 * the spec's worked scenario forced: the baseline is a *strategy* (not just
 * "previous"), and thresholds are *per-metric* and respect each `Metric.direction`.
 *
 * These models are camelCase (TS-idiomatic) — they are NOT cross-language wire types
 * (the wire type is `AnalysisReport`, kept snake_case in `types.ts`).
 */

import type { Direction } from "./types.js";

/** "previous" / "rolling_median" / "last_known_good", or a pinned run_id. */
export type Baseline = "previous" | "rolling_median" | "last_known_good" | string;

/** Per-metric regression thresholds (percent); falls back to `defaultPct`. */
export interface RegressionPolicy {
  readonly defaultPct: number;
  readonly perMetric: Record<string, number>;
}

export function policyThreshold(policy: RegressionPolicy, key: string): number {
  return policy.perMetric[key] ?? policy.defaultPct;
}

export function defaultPolicy(): RegressionPolicy {
  return { defaultPct: 10, perMetric: {} };
}

export interface Regression {
  readonly metric: string;
  readonly baseline: number;
  readonly current: number;
  readonly deltaPct: number;
  readonly flagged: boolean;
  readonly direction: Direction;
}

export interface TrendSeries {
  readonly metricKey: string;
  readonly dataset: string;
  readonly points: ReadonlyArray<readonly [string, number]>; // (runId, value), oldest -> newest
}

function median(values: readonly number[]): number {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}

/**
 * The baseline value to compare the current value against. `rolling_median` (median
 * of the last `window`) is immune to one noisy night; `previous` / `last_known_good`
 * (v1 alias) return the most recent historical value. Empty history => null.
 */
export function baselineValue(history: readonly number[], strategy: Baseline, window = 7): number | null {
  if (history.length === 0) return null;
  if (strategy === "rolling_median") return median(history.slice(-window));
  return history[history.length - 1]!;
}

export function deltaPct(baseline: number, current: number): number {
  if (baseline === 0) return 0;
  return ((current - baseline) / baseline) * 100;
}

/** Direction-aware flag: higher_better flags on a drop; lower_better on a rise; neutral either way. */
export function isRegression(direction: Direction, baseline: number, current: number, thresholdPct: number): boolean {
  const d = deltaPct(baseline, current);
  if (direction === "higher_better") return d <= -thresholdPct;
  if (direction === "lower_better") return d >= thresholdPct;
  return Math.abs(d) >= thresholdPct;
}
