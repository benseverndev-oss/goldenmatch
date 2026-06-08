import { describe, expect, it } from "vitest";
import {
  baselineValue,
  defaultPolicy,
  deltaPct,
  isRegression,
  policyThreshold,
} from "../../src/core/regressions.js";

const HEALTHY = [0.97, 0.96, 0.98, 0.97, 0.97, 0.96, 0.97];

describe("baseline strategies", () => {
  it("rolling_median is the median of the window; previous is the last value", () => {
    expect(baselineValue(HEALTHY, "rolling_median", 7)).toBe(0.97);
    expect(baselineValue(HEALTHY, "previous")).toBe(0.97);
  });

  it("empty history has no baseline", () => {
    expect(baselineValue([], "rolling_median")).toBeNull();
  });

  it("rolling_median ignores one noisy night where previous would not", () => {
    const noisy = [0.97, 0.97, 0.97, 0.5, 0.97, 0.97, 0.97];
    expect(baselineValue(noisy, "rolling_median", 7)).toBe(0.97); // median unmoved
    expect(baselineValue(noisy, "previous")).toBe(0.97);
  });
});

describe("isRegression", () => {
  it("recall flags under a per-metric 2% gate that a global 10% gate misses", () => {
    const policy = { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } };
    // higher_better: 0.97 -> 0.89 is -8.2%
    expect(isRegression("higher_better", 0.97, 0.89, policyThreshold(policy, "match.recall_safe_bound"))).toBe(true);
    expect(isRegression("higher_better", 0.97, 0.89, policyThreshold(policy, "anything_else"))).toBe(false);
  });

  it("is direction-aware", () => {
    expect(isRegression("higher_better", 0.5, 0.9, 5)).toBe(false); // a rise is fine
    expect(isRegression("lower_better", 100, 130, 10)).toBe(true); // only flags on a rise
    expect(isRegression("lower_better", 100, 70, 10)).toBe(false);
    expect(isRegression("neutral", 0.58, 0.71, 10)).toBe(true); // +22.4%
    expect(isRegression("neutral", 0.71, 0.58, 10)).toBe(true);
  });

  it("ignores noise wobble under the gate", () => {
    expect(isRegression("neutral", 1.0, 1.03, 10)).toBe(false); // +3% < 10%
  });
});

describe("helpers", () => {
  it("deltaPct guards a zero baseline", () => {
    expect(deltaPct(0, 5)).toBe(0);
    expect(deltaPct(100, 130)).toBeCloseTo(30, 9);
  });

  it("defaultPolicy is a 10% global gate with no per-metric overrides", () => {
    expect(defaultPolicy()).toEqual({ defaultPct: 10, perMetric: {} });
  });

  it("policyThreshold falls back to defaultPct", () => {
    const policy = { defaultPct: 10, perMetric: { a: 2 } };
    expect(policyThreshold(policy, "a")).toBe(2);
    expect(policyThreshold(policy, "b")).toBe(10);
  });
});
