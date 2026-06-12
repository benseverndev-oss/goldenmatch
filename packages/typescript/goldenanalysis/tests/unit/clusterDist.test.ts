import { describe, expect, it } from "vitest";
import { ClusterDistributionAnalyzer } from "../../src/core/analyzers/clusterDist.js";
import type { AnalyzerInput, Metric } from "../../src/core/types.js";

// 4 clusters, sizes [1, 1, 3, 2] -> 7 records.
const CLUSTERS = {
  0: { members: [0], size: 1 },
  1: { members: [1], size: 1 },
  2: { members: [2, 3, 4], size: 3 },
  3: { members: [5, 6], size: 2 },
};

function run(artifacts: Record<string, unknown>) {
  const inp: AnalyzerInput = { dataset: "customers", artifacts };
  return new ClusterDistributionAnalyzer().run(inp);
}

function byKey(metrics: readonly Metric[]): Map<string, Metric> {
  return new Map(metrics.map((m) => [m.key, m]));
}

describe("cluster.distribution", () => {
  it("core metrics", () => {
    const m = byKey(run({ clusters: CLUSTERS }).metrics);
    expect(m.get("cluster.count")!.value).toBe(4);
    expect(m.get("cluster.record_count")!.value).toBe(7);
    expect(m.get("cluster.singleton_ratio")!.value).toBe(0.5);
    expect(m.get("cluster.size_max")!.value).toBe(3);
    expect(Number(m.get("cluster.reduction_ratio")!.value)).toBeCloseTo(1 - 4 / 7, 9);
  });

  it("discrete size histogram buckets", () => {
    const tbl = run({ clusters: CLUSTERS }).tables.find((t) => t.name === "cluster_size_histogram")!;
    expect(tbl.rows).toEqual([
      [1, 2],
      [2, 1],
      [3, 1],
      ["4+", 0],
    ]);
  });

  it("prefers match_stats.total_records for the record count", () => {
    const m = byKey(run({ clusters: CLUSTERS, match_stats: { total_records: 20 } }).metrics);
    expect(m.get("cluster.record_count")!.value).toBe(20);
    expect(Number(m.get("cluster.reduction_ratio")!.value)).toBeCloseTo(1 - 4 / 20, 9);
  });

  it("emits nothing for an empty clusters object (truthy {} guard)", () => {
    const r = run({ clusters: {} });
    expect(r.metrics).toEqual([]);
    expect(r.tables).toEqual([]);
  });

  it("emits nothing when clusters is absent", () => {
    const r = run({});
    expect(r.metrics).toEqual([]);
  });
});
