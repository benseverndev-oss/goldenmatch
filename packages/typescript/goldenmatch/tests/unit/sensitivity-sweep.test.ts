/**
 * sensitivity-sweep.test.ts — the Python-faithful sweep engine
 * (runSensitivitySweep + sweepStabilityReport), which backs the MCP
 * `sensitivity` tool.
 *
 * The pipeline is wrapped (hoisted) so ONE sweep value (threshold 0.9) throws,
 * proving the engine PRESERVES partial results (a failing point is skipped, not
 * fatal — the CCMS-vs-baseline landmine). Non-poison values run the real
 * pipeline so the compare-to-baseline assertions are genuine.
 */
import { describe, it, expect, vi } from "vitest";

const POISON_THRESHOLD = 0.9;

vi.mock("../../src/core/pipeline.js", async (orig) => {
  const actual = await orig<typeof import("../../src/core/pipeline.js")>();
  return {
    ...actual,
    runDedupePipeline: (...args: Parameters<typeof actual.runDedupePipeline>) => {
      const cfg = args[1] as unknown as {
        matchkeys?: Array<{ threshold?: number }>;
        matchSettings?: Array<{ threshold?: number }>;
      };
      const mks = cfg.matchkeys ?? cfg.matchSettings ?? [];
      const t = mks[0]?.threshold;
      if (t === POISON_THRESHOLD) {
        throw new Error(`simulated pipeline failure at threshold ${t}`);
      }
      return actual.runDedupePipeline(...args);
    },
  };
});

import {
  runSensitivitySweep,
  sweepStabilityReport,
} from "../../src/core/sensitivity.js";
import type { GoldenMatchConfig, MatchkeyConfig, Row } from "../../src/core/types.js";

const NAME_MK: MatchkeyConfig = {
  name: "name",
  type: "weighted",
  threshold: 0.85,
  fields: [{ field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 }],
};
const CONFIG: GoldenMatchConfig = {
  matchkeys: [NAME_MK],
  // All rows share one block so every pair is scored.
  blocking: {
    strategy: "static",
    keys: [{ fields: ["b"], transforms: [] }],
    maxBlockSize: 5000,
    skipOversized: false,
  },
};

const ROWS: Row[] = [
  { name: "John Smith", b: "1" },
  { name: "Jon Smith", b: "1" },
  { name: "Alice Jones", b: "1" },
  { name: "Alicia Jones", b: "1" },
];

describe("runSensitivitySweep", () => {
  it("runs a sweep and compares each point to the baseline via CCMS", async () => {
    // Sweep 0.80..0.85 — no poison; both points run.
    const results = await runSensitivitySweep(ROWS, CONFIG, [
      { field: "threshold", start: 0.8, stop: 0.85, step: 0.05 },
    ]);

    expect(results.length).toBe(1);
    const r = results[0]!;
    expect(r.param.field).toBe("threshold");
    expect(r.baselineValue).toBe(0.85); // current value from the fuzzy matchkey
    expect(r.points.length).toBe(2); // 0.80, 0.85

    // Every point carries a real CCMS comparison against the baseline.
    for (const p of r.points) {
      expect(p.comparison).toHaveProperty("twi");
      expect(p.comparison).toHaveProperty("cc1");
      expect(p.comparison).toHaveProperty("unchanged");
    }

    // The point equal to the baseline threshold reproduces the baseline
    // clustering exactly: every cluster unchanged, TWI == 1.
    const atBaseline = r.points.find((p) => p.paramValue === 0.85)!;
    expect(atBaseline.comparison.unchanged).toBe(atBaseline.comparison.cc1);
    expect(atBaseline.comparison.twi).toBeCloseTo(1.0, 6);
  });

  it("PRESERVES partial results when one sweep point errors", async () => {
    // Sweep 0.80..0.90 — the 0.90 point throws in the (wrapped) pipeline.
    const results = await runSensitivitySweep(ROWS, CONFIG, [
      { field: "threshold", start: 0.8, stop: 0.9, step: 0.05 },
    ]);

    const r = results[0]!;
    // 3 values generated (0.80, 0.85, 0.90); the poison point is dropped, not fatal.
    expect(r.points.length).toBe(2);
    expect(r.points.map((p) => p.paramValue).sort()).toEqual([0.8, 0.85]);
  });

  it("sweepStabilityReport matches the Python stability_report wire shape", async () => {
    const results = await runSensitivitySweep(ROWS, CONFIG, [
      { field: "threshold", start: 0.8, stop: 0.85, step: 0.05 },
    ]);
    const report = sweepStabilityReport(results[0]!);

    expect(Object.keys(report).sort()).toEqual(
      ["best_unchanged_pct", "best_value", "points"].sort(),
    );
    expect(typeof report.best_value).toBe("number");
    expect(report.best_unchanged_pct).toBeGreaterThanOrEqual(0);
    expect(report.best_unchanged_pct).toBeLessThanOrEqual(1);
    for (const p of report.points) {
      expect(Object.keys(p).sort()).toEqual(
        ["merged", "overlapping", "partitioned", "twi", "unchanged", "value"].sort(),
      );
    }
  });

  it("empty points yields the baseline fallback report", () => {
    const report = sweepStabilityReport({
      param: { field: "threshold", start: 0.8, stop: 0.85, step: 0.05 },
      baselineValue: 0.85,
      points: [],
    });
    expect(report).toEqual({
      best_value: 0.85,
      best_unchanged_pct: 1.0,
      points: [],
    });
  });

  it("rejects an unsupported sweep field", async () => {
    await expect(
      runSensitivitySweep(ROWS, CONFIG, [
        { field: "not_a_field", start: 0, stop: 1, step: 0.5 },
      ]),
    ).rejects.toThrow(/Unsupported sweep field/);
  });

  it("rejects a matchkey.<name>.threshold sweep for an unknown matchkey", async () => {
    await expect(
      runSensitivitySweep(ROWS, CONFIG, [
        { field: "matchkey.nope.threshold", start: 0.7, stop: 0.9, step: 0.1 },
      ]),
    ).rejects.toThrow(/not found in config/);
  });
});
