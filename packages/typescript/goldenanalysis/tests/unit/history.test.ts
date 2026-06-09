import { describe, expect, it } from "vitest";
import { buildTrend, detectRegressions } from "../../src/core/history.js";
import { night, scenarioReports } from "../fixtures/reports.js";

describe("buildTrend (edge-safe)", () => {
  it("collects a metric oldest -> newest and trims to lastN", () => {
    const series = buildTrend(scenarioReports(), "cluster.singleton_ratio", "customers", 14);
    expect(series.metricKey).toBe("cluster.singleton_ratio");
    expect(series.dataset).toBe("customers");
    expect(series.points.length).toBe(8);
    expect(series.points[series.points.length - 1]).toEqual(["r7", 0.71]);
  });

  it("skips reports missing the metric / non-numeric values", () => {
    const series = buildTrend(scenarioReports(), "does.not.exist", "customers");
    expect(series.points).toEqual([]);
  });
});

describe("detectRegressions (edge-safe)", () => {
  it("the 2% recall gate catches a drop the 10% gate misses; neutral + lower_better also flag", () => {
    const policy = { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } };
    const flagged = detectRegressions(scenarioReports(), { baseline: "rolling_median", policy });
    const keys = new Set(flagged.map((r) => r.metric));
    expect(keys.has("match.recall_safe_bound")).toBe(true);
    expect(keys.has("cluster.singleton_ratio")).toBe(true);
    expect(keys.has("quality.findings_total")).toBe(true);

    // a global 10% gate does NOT flag recall (-8.2%)
    const global = detectRegressions(scenarioReports(), { policy: { defaultPct: 10, perMetric: {} } });
    expect(global.some((r) => r.metric === "match.recall_safe_bound")).toBe(false);
  });

  it("previous baseline over a post-step pair flags nothing", () => {
    const reps = [night("a", 0.89, 0.71, 1205), night("b", 0.89, 0.71, 1205)];
    const policy = { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } };
    expect(detectRegressions(reps, { baseline: "previous", policy })).toEqual([]);
  });

  it("needs at least two reports", () => {
    expect(detectRegressions([night("a", 0.9, 0.5, 1)], {})).toEqual([]);
    expect(detectRegressions([], {})).toEqual([]);
  });

  it("reports carry direction + delta for the flagged metric", () => {
    const policy = { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } };
    const flagged = detectRegressions(scenarioReports(), { baseline: "rolling_median", policy });
    const recall = flagged.find((r) => r.metric === "match.recall_safe_bound");
    expect(recall?.direction).toBe("higher_better");
    expect(recall?.baseline).toBe(0.97);
    expect(recall?.current).toBe(0.89);
    expect(recall?.deltaPct).toBeCloseTo(-8.247, 2);
  });
});
