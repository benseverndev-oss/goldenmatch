import { describe, expect, it } from "vitest";
import { analyzeMatch, analyzePipeline } from "../../src/core/analyze.js";

describe("analyzeMatch", () => {
  it("runs match.rates + cluster.distribution over a DedupeResult-like object", () => {
    const result = {
      clusters: { 0: { members: [0], size: 1 }, 1: { members: [1, 2], size: 2 } },
      scored_pairs: [[1, 2, 0.9]],
      stats: { total_records: 3, match_rate: 0.66 },
      config: null,
    };
    const report = analyzeMatch(result, { dataset: "customers" });
    expect(new Set(report.analyzers_run)).toEqual(new Set(["match.rates", "cluster.distribution"]));
    const keys = new Set(report.metrics.map((m) => m.key));
    expect(keys.has("match.pair_count")).toBe(true);
    expect(keys.has("cluster.count")).toBe(true);
    expect(report.source["dataset"]).toBe("customers");
    expect(report.source["producer"]).toBe("goldenmatch");
  });
});

describe("analyzePipeline", () => {
  it("fans out to the analyzers whose consumed artifacts are present", () => {
    const result = {
      artifacts: {
        findings: [{ check: "x", column: "a", severity: "WARNING" }],
        manifest: { records: [] },
        clusters: { 0: { members: [0], size: 1 } },
        scored_pairs: [],
        match_stats: { match_rate: 0.5 },
      },
      source: "customers.parquet",
    };
    const ran = new Set(analyzePipeline(result).analyzers_run);
    expect(ran.has("quality.rollup")).toBe(true);
    expect(ran.has("cluster.distribution")).toBe(true);
    expect(ran.has("match.rates")).toBe(true);
    // frame.summary needs a `frame` artifact, which a PipeResult doesn't expose.
    expect(ran.has("frame.summary")).toBe(false);
  });

  it("omits absent analyzers (manifest only -> just quality.rollup)", () => {
    const report = analyzePipeline({ artifacts: { manifest: { records: [] } }, source: "d.csv" });
    expect(report.analyzers_run).toEqual(["quality.rollup"]);
    expect(report.source["producer"]).toBe("goldenpipe");
    expect(report.source["dataset"]).toBe("d");
  });
});
