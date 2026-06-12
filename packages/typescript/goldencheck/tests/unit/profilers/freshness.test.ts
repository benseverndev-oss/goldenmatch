import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { FreshnessProfiler } from "../../../src/core/profilers/freshness.js";

const checks = (fs: { check: string }[]) => new Set(fs.map((f) => f.check));

describe("FreshnessProfiler", () => {
  const profiler = new FreshnessProfiler();

  it("flags future-dated values (datetime)", () => {
    const data = new TabularData([
      { order_ts: "2020-01-01T00:00:00" },
      { order_ts: "2099-01-01T00:00:00" },
    ]);
    const findings = profiler.profile(data, "order_ts");
    expect(checks(findings).has("future_dated")).toBe(true);
    const f = findings.find((x) => x.check === "future_dated")!;
    expect(f.affectedRows).toBe(1);
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.confidence).toBe(0.7);
  });

  it("flags future-dated values (date)", () => {
    const data = new TabularData([{ d: "2020-06-01" }, { d: "2099-01-01" }]);
    expect(checks(profiler.profile(data, "d")).has("future_dated")).toBe(true);
  });

  it("is silent on old, non-update date columns", () => {
    const data = new TabularData([{ d: "2020-01-01" }, { d: "2021-06-01" }]);
    expect(profiler.profile(data, "d")).toEqual([]);
  });

  it("flags staleness on update/event columns", () => {
    // Far-past values on an update-named column; both well over a year old.
    const data = new TabularData([
      { updated_at: "2001-01-01" },
      { updated_at: "2000-12-27" },
    ]);
    const findings = profiler.profile(data, "updated_at");
    expect(checks(findings).has("stale_data")).toBe(true);
    const f = findings.find((x) => x.check === "stale_data")!;
    expect(f.severity).toBe(Severity.INFO);
    expect(f.confidence).toBe(0.5);
    expect(f.affectedRows).toBe(2);
  });

  it("does NOT flag an old non-update column as stale", () => {
    const data = new TabularData([{ birth_date: "2001-01-01" }]);
    expect(checks(profiler.profile(data, "birth_date")).has("stale_data")).toBe(false);
  });

  it("skips non-temporal columns", () => {
    const data = new TabularData([{ n: 1 }, { n: 2 }, { n: 3 }]);
    expect(profiler.profile(data, "n")).toEqual([]);
    const s = new TabularData([{ s: "a" }, { s: "b" }, { s: "c" }]);
    expect(profiler.profile(s, "s")).toEqual([]);
  });
});
