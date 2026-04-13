import { describe, it, expect } from "vitest";
import {
  mean,
  std,
  sampleStd,
  percentile,
  iqr,
  median,
  entropy,
  pearson,
  ksTwoSample,
  chiSquaredTest,
  normalCdf,
  benfordExpected,
  createRng,
} from "../../src/core/stats.js";

describe("mean", () => {
  it("computes arithmetic mean", () => {
    expect(mean([1, 2, 3, 4, 5])).toBe(3);
  });

  it("returns null for empty array", () => {
    expect(mean([])).toBeNull();
  });
});

describe("std", () => {
  it("computes population standard deviation", () => {
    // std of [2, 4, 4, 4, 5, 5, 7, 9] = 2.0
    expect(std([2, 4, 4, 4, 5, 5, 7, 9])).toBe(2);
  });

  it("returns null for empty array", () => {
    expect(std([])).toBeNull();
  });

  it("returns 0 for identical values", () => {
    expect(std([5, 5, 5, 5])).toBe(0);
  });
});

describe("sampleStd", () => {
  it("returns null for < 2 values", () => {
    expect(sampleStd([1])).toBeNull();
    expect(sampleStd([])).toBeNull();
  });

  it("computes sample std (Bessel correction)", () => {
    const s = sampleStd([2, 4, 4, 4, 5, 5, 7, 9]);
    expect(s).not.toBeNull();
    // Sample std ≈ 2.138
    expect(s!).toBeCloseTo(2.138, 2);
  });
});

describe("percentile", () => {
  it("computes median (p=50)", () => {
    expect(percentile([1, 2, 3, 4, 5], 50)).toBe(3);
  });

  it("interpolates between values", () => {
    expect(percentile([1, 2, 3, 4], 50)).toBe(2.5);
  });

  it("handles p=0 and p=100", () => {
    expect(percentile([10, 20, 30], 0)).toBe(10);
    expect(percentile([10, 20, 30], 100)).toBe(30);
  });

  it("throws on empty array", () => {
    expect(() => percentile([], 50)).toThrow();
  });
});

describe("iqr", () => {
  it("computes interquartile range", () => {
    const sorted = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const result = iqr(sorted);
    // Q75 = 7.75, Q25 = 3.25 → IQR = 4.5
    expect(result).toBeCloseTo(4.5, 1);
  });
});

describe("median", () => {
  it("finds median of odd-length array", () => {
    expect(median([1, 2, 3, 4, 5])).toBe(3);
  });

  it("finds median of even-length array", () => {
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });
});

describe("entropy", () => {
  it("returns 0 for single value", () => {
    expect(entropy(new Map([["a", 10]]))).toBe(0);
  });

  it("returns 1 for two equally frequent values", () => {
    expect(entropy(new Map([["a", 5], ["b", 5]]))).toBeCloseTo(1.0, 5);
  });

  it("returns 0 for empty map", () => {
    expect(entropy(new Map())).toBe(0);
  });
});

describe("pearson", () => {
  it("returns 1 for perfect positive correlation", () => {
    expect(pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])).toBeCloseTo(1.0, 5);
  });

  it("returns -1 for perfect negative correlation", () => {
    expect(pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2])).toBeCloseTo(-1.0, 5);
  });

  it("returns null for < 3 values", () => {
    expect(pearson([1, 2], [3, 4])).toBeNull();
  });

  it("returns null for zero variance", () => {
    expect(pearson([5, 5, 5], [1, 2, 3])).toBeNull();
  });
});

describe("ksTwoSample", () => {
  it("returns 0 for identical samples", () => {
    const sorted = [1, 2, 3, 4, 5];
    const { statistic } = ksTwoSample(sorted, sorted);
    expect(statistic).toBe(0);
  });

  it("returns high statistic for very different distributions", () => {
    const { statistic } = ksTwoSample([1, 2, 3], [100, 200, 300]);
    expect(statistic).toBeGreaterThan(0.5);
  });

  it("handles empty arrays", () => {
    const { statistic, pValue } = ksTwoSample([], [1, 2, 3]);
    expect(statistic).toBe(0);
    expect(pValue).toBe(1);
  });
});

describe("chiSquaredTest", () => {
  it("returns 0 statistic for perfect fit", () => {
    const { statistic } = chiSquaredTest([10, 20, 30], [10, 20, 30]);
    expect(statistic).toBeCloseTo(0, 5);
  });

  it("returns positive statistic for poor fit", () => {
    const { statistic } = chiSquaredTest([50, 10, 5], [20, 20, 25]);
    expect(statistic).toBeGreaterThan(0);
  });
});

describe("normalCdf", () => {
  it("returns 0.5 at z=0", () => {
    expect(normalCdf(0)).toBeCloseTo(0.5, 3);
  });

  it("approaches 1 for large positive z", () => {
    expect(normalCdf(4)).toBeGreaterThan(0.999);
  });

  it("approaches 0 for large negative z", () => {
    expect(normalCdf(-4)).toBeLessThan(0.001);
  });
});

describe("benfordExpected", () => {
  it("returns 9 probabilities summing to 1", () => {
    const expected = benfordExpected();
    expect(expected.length).toBe(9);
    const sum = expected.reduce((a, b) => a + b, 0);
    expect(sum).toBeCloseTo(1.0, 5);
  });

  it("digit 1 is most frequent", () => {
    const expected = benfordExpected();
    expect(expected[0]!).toBeGreaterThan(expected[1]!);
  });
});

describe("createRng", () => {
  it("produces deterministic sequence", () => {
    const rng1 = createRng(42);
    const rng2 = createRng(42);
    for (let i = 0; i < 100; i++) {
      expect(rng1()).toBe(rng2());
    }
  });

  it("produces values in [0, 1)", () => {
    const rng = createRng(12345);
    for (let i = 0; i < 1000; i++) {
      const v = rng();
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  it("different seeds produce different sequences", () => {
    const rng1 = createRng(1);
    const rng2 = createRng(2);
    // Very unlikely to be equal
    let same = 0;
    for (let i = 0; i < 10; i++) {
      if (rng1() === rng2()) same++;
    }
    expect(same).toBeLessThan(10);
  });
});
