import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { FunctionalDependencyProfiler } from "../../../src/core/relations/functional-dependency.js";

function lookupData(n = 120): TabularData {
  const zipToCity: Record<number, number> = { 0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4 };
  return new TabularData(
    Array.from({ length: n }, (_, i) => {
      const zip = i % 6;
      return { zip, city: zipToCity[zip]!, amt: (i * 7) % 50 };
    }),
  );
}

describe("FunctionalDependencyProfiler", () => {
  const profiler = new FunctionalDependencyProfiler();

  it("discovers a strict FD (zip -> city)", () => {
    const findings = profiler.profile(lookupData());
    const f = findings.find((x) => x.metadata["determinant"] === "zip");
    expect(f).toBeDefined();
    expect(f!.check).toBe("functional_dependency");
    expect(f!.severity).toBe(Severity.INFO);
    expect(f!.metadata["dependents"] as string[]).toEqual(["city"]);
    expect(f!.column).toBe("zip");
    expect(f!.confidence).toBe(0.55);
  });

  it("reports nothing for independent columns", () => {
    const data = new TabularData(
      Array.from({ length: 120 }, (_, i) => ({ a: i % 5, b: (i * 3) % 7 })),
    );
    expect(profiler.profile(data)).toEqual([]);
  });

  it("requires minimum support", () => {
    expect(profiler.profile(lookupData(10))).toEqual([]);
  });
});
