/**
 * Report-level cross-run functions (edge-safe; operate on an in-memory
 * `AnalysisReport[]`). The node-only `ReportHistory` (jsonl) wraps storage around
 * these. Parity with the Python `history.py` trend/detect_regressions logic.
 */

import { baselineValue, defaultPolicy, deltaPct, isRegression, policyThreshold } from "./regressions.js";
import type { Baseline, Regression, RegressionPolicy, TrendSeries } from "./regressions.js";
import type { AnalysisReport } from "./types.js";

function numericValue(report: AnalysisReport, key: string): number | null {
  for (const m of report.metrics) {
    if (m.key === key) return typeof m.value === "number" ? m.value : null;
  }
  return null;
}

/** A metric's value across the reports (oldest -> newest), last `lastN`. */
export function buildTrend(
  reports: readonly AnalysisReport[],
  metricKey: string,
  dataset: string,
  lastN = 30,
): TrendSeries {
  const points: Array<readonly [string, number]> = [];
  for (const report of reports) {
    const value = numericValue(report, metricKey);
    if (value !== null) points.push([report.run_id, value] as const);
  }
  return { metricKey, dataset, points: points.slice(-lastN) };
}

export interface DetectOptions {
  readonly baseline?: Baseline;
  readonly window?: number;
  readonly policy?: RegressionPolicy;
}

/**
 * Flag metric movements in the LATEST report vs the prior history. Compares each
 * numeric metric in the most-recent report against a baseline from the earlier
 * reports under the chosen strategy + per-metric policy. Returns only the flagged.
 */
export function detectRegressions(reports: readonly AnalysisReport[], options: DetectOptions = {}): Regression[] {
  if (reports.length < 2) return [];
  const baseline: Baseline = options.baseline ?? "rolling_median";
  const window = options.window ?? 7;
  const policy = options.policy ?? defaultPolicy();

  const current = reports[reports.length - 1]!;
  const prior = reports.slice(0, -1);
  const out: Regression[] = [];
  for (const metric of current.metrics) {
    if (typeof metric.value !== "number") continue;
    const series: number[] = [];
    for (const rep of prior) {
      const v = numericValue(rep, metric.key);
      if (v !== null) series.push(v);
    }
    const base = baselineValue(series, baseline, window);
    if (base === null) continue;
    const threshold = policyThreshold(policy, metric.key);
    const flagged = isRegression(metric.direction, base, metric.value, threshold);
    if (flagged) {
      out.push({
        metric: metric.key,
        baseline: base,
        current: metric.value,
        deltaPct: deltaPct(base, metric.value),
        flagged: true,
        direction: metric.direction,
      });
    }
  }
  return out;
}
