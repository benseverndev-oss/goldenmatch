// Hand-built `AnalysisReport` fixtures for the cross-run tests. Mirrors the Python
// `_night` / `_report` helpers (packages/python/goldenanalysis/tests/) so the TS
// cross-run layer is exercised on the same "Maya scenario": 7 healthy nights then a
// regressed 8th where a per-metric 2% gate on recall catches a drop a 10% gate misses.

import type { AnalysisReport, AnalysisTable, Direction, Metric } from "../../src/core/types.js";

export function metric(
  key: string,
  value: number | string,
  direction: Direction,
  unit: string | null = "ratio",
): Metric {
  return { key, value, unit, direction };
}

export function report(
  runId: string,
  metrics: readonly Metric[],
  options: { dataset?: string; tables?: readonly AnalysisTable[]; narrative?: string | null } = {},
): AnalysisReport {
  return {
    schema_version: 1,
    run_id: runId,
    generated_at: "2026-06-08T00:00:00+00:00",
    source: { dataset: options.dataset ?? "customers" },
    metrics,
    tables: options.tables ?? [],
    narrative: options.narrative ?? null,
    analyzers_run: [],
  };
}

/** One night of the worked scenario: recall (higher_better), singleton (neutral),
 * findings (lower_better), plus a `findings_by_class` table. */
export function night(runId: string, recall: number, singleton: number, findings: number): AnalysisReport {
  return report(
    runId,
    [
      metric("match.recall_safe_bound", recall, "higher_better"),
      metric("cluster.singleton_ratio", singleton, "neutral"),
      metric("quality.findings_total", findings, "lower_better", "findings"),
    ],
    {
      tables: [{ name: "findings_by_class", columns: ["class", "count"], rows: [["email_blanked", 1188]] }],
    },
  );
}

/** 7 healthy nights then a regressed 8th. */
export function scenarioReports(): AnalysisReport[] {
  const reps: AnalysisReport[] = [];
  for (let i = 0; i < 7; i++) reps.push(night(`r${i}`, 0.97, 0.58, 410));
  reps.push(night("r7", 0.89, 0.71, 1205));
  return reps;
}
