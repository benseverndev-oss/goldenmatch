import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { ApproxDuplicateProfiler } from "../../../src/core/relations/approx-duplicate.js";

const checks = (fs: { check: string }[]) => new Set(fs.map((f) => f.check));

describe("ApproxDuplicateProfiler", () => {
  const profiler = new ApproxDuplicateProfiler();

  it("detects exact duplicate rows", () => {
    const data = new TabularData([
      { name: "Acme", city: "NYC" },
      { name: "Beta", city: "LA" },
      { name: "Acme", city: "NYC" },
      { name: "Gamma", city: "SF" },
    ]);
    const findings = profiler.profile(data);
    expect(checks(findings).has("duplicate_rows")).toBe(true);
    const f = findings.find((x) => x.check === "duplicate_rows")!;
    expect(f.affectedRows).toBe(2);
    expect(f.column).toBe("__dataset__");
    expect(f.metadata["duplicate_groups"]).toBe(1);
  });

  it("detects near-duplicate rows (case/whitespace/punct only)", () => {
    const data = new TabularData([
      { name: "Acme, Inc.", city: "New York" },
      { name: "acme inc", city: "new york" },
      { name: "Beta LLC", city: "Boston" },
    ]);
    const findings = profiler.profile(data);
    expect(checks(findings).has("near_duplicate_rows")).toBe(true);
    expect(findings.find((x) => x.check === "near_duplicate_rows")!.affectedRows).toBe(2);
  });

  it("does NOT also count exact dupes as near-dupes", () => {
    const data = new TabularData([
      { name: "Acme", city: "NYC" },
      { name: "Acme", city: "NYC" },
      { name: "Beta", city: "LA" },
    ]);
    const findings = profiler.profile(data);
    expect(checks(findings).has("duplicate_rows")).toBe(true);
    expect(checks(findings).has("near_duplicate_rows")).toBe(false);
  });

  it("is silent on clean data and trivial frames", () => {
    expect(
      profiler.profile(new TabularData([{ id: 1, name: "a" }, { id: 2, name: "b" }])),
    ).toEqual([]);
    expect(profiler.profile(new TabularData([{ a: 1 }]))).toEqual([]);
    expect(profiler.profile(new TabularData([]))).toEqual([]);
  });
});
