import { describe, it, expect } from "vitest";
import {
  compareClusters,
  buildClusters,
  ccmsSummary,
  parseClustersJson,
} from "../../src/core/index.js";

describe("compareClusters (CCMS)", () => {
  it("identical clustering -> all unchanged, TWI = 1", () => {
    const pairs: [number, number, number][] = [[1, 2, 0.9], [3, 4, 0.9]];
    const a = buildClusters(pairs, [1, 2, 3, 4]);
    const b = buildClusters(pairs, [1, 2, 3, 4]);
    const result = compareClusters(a, b);
    expect(result.unchanged).toBe(a.size);
    expect(result.merged).toBe(0);
    expect(result.partitioned).toBe(0);
    expect(result.overlapping).toBe(0);
    expect(result.twi).toBeCloseTo(1.0, 5);
  });

  it("different clustering produces non-zero classifications", () => {
    // A has {1,2,3}, B has {1,2},{3}
    const pairsA: [number, number, number][] = [[1, 2, 0.9], [2, 3, 0.9]];
    const pairsB: [number, number, number][] = [[1, 2, 0.9]];
    const a = buildClusters(pairsA, [1, 2, 3]);
    const b = buildClusters(pairsB, [1, 2, 3]);
    const result = compareClusters(a, b);
    // A cluster {1,2,3} is partitioned in B
    expect(result.partitioned).toBeGreaterThanOrEqual(1);
  });

  it("throws if row id coverage differs", () => {
    const a = buildClusters([], [1, 2]);
    const b = buildClusters([], [1, 2, 3]);
    expect(() => compareClusters(a, b)).toThrow();
  });

  it("returns cc1, cc2, rc metadata", () => {
    const a = buildClusters([[1, 2, 0.9]], [1, 2, 3]);
    const b = buildClusters([[1, 2, 0.9]], [1, 2, 3]);
    const result = compareClusters(a, b);
    expect(result.cc1).toBe(a.size);
    expect(result.cc2).toBe(b.size);
    expect(result.rc).toBe(3);
  });

  it("counts singleton clusters as sc1 / sc2", () => {
    // A: {1,2},{3}  (one singleton)   B: {1},{2},{3}  (three singletons)
    const a = buildClusters([[1, 2, 0.9]], [1, 2, 3]);
    const b = buildClusters([], [1, 2, 3]);
    const result = compareClusters(a, b);
    expect(result.sc1).toBe(1);
    expect(result.sc2).toBe(3);
  });
});

describe("ccmsSummary (Python CompareResult.summary parity)", () => {
  it("emits the snake_case wire dict with rounded twi + percentages", () => {
    // A: {1,2,3}  ->  B: {1,2},{3}  == partitioned
    const a = parseClustersJson({ "1": { members: [1, 2, 3] } });
    const b = parseClustersJson({ "1": [1, 2], "2": [3] });
    const summary = ccmsSummary(compareClusters(a, b));
    expect(summary).toEqual({
      unchanged: 0,
      merged: 0,
      partitioned: 1,
      overlapping: 0,
      rc: 3,
      cc1: 1,
      cc2: 2,
      sc1: 0,
      sc2: 1,
      twi: 0.7071,
      unchanged_pct: 0,
      merged_pct: 0,
      partitioned_pct: 1,
      overlapping_pct: 0,
    });
  });
});

describe("parseClustersJson (Python _load_clusters_json parity)", () => {
  it("accepts a bare mapping of members-objects", () => {
    const m = parseClustersJson({ "0": { members: [0, 1] }, "1": { members: [2] } });
    expect(m.get(0)?.members).toEqual([0, 1]);
    expect(m.get(1)?.members).toEqual([2]);
  });

  it("accepts bare member lists", () => {
    const m = parseClustersJson({ "5": [5, 6, 7] });
    expect(m.get(5)?.members).toEqual([5, 6, 7]);
  });

  it("unwraps a { clusters: {...} } envelope and coerces string ids", () => {
    const m = parseClustersJson({ clusters: { "9": ["1", "2"] } });
    expect(m.get(9)?.members).toEqual([1, 2]);
  });

  it("throws on a payload with no members", () => {
    expect(() => parseClustersJson({ "0": { size: 3 } })).toThrow();
    expect(() => parseClustersJson(42)).toThrow();
  });
});
