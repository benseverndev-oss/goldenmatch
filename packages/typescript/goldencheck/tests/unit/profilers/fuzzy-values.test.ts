import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { FuzzyValuesProfiler } from "../../../src/core/profilers/fuzzy-values.js";

function stateData(n = 120): TabularData {
  const variants = ["California", "Californa", "CALIFORNIA", "Texas", "New York"];
  const rows = Array.from({ length: n }, (_, i) => ({
    state: variants[i % variants.length]!,
    clean: ["apple", "banana", "cherry"][i % 3]!,
  }));
  return new TabularData(rows);
}

describe("FuzzyValuesProfiler", () => {
  const profiler = new FuzzyValuesProfiler();

  it("flags near-duplicate value variants", () => {
    const findings = profiler.profile(stateData(), "state");
    expect(findings.length).toBeGreaterThan(0);
    const f = findings[0]!;
    expect(f.check).toBe("fuzzy_duplicate_values");
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.confidence).toBe(0.6);
    const variants = new Set(f.metadata["variants"] as string[]);
    expect(variants.has("California")).toBe(true);
    expect(variants.has("Californa")).toBe(true);
    expect(variants.has("CALIFORNIA")).toBe(true);
    // 120 rows cycling through 5 variants; the cluster is the 3 California
    // spellings (positions 0,1,2 of every 5) = 3/5 * 120 = 72 rows.
    expect(f.affectedRows).toBe(72);
  });

  it("is silent on a clean categorical column", () => {
    expect(profiler.profile(stateData(), "clean")).toEqual([]);
  });

  it("skips non-string columns", () => {
    const data = new TabularData(Array.from({ length: 100 }, (_, i) => ({ n: i })));
    expect(profiler.profile(data, "n")).toEqual([]);
  });

  it("skips columns below the row floor", () => {
    expect(profiler.profile(stateData(10), "state")).toEqual([]);
  });
});
