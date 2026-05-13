import { describe, it, expect } from "vitest";
import { RunHistory, RED_PROFILE, type HistoryEntry } from "../../src/core/autoconfigHistory.js";
import {
  HealthVerdict,
  StopReason,
  makeComplexityProfile,
  makeDataProfile,
  makeBlockingProfile,
  makeScoringProfile,
} from "../../src/core/complexityProfile.js";
import { makeConfig } from "../../src/core/types.js";

function makeEntry(opts: {
  iteration: number;
  verdict: HealthVerdict;
  massAbove?: number;
  massBorderline?: number;
  error?: boolean;
}): HistoryEntry {
  // Build a profile that rolls up to the desired verdict.
  let profile;
  if (opts.verdict === HealthVerdict.GREEN) {
    profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { a: "text", b: "numeric", c: "date" },
      }),
      blocking: makeBlockingProfile({
        nBlocks: 10,
        blockSizesP99: 10,
        reductionRatio: 0.9,
        singletonBlockCount: 1,
      }),
      scoring: makeScoringProfile({
        candidatesCompared: 100,
        nPairsScored: 50,
        massAboveThreshold: opts.massAbove ?? 0.6,
        massInBorderline: opts.massBorderline ?? 0.1,
        dipStatistic: 0.05,
      }),
    });
  } else if (opts.verdict === HealthVerdict.YELLOW) {
    profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { a: "text", b: "numeric", c: "date" },
      }),
      blocking: makeBlockingProfile({
        nBlocks: 10,
        blockSizesP99: 10,
        reductionRatio: 0.9,
        singletonBlockCount: 1,
      }),
      scoring: makeScoringProfile({
        candidatesCompared: 100,
        nPairsScored: 50,
        massAboveThreshold: opts.massAbove ?? 0.5,
        massInBorderline: opts.massBorderline ?? 0.4,  // > 0.3 → YELLOW
        dipStatistic: 0.05,
      }),
    });
  } else {
    profile = RED_PROFILE;
  }

  return {
    iteration: opts.iteration,
    config: makeConfig(),
    profile,
    decision: null,
    error: opts.error
      ? { exceptionType: "RuntimeError", tracebackSummary: "boom" }
      : null,
    wallClockMs: 1,
  };
}

describe("RunHistory.pickCommitted", () => {
  it("returns null when every entry errored", () => {
    const h = new RunHistory();
    h.append(makeEntry({ iteration: 0, verdict: HealthVerdict.GREEN, error: true }));
    h.append(makeEntry({ iteration: 1, verdict: HealthVerdict.GREEN, error: true }));
    expect(h.pickCommitted()).toBe(null);
  });

  it("prefers GREEN over YELLOW over RED", () => {
    const h = new RunHistory();
    h.append(makeEntry({ iteration: 0, verdict: HealthVerdict.RED }));
    h.append(makeEntry({ iteration: 1, verdict: HealthVerdict.YELLOW }));
    h.append(makeEntry({ iteration: 2, verdict: HealthVerdict.GREEN }));
    const best = h.pickCommitted();
    expect(best?.iteration).toBe(2);
  });

  it("falls back to RED when no GREEN/YELLOW exists", () => {
    const h = new RunHistory();
    h.append(makeEntry({ iteration: 0, verdict: HealthVerdict.RED }));
    h.append(makeEntry({ iteration: 1, verdict: HealthVerdict.RED }));
    const best = h.pickCommitted();
    expect(best?.iteration).toBeDefined();
  });

  it("tie-breaks YELLOW entries by mass separation", () => {
    const h = new RunHistory();
    h.append(
      makeEntry({
        iteration: 0,
        verdict: HealthVerdict.YELLOW,
        massAbove: 0.5,
        massBorderline: 0.4,
      }),
    );
    h.append(
      makeEntry({
        iteration: 1,
        verdict: HealthVerdict.YELLOW,
        massAbove: 0.7,
        massBorderline: 0.4,
      }),
    );
    expect(h.pickCommitted()?.iteration).toBe(1);
  });

  it("precisionCollapseFloor demotes RED with mass_above above floor", () => {
    // Build two RED entries with distinct profile objects.
    const h = new RunHistory();
    const mkRed = (iter: number, massAbove: number): HistoryEntry => ({
      iteration: iter,
      config: makeConfig(),
      profile: makeComplexityProfile({
        // nRows=0 forces dataHealth=RED, which forces rollup=RED.
        data: makeDataProfile({ nRows: 0 }),
        scoring: makeScoringProfile({ massAboveThreshold: massAbove }),
      }),
      decision: null,
      error: null,
      wallClockMs: 1,
    });
    h.append(mkRed(0, 0.95));  // demoted to rank 3 under floor 0.9
    h.append(mkRed(1, 0.5));   // stays at rank 2 → wins
    expect(h.pickCommitted(0.9)?.iteration).toBe(1);
  });

  it("rejects floor outside [0,1]", () => {
    const h = new RunHistory();
    expect(() => h.pickCommitted(1.5)).toThrow(RangeError);
  });
});

describe("RunHistory.isOscillating", () => {
  it("false when fewer than 4 entries", () => {
    const h = new RunHistory();
    h.append(makeEntry({ iteration: 0, verdict: HealthVerdict.GREEN }));
    expect(h.isOscillating()).toBe(false);
  });

  it("true when a (config, decision) pair repeats in the window", () => {
    const h = new RunHistory();
    for (let i = 0; i < 4; i++) {
      const e = makeEntry({ iteration: i, verdict: HealthVerdict.YELLOW });
      e.decision = {
        ruleName: i % 2 === 0 ? "rule_a" : "rule_b",
        rationale: "",
        configDiff: {},
      };
      h.append(e);
    }
    expect(h.isOscillating()).toBe(true);
  });
});

describe("RunHistory.stopReason setter", () => {
  it("can be set and read", () => {
    const h = new RunHistory();
    h.stopReason = StopReason.GREEN;
    expect(h.stopReason).toBe(StopReason.GREEN);
    // Setting again is idempotent.
    h.stopReason = StopReason.CONVERGED;
    expect(h.stopReason).toBe(StopReason.CONVERGED);
  });
});
