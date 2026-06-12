import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { ApproximateFDProfiler } from "../../../src/core/relations/approx-fd.js";

function nearFdData(n = 300): TabularData {
  const rows = Array.from({ length: n }, (_, i) => {
    const zip = i % 10;
    return { zip, city: `city_${zip}`, amt: (i * 13) % 97 };
  });
  for (const bad of [7 % n, 50 % n, 123 % n]) rows[bad]!.city = "WRONGCITY";
  return new TabularData(rows);
}

describe("ApproximateFDProfiler", () => {
  const profiler = new ApproximateFDProfiler();

  it("surfaces near-FD violations (zip -> city, 3 typos)", () => {
    const findings = profiler.profile(nearFdData());
    const f = findings.find(
      (x) => x.metadata["determinant"] === "zip" && x.metadata["dependent"] === "city",
    );
    expect(f).toBeDefined();
    expect(f!.check).toBe("fd_violation");
    expect(f!.severity).toBe(Severity.WARNING);
    expect(f!.confidence).toBe(0.7);
    expect(f!.metadata["violation_count"]).toBe(3);
    expect(f!.affectedRows).toBe(3);
    expect(f!.metadata["fd_confidence"] as number).toBeGreaterThanOrEqual(0.95);
  });

  it("is silent on a perfect (strict) FD — that's the strict profiler's job", () => {
    const rows = Array.from({ length: 300 }, (_, i) => {
      const zip = i % 10;
      return { zip, city: `c${zip}` };
    });
    expect(profiler.profile(new TabularData(rows))).toEqual([]);
  });

  it("guards against a near-unique determinant", () => {
    const data = new TabularData(
      Array.from({ length: 300 }, (_, i) => ({ id: i, grp: i % 4 })),
    );
    const findings = profiler.profile(data);
    expect(findings.every((f) => f.metadata["determinant"] !== "id")).toBe(true);
  });

  it("requires minimum support", () => {
    expect(profiler.profile(nearFdData(40))).toEqual([]);
  });
});
