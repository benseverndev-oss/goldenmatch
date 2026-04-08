import { describe, it, expect } from "vitest";
import {
  inferDtype,
  profileColumn,
  isNullLike,
} from "../../src/core/util/profile.js";

describe("isNullLike", () => {
  it("recognizes null, undefined, empty string, 'null', 'nan'", () => {
    expect(isNullLike(null)).toBe(true);
    expect(isNullLike(undefined)).toBe(true);
    expect(isNullLike("")).toBe(true);
    expect(isNullLike("   ")).toBe(true);
    expect(isNullLike("null")).toBe(true);
    expect(isNullLike("NaN")).toBe(true);
    expect(isNullLike("0")).toBe(false);
    expect(isNullLike(0)).toBe(false);
  });
});

describe("inferDtype", () => {
  it("detects integers", () => {
    expect(inferDtype(["1", "2", "3"])).toBe("integer");
    expect(inferDtype([1, 2, 3])).toBe("integer");
    expect(inferDtype(["-10", "0", "42"])).toBe("integer");
  });

  it("detects floats", () => {
    expect(inferDtype(["1.5", "2.7"])).toBe("float");
    expect(inferDtype([1.5, 2.7])).toBe("float");
    // Mixed int and float strings → float
    expect(inferDtype(["1", "2.5"])).toBe("float");
  });

  it("detects booleans", () => {
    expect(inferDtype(["true", "false", "true"])).toBe("boolean");
    expect(inferDtype([true, false])).toBe("boolean");
  });

  it("does not misclassify 0/1 as boolean without true/false present", () => {
    expect(inferDtype(["0", "1", "0"])).toBe("integer");
  });

  it("detects iso dates", () => {
    expect(inferDtype(["2024-01-01", "2025-06-15"])).toBe("date");
  });

  it("detects iso datetimes", () => {
    expect(inferDtype(["2024-01-01T12:00:00Z", "2025-06-15T09:30:00"])).toBe(
      "datetime"
    );
  });

  it("falls back to string", () => {
    expect(inferDtype(["foo", "bar"])).toBe("string");
    expect(inferDtype(["1", "foo"])).toBe("string");
  });

  it("returns string on all-null", () => {
    expect(inferDtype([null, undefined, ""])).toBe("string");
  });
});

describe("profileColumn", () => {
  it("computes null rate, unique rate, and samples", () => {
    const stats = profileColumn(["a", "b", "a", "", null, "c"]);
    expect(stats.valueCount).toBe(6);
    expect(stats.nullRate).toBeCloseTo(2 / 6, 5);
    // unique non-null = {a, b, c} = 3; unique_rate = 3/6
    expect(stats.uniqueRate).toBeCloseTo(3 / 6, 5);
    expect(stats.sampleValues).toEqual(["a", "b", "a", "c"]);
  });

  it("respects sample size", () => {
    const values = Array.from({ length: 1000 }, (_, i) => String(i));
    const stats = profileColumn(values, 10);
    expect(stats.sampleValues).toHaveLength(10);
    expect(stats.valueCount).toBe(1000);
  });

  it("handles empty input", () => {
    const stats = profileColumn([]);
    expect(stats).toEqual({
      nullRate: 0,
      uniqueRate: 0,
      valueCount: 0,
      sampleValues: [],
    });
  });
});
