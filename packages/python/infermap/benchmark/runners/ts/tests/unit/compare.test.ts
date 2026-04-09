import { describe, it, expect } from "vitest";
import { computeDelta, Delta } from "../../src/compare.js";

function metricSet(overrides: Partial<{ f1: number; top1: number; mrr: number; ece: number; n: number }> = {}) {
  return { f1: 0.5, top1: 0.5, mrr: 0.5, ece: 0.1, n: 10, ...overrides };
}

function report(opts: {
  overallF1?: number;
  perCase?: Array<{ id: string; f1: number }>;
  byDifficulty?: Record<string, ReturnType<typeof metricSet>>;
  byCategory?: Record<string, ReturnType<typeof metricSet>>;
  byTag?: Record<string, ReturnType<typeof metricSet>>;
} = {}) {
  return {
    version: 1 as const,
    language: "python" as const,
    infermap_version: "0.1.0",
    runner_version: "0.1.0",
    ran_at: "2026-04-08T00:00:00Z",
    duration_seconds: 1.0,
    scorecard: {
      overall: metricSet({ f1: opts.overallF1 ?? 0.5 }),
      by_difficulty: opts.byDifficulty ?? {},
      by_category: opts.byCategory ?? {},
      by_tag: opts.byTag ?? {},
    },
    per_case: (opts.perCase ?? []).map((pc) => ({
      id: pc.id,
      f1: pc.f1,
      top1: pc.f1,
      mrr: pc.f1,
      expected_n: 2,
      predicted_n: 2,
      true_positives: 1,
      false_positives: 1,
      false_negatives: 1,
      failed: false,
      failure_reason: null,
    })),
    failed_cases: [],
  };
}

describe("computeDelta — overall", () => {
  it("detects improvement", () => {
    const delta = computeDelta(report({ overallF1: 0.5 }), report({ overallF1: 0.6 }));
    expect(delta).toBeInstanceOf(Delta);
    expect(Math.abs(delta.overall.f1! - 0.1)).toBeLessThan(1e-9);
    expect(delta.isRegression(0.02)).toBe(false);
  });

  it("detects regression", () => {
    const delta = computeDelta(report({ overallF1: 0.5 }), report({ overallF1: 0.4 }));
    expect(Math.abs(delta.overall.f1! - -0.1)).toBeLessThan(1e-9);
    expect(delta.isRegression(0.02)).toBe(true);
  });

  it("regression below threshold", () => {
    const delta = computeDelta(report({ overallF1: 0.5 }), report({ overallF1: 0.495 }));
    expect(delta.isRegression(0.02)).toBe(false);
  });

  it("regression exactly at threshold is NOT a regression (IEEE 754 epsilon guard)", () => {
    const delta = computeDelta(report({ overallF1: 0.5 }), report({ overallF1: 0.48 }));
    expect(delta.isRegression(0.02)).toBe(false);
  });

  it("respects custom threshold", () => {
    const delta = computeDelta(report({ overallF1: 0.5 }), report({ overallF1: 0.45 }));
    expect(delta.isRegression(0.02)).toBe(true);
    expect(delta.isRegression(0.10)).toBe(false);
  });
});

describe("computeDelta — slices", () => {
  it("byDifficulty delta", () => {
    const delta = computeDelta(
      report({ byDifficulty: { easy: metricSet({ f1: 0.9 }), hard: metricSet({ f1: 0.3 }) } }),
      report({ byDifficulty: { easy: metricSet({ f1: 0.95 }), hard: metricSet({ f1: 0.35 }) } }),
    );
    expect(Math.abs(delta.byDifficulty.easy!.f1! - 0.05)).toBeLessThan(1e-9);
    expect(Math.abs(delta.byDifficulty.hard!.f1! - 0.05)).toBeLessThan(1e-9);
  });

  it("missing slice in current treated as all zeros", () => {
    const delta = computeDelta(
      report({ byDifficulty: { easy: metricSet({ f1: 0.9 }) } }),
      report({ byDifficulty: {} }),
    );
    expect(Math.abs(delta.byDifficulty.easy!.f1! - -0.9)).toBeLessThan(1e-9);
  });

  it("new slice in current only", () => {
    const delta = computeDelta(
      report({ byTag: {} }),
      report({ byTag: { new_tag: metricSet({ f1: 0.7 }) } }),
    );
    expect(Math.abs(delta.byTag.new_tag!.f1! - 0.7)).toBeLessThan(1e-9);
  });
});

