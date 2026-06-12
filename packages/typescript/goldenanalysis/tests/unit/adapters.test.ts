import { describe, expect, it } from "vitest";
import { checkArtifacts } from "../../src/core/adapters/check.js";
import { flowArtifacts } from "../../src/core/adapters/flow.js";
import { matchArtifacts } from "../../src/core/adapters/match.js";
import { pipeArtifacts } from "../../src/core/adapters/pipe.js";

describe("matchArtifacts", () => {
  it("normalizes a DedupeResult-like object + an explicit certificate", () => {
    const result = {
      clusters: { 0: { members: [0], size: 1 } },
      scored_pairs: [[0, 1, 0.9]],
      stats: { total_records: 2, match_rate: 0.5 },
      config: null,
    };
    const inp = matchArtifacts(result, { dataset: "customers", certificate: { estimate: 0.94, safe_bound: 0.89 } });
    expect(inp.dataset).toBe("customers");
    expect(inp.artifacts["__producer__"]).toBe("goldenmatch");
    expect(inp.artifacts["clusters"]).toEqual(result.clusters);
    expect(inp.artifacts["scored_pairs"]).toEqual(result.scored_pairs);
    expect(inp.artifacts["match_stats"]).toEqual(result.stats);
    expect(inp.artifacts["recall_certificate"]).toEqual({ estimate: 0.94, safe_bound: 0.89 });
  });

  it("reads + normalizes a producer-attached certificate (RecallEstimate, no safe bound)", () => {
    const result = { clusters: {}, scored_pairs: [], stats: {}, config: null, recall_certificate: { recall: 0.94, recall_lower: null } };
    const inp = matchArtifacts(result);
    expect(inp.artifacts["recall_certificate"]).toEqual({ estimate: 0.94, safe_bound: null });
  });
});

describe("flowArtifacts", () => {
  it("normalizes a TransformResult-like object (frame + manifest)", () => {
    const df = [{ a: 1 }];
    const manifest = { records: [] };
    const inp = flowArtifacts({ df, manifest }, { dataset: "d" });
    expect(inp.artifacts["__producer__"]).toBe("goldenflow");
    expect(inp.artifacts["manifest"]).toBe(manifest);
    expect(inp.frame).toBe(df);
  });
});

describe("checkArtifacts", () => {
  it("is a pure from-scan seam", () => {
    const inp = checkArtifacts([{ check: "x" }], null, { dataset: "d" });
    expect(inp.artifacts["__producer__"]).toBe("goldencheck");
    expect(inp.artifacts["findings"]).toEqual([{ check: "x" }]);
    expect(inp.artifacts["profile"]).toBeNull();
  });
});

describe("pipeArtifacts", () => {
  it("passes artifacts through, derives the dataset from the source stem, normalizes the cert", () => {
    const result = {
      artifacts: {
        clusters: { 0: { members: [0], size: 1 } },
        scored_pairs: [[0, 1, 0.9]],
        match_stats: { match_rate: 0.5 },
        findings: [{ check: "x", column: "a", severity: "WARNING" }],
        manifest: { records: [] },
        recall_certificate: { recall: 0.94, recall_lower: 0.89 },
      },
      source: "customers.parquet",
      input_rows: 4000,
    };
    const inp = pipeArtifacts(result);
    expect(inp.dataset).toBe("customers");
    expect(inp.artifacts["__producer__"]).toBe("goldenpipe");
    expect(inp.artifacts["clusters"]).toEqual(result.artifacts.clusters);
    expect(inp.artifacts["recall_certificate"]).toEqual({ estimate: 0.94, safe_bound: 0.89 });
  });

  it("falls back to 'frame' for a non-file source", () => {
    const inp = pipeArtifacts({ artifacts: {}, source: "<DataFrame>" });
    expect(inp.dataset).toBe("frame");
  });
});
