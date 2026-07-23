import { describe, it, expect } from "vitest";

import {
  estimateRecall,
  buildDecorrelatedSystems,
  clustersToPairs,
  toCertifyRecallResponse,
} from "../../src/core/recall-certificate.js";
import type { MatchkeyConfig, ClusterInfo } from "../../src/core/types.js";

/** Build a pairset (a decorrelated system's found pairs) from "a,b" keys. */
function sys(...pairs: string[]): Set<string> {
  return new Set(pairs);
}

describe("recall-certificate: estimateRecall (capture-recapture)", () => {
  // 3 decorrelated systems. 4 pairs captured by ALL 3 (f_3 = 4); 6 pairs
  // captured by exactly 2 (f_2 = 6); plus FP singletons (ignored).
  const A = ["1,2", "3,4", "5,6", "7,8"]; // captured 3x
  const B = ["9,10", "11,12", "13,14", "15,16", "17,18", "19,20"]; // captured 2x
  const s0 = sys(...A, ...B, "90,91"); // + a spurious singleton
  const s1 = sys(...A, ...B);
  const s2 = sys(...A, "92,93"); // + a spurious singleton

  const est = estimateRecall([s0, s1, s2]);

  it("produces a lower-bound point estimate in (0,1] from >=3 systems", () => {
    expect(est.estimable).toBe(true);
    expect(est.recall).not.toBeNull();
    expect(est.recall!).toBeGreaterThan(0);
    expect(est.recall!).toBeLessThanOrEqual(1);
    expect(est.nSystems).toBe(3);
    // capture histogram: f_3 = 4, f_2 = 6.
    expect(est.captureHistogram[3]).toBe(4);
    expect(est.captureHistogram[2]).toBe(6);
  });

  it("preserves Python's lower-bound / no-labels caveat framing verbatim", () => {
    // The estimate is NOT a supervised recall number -- the note must say so.
    expect(est.note).toContain("point estimate (no labels)");
    expect(est.note).toContain("lower bound needs a small labelled audit");
  });

  it("refuses (recall null) with the >=3-systems note when fewer than 3 systems", () => {
    const out = estimateRecall([sys("1,2"), sys("1,2")]);
    expect(out.estimable).toBe(false);
    expect(out.recall).toBeNull();
    expect(out.nSystems).toBe(2);
    expect(out.note).toContain("need >=3 decorrelated systems");
  });

  it("refuses when there are too few multi-captured pairs to fit", () => {
    // 3 systems but no pair is captured by >=2 (all disjoint singletons).
    const out = estimateRecall([sys("1,2"), sys("3,4"), sys("5,6")]);
    expect(out.estimable).toBe(false);
    expect(out.recall).toBeNull();
    expect(out.note).toContain("too few multi-captured pairs");
  });
});

describe("recall-certificate: toCertifyRecallResponse (Python wire shape)", () => {
  it("maps to {estimated_recall, n_systems, found_pairs, system_overlap, estimable, note}", () => {
    const est = estimateRecall([sys("1,2"), sys("1,2")]);
    const resp = toCertifyRecallResponse(est);
    expect(resp).toHaveProperty("estimated_recall");
    expect(resp).toHaveProperty("n_systems", 2);
    expect(resp).toHaveProperty("found_pairs", 1);
    expect(resp).toHaveProperty("system_overlap");
    expect(resp).toHaveProperty("estimable", false);
    expect(resp).toHaveProperty("note");
    // system_overlap rounded to 3 decimals.
    expect(Number.isFinite(resp.system_overlap)).toBe(true);
  });
});

describe("recall-certificate: buildDecorrelatedSystems", () => {
  function weighted(name: string, fields: string[]): MatchkeyConfig {
    return {
      name,
      type: "weighted",
      threshold: 0.85,
      fields: fields.map((f) => ({
        field: f,
        transforms: ["lowercase"],
        scorer: "jaro_winkler",
        weight: 1.0,
      })),
    };
  }

  it("one system per matchkey when >=3 matchkeys exist", () => {
    const mks = [weighted("a", ["x"]), weighted("b", ["y"]), weighted("c", ["z"])];
    const systems = buildDecorrelatedSystems(mks);
    expect(systems.length).toBe(3);
    expect(systems.every((s) => s.length === 1)).toBe(true);
  });

  it("splits a single multi-field matchkey into per-field systems (<3 matchkeys)", () => {
    const mks = [weighted("wide", ["fname", "lname", "email"])];
    const systems = buildDecorrelatedSystems(mks);
    expect(systems.length).toBe(3);
    // each split system carries exactly one field + a derived name.
    expect(systems[0]![0]!.fields.length).toBe(1);
    expect(systems[0]![0]!.name).toBe("wide__f0");
    expect(systems[2]![0]!.fields[0]!.field).toBe("email");
  });
});

describe("recall-certificate: clustersToPairs", () => {
  it("expands cluster members into canonical within-cluster pairs", () => {
    const clusters = new Map<number, ClusterInfo>([
      [
        0,
        {
          members: [3, 1, 2],
          size: 3,
          oversized: false,
          pairScores: new Map(),
          confidence: 1,
          bottleneckPair: null,
          clusterQuality: "strong",
        },
      ],
      [
        1,
        {
          members: [7],
          size: 1,
          oversized: false,
          pairScores: new Map(),
          confidence: 1,
          bottleneckPair: null,
          clusterQuality: "strong",
        },
      ],
    ]);
    const pairs = clustersToPairs(clusters);
    // {1,2,3} -> (1,2),(1,3),(2,3); singleton {7} -> none.
    expect(pairs).toEqual(new Set(["1,2", "1,3", "2,3"]));
  });
});