describe("computeDelta — perCase", () => {
  it("detects regression case", () => {
    const delta = computeDelta(
      report({ perCase: [{ id: "a", f1: 0.9 }] }),
      report({ perCase: [{ id: "a", f1: 0.4 }] }),
    );
    const { regressions, improvements } = delta.topMovers(10, 0.05);
    expect(regressions).toHaveLength(1);
    expect(regressions[0]!.caseId).toBe("a");
    expect(regressions[0]!.baselineF1).toBe(0.9);
    expect(regressions[0]!.currentF1).toBe(0.4);
    expect(Math.abs(regressions[0]!.deltaF1 - -0.5)).toBeLessThan(1e-9);
    expect(improvements).toHaveLength(0);
  });

  it("detects improvement case", () => {
    const delta = computeDelta(
      report({ perCase: [{ id: "a", f1: 0.2 }] }),
      report({ perCase: [{ id: "a", f1: 0.8 }] }),
    );
    const { regressions, improvements } = delta.topMovers(10, 0.05);
    expect(regressions).toHaveLength(0);
    expect(improvements).toHaveLength(1);
    expect(improvements[0]!.caseId).toBe("a");
  });

  it("threshold filters small movements", () => {
    const delta = computeDelta(
      report({ perCase: [{ id: "a", f1: 0.5 }] }),
      report({ perCase: [{ id: "a", f1: 0.52 }] }),
    );
    const { regressions, improvements } = delta.topMovers(10, 0.05);
    expect(regressions).toEqual([]);
    expect(improvements).toEqual([]);
  });

  it("top-n limits results", () => {
    const baseline = report({ perCase: Array.from({ length: 15 }, (_, i) => ({ id: `c${i}`, f1: 0.9 })) });
    const current = report({ perCase: Array.from({ length: 15 }, (_, i) => ({ id: `c${i}`, f1: 0.1 })) });
    const delta = computeDelta(baseline, current);
    const { regressions } = delta.topMovers(5, 0.05);
    expect(regressions).toHaveLength(5);
  });

  it("only includes intersection of case ids", () => {
    const delta = computeDelta(
      report({ perCase: [{ id: "a", f1: 0.5 }, { id: "b", f1: 0.5 }] }),
      report({ perCase: [{ id: "a", f1: 0.8 }] }),
    );
    const ids = new Set(delta.perCaseDeltas.map(([id]) => id));
    expect(ids).toEqual(new Set(["a"]));
  });

  it("regressions sorted worst first", () => {
    const delta = computeDelta(
      report({ perCase: [{ id: "big", f1: 0.9 }, { id: "small", f1: 0.9 }] }),
      report({ perCase: [{ id: "big", f1: 0.1 }, { id: "small", f1: 0.6 }] }),
    );
    const { regressions } = delta.topMovers(10, 0.05);
    expect(regressions.map((r) => r.caseId)).toEqual(["big", "small"]);
  });

  it("improvements sorted best first", () => {
    const delta = computeDelta(
      report({ perCase: [{ id: "big", f1: 0.1 }, { id: "small", f1: 0.5 }] }),
      report({ perCase: [{ id: "big", f1: 0.9 }, { id: "small", f1: 0.7 }] }),
    );
    const { improvements } = delta.topMovers(10, 0.05);
    expect(improvements.map((i) => i.caseId)).toEqual(["big", "small"]);
  });
});
