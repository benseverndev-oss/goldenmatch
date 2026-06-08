import { describe, expect, it } from "vitest";
import { MatchRatesAnalyzer } from "../../src/core/analyzers/matchRates.js";
import type { AnalyzerInput, Metric } from "../../src/core/types.js";

function input(artifacts: Record<string, unknown>): AnalyzerInput {
  return { dataset: "customers", artifacts };
}

function byKey(metrics: readonly Metric[]): Map<string, Metric> {
  return new Map(metrics.map((m) => [m.key, m]));
}

describe("match.rates", () => {
  it("core metrics; recall omitted without a certificate", () => {
    const r = new MatchRatesAnalyzer().run(
      input({
        scored_pairs: [
          [0, 1, 0.9],
          [1, 2, 0.8],
          [3, 4, 0.95],
        ],
        match_stats: { total_records: 10, match_rate: 0.3, total_clusters: 2 },
        match_threshold: 0.82,
      }),
    );
    const m = byKey(r.metrics);
    expect(m.get("match.pair_count")!.value).toBe(3);
    expect(m.get("match.match_rate")!.value).toBe(0.3);
    expect(m.get("match.threshold")!.value).toBe(0.82);
    expect(Number(m.get("match.mean_pair_score")!.value)).toBeCloseTo((0.9 + 0.8 + 0.95) / 3, 9);
    expect(m.has("match.recall_estimate")).toBe(false);
    expect(m.has("match.recall_safe_bound")).toBe(false);
  });

  it("recall from a {estimate, safe_bound} certificate (both higher_better)", () => {
    const r = new MatchRatesAnalyzer().run(
      input({
        scored_pairs: [[0, 1, 0.9]],
        match_stats: { total_records: 4, match_rate: 0.5 },
        recall_certificate: { estimate: 0.94, safe_bound: 0.89 },
      }),
    );
    const m = byKey(r.metrics);
    expect(m.get("match.recall_estimate")!.value).toBe(0.94);
    expect(m.get("match.recall_estimate")!.direction).toBe("higher_better");
    expect(m.get("match.recall_safe_bound")!.value).toBe(0.89);
    expect(m.get("match.recall_safe_bound")!.direction).toBe("higher_better");
  });

  it("estimate-only certificate omits the safe bound", () => {
    const r = new MatchRatesAnalyzer().run(
      input({ scored_pairs: [[0, 1, 0.9]], match_stats: { match_rate: 0.5 }, recall_certificate: { estimate: 0.94, safe_bound: null } }),
    );
    const m = byKey(r.metrics);
    expect(m.get("match.recall_estimate")!.value).toBe(0.94);
    expect(m.has("match.recall_safe_bound")).toBe(false);
  });

  it("emits a score_histogram table when pairs are present", () => {
    const r = new MatchRatesAnalyzer().run(
      input({ scored_pairs: [[0, 1, 0.1], [2, 3, 0.9]], match_stats: { match_rate: 0.5 } }),
    );
    expect(r.tables.map((t) => t.name)).toContain("score_histogram");
  });

  it("degrades on empty pairs (no mean_pair_score)", () => {
    const r = new MatchRatesAnalyzer().run(input({ scored_pairs: [], match_stats: { total_records: 5, match_rate: 0.0 } }));
    const m = byKey(r.metrics);
    expect(m.get("match.pair_count")!.value).toBe(0);
    expect(m.has("match.mean_pair_score")).toBe(false);
    expect(r.tables.length).toBe(0);
  });
});
