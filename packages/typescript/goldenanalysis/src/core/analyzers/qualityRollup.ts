/**
 * `quality.rollup` — a single "is the data healthy" view rolling up both GoldenCheck
 * (findings + profile) and GoldenFlow (manifest).
 *
 * Reads `findings` / `profile` / `manifest` from `AnalyzerInput.artifacts`, degrading
 * per-producer: the `quality.*` keys need `findings`; the `flow.*` keys need
 * `manifest`; either can be absent. Parity with
 * `packages/python/goldenanalysis/goldenanalysis/analyzers/quality_rollup.py`.
 */

import type {
  AnalysisTable,
  Analyzer,
  AnalyzerInfo,
  AnalyzerInput,
  AnalyzerResult,
  Metric,
} from "../types.js";

const PRODUCES = [
  "quality.findings_total",
  "quality.columns_with_findings",
  "quality.score",
  "flow.rows_changed",
  "flow.rules_fired",
];

/** Read `key` from a dict/object (Finding/TransformRecord either way). */
function get(obj: unknown, key: string, fallback: unknown = undefined): unknown {
  if (obj !== null && typeof obj === "object") {
    const v = (obj as Record<string, unknown>)[key];
    return v === undefined ? fallback : v;
  }
  return fallback;
}

/** Normalize a severity (enum-ish / int / str) to an upper-case name. */
function severityName(value: unknown): string {
  if (value !== null && typeof value === "object") {
    const name = (value as Record<string, unknown>)["name"];
    if (name) return String(name).toUpperCase();
  }
  if (typeof value === "number") {
    return ({ 1: "INFO", 2: "WARNING", 3: "ERROR" } as Record<number, string>)[value] ?? String(value);
  }
  return String(value).toUpperCase();
}

/** Counter.most_common(): count desc, ties in first-appearance order (stable sort). */
function mostCommon(counts: Map<string, number>): Array<[string, number]> {
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * GoldenCheck `DatasetProfile.health_score` normalized to a 0-1 ratio. Duck-typed:
 * if `profile` exposes a `healthScore`/`health_score` function, it is called with the
 * per-column `{errors,warnings}` map; the return is read as `[grade, score]` or a
 * bare `score` and divided by 100. Returns null if absent or it throws.
 */
function healthScore(profile: unknown, findings: readonly unknown[]): number | null {
  if (profile === null || typeof profile !== "object") return null;
  const fn = (profile as Record<string, unknown>)["healthScore"] ?? (profile as Record<string, unknown>)["health_score"];
  if (typeof fn !== "function") return null;
  const byCol: Record<string, { errors: number; warnings: number }> = {};
  for (const f of findings) {
    const col = get(f, "column");
    if (col === null || col === undefined) continue;
    const sev = severityName(get(f, "severity"));
    const bucket = (byCol[String(col)] ??= { errors: 0, warnings: 0 });
    if (sev === "ERROR") bucket.errors += 1;
    else if (sev === "WARNING") bucket.warnings += 1;
  }
  try {
    const out = (fn as (arg: unknown) => unknown)(byCol);
    const score = Array.isArray(out) ? Number(out[1]) : Number(out);
    return Number.isNaN(score) ? null : score / 100;
  } catch {
    return null;
  }
}

export class QualityRollupAnalyzer implements Analyzer {
  readonly info: AnalyzerInfo = {
    name: "quality.rollup",
    consumes: ["findings", "manifest"],
    produces: PRODUCES,
  };

  run(input: AnalyzerInput): AnalyzerResult {
    const art = input.artifacts;
    const metrics: Metric[] = [];
    const tables: AnalysisTable[] = [];

    const findings = art["findings"];
    if (Array.isArray(findings)) {
      const byClass = new Map<string, number>();
      const columns = new Set<string>();
      for (const f of findings) {
        const cls = String(get(f, "check", "unknown"));
        byClass.set(cls, (byClass.get(cls) ?? 0) + 1);
        const col = get(f, "column");
        if (col !== null && col !== undefined) columns.add(String(col));
      }
      metrics.push({ key: "quality.findings_total", value: findings.length, unit: "findings", direction: "lower_better" });
      metrics.push({
        key: "quality.columns_with_findings",
        value: columns.size,
        unit: "columns",
        direction: "lower_better",
      });
      const score = healthScore(art["profile"], findings);
      if (score !== null) {
        metrics.push({ key: "quality.score", value: score, unit: "ratio", direction: "higher_better" });
      }
      tables.push({ name: "findings_by_class", columns: ["class", "count"], rows: mostCommon(byClass) });
    }

    const manifest = art["manifest"];
    if (manifest !== null && manifest !== undefined) {
      const records = (get(manifest, "records", []) as unknown[]) ?? [];
      const rowsChanged = records.reduce((acc: number, r) => acc + Number(get(r, "affected_rows", 0) ?? 0), 0);
      metrics.push({ key: "flow.rows_changed", value: rowsChanged, unit: "rows", direction: "neutral" });
      metrics.push({ key: "flow.rules_fired", value: records.length, unit: "count", direction: "neutral" });
    }

    return { metrics, tables };
  }
}
