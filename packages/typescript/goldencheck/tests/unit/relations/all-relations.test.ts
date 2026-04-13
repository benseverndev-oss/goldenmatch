import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { TemporalOrderProfiler } from "../../../src/core/relations/temporal.js";
import { NullCorrelationProfiler } from "../../../src/core/relations/null-correlation.js";
import { NumericCrossColumnProfiler } from "../../../src/core/relations/numeric-cross.js";
import { AgeValidationProfiler } from "../../../src/core/relations/age-validation.js";
import { RELATION_PROFILERS } from "../../../src/core/relations/index.js";

describe("TemporalOrderProfiler", () => {
  const profiler = new TemporalOrderProfiler();

  it("detects start_date > end_date violations", () => {
    const data = new TabularData([
      { start_date: "2024-01-01", end_date: "2024-01-10" },
      { start_date: "2024-03-15", end_date: "2024-01-05" }, // violation
      { start_date: "2024-02-01", end_date: "2024-06-01" },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBeGreaterThan(0);
    expect(findings[0]!.check).toBe("temporal_order");
    expect(findings[0]!.severity).toBe(Severity.ERROR);
    expect(findings[0]!.affectedRows).toBe(1);
  });

  it("returns empty for valid ordering", () => {
    const data = new TabularData([
      { created_at: "2024-01-01", updated_at: "2024-01-10" },
      { created_at: "2024-02-01", updated_at: "2024-06-01" },
    ]);
    const findings = profiler.profile(data);
    expect(findings).toEqual([]);
  });

  it("matches multiple keyword pairs", () => {
    const data = new TabularData([
      { open_date: "2024-05-01", close_date: "2024-01-01" }, // violation
    ]);
    const findings = profiler.profile(data);
    expect(findings.some((f) => f.column.includes("open_date"))).toBe(true);
  });
});

describe("NullCorrelationProfiler", () => {
  const profiler = new NullCorrelationProfiler();

  it("detects correlated null patterns", () => {
    const rows = Array.from({ length: 100 }, (_, i) => ({
      a: i < 20 ? null : `val_${i}`,
      b: i < 20 ? null : `other_${i}`,
      c: `always_${i}`,
    }));
    const data = new TabularData(rows);
    const findings = profiler.profile(data);
    expect(findings.some((f) => f.check === "null_correlation")).toBe(true);
  });

  it("returns empty when no nulls", () => {
    const data = new TabularData(
      Array.from({ length: 50 }, (_, i) => ({ a: i, b: i * 2 })),
    );
    expect(profiler.profile(data)).toEqual([]);
  });
});

describe("NumericCrossColumnProfiler", () => {
  const profiler = new NumericCrossColumnProfiler();

  it("detects amount > limit violations", () => {
    const data = new TabularData([
      { amount: 50, credit_limit: 100 },
      { amount: 150, credit_limit: 100 }, // violation
      { amount: 30, credit_limit: 100 },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBeGreaterThan(0);
    expect(findings[0]!.check).toBe("cross_column_validation");
    expect(findings[0]!.column).toBe("amount"); // value column only
  });

  it("returns empty when constraints satisfied", () => {
    const data = new TabularData([
      { total_cost: 50, total_max: 100 },
      { total_cost: 80, total_max: 100 },
    ]);
    const findings = profiler.profile(data);
    expect(findings).toEqual([]);
  });
});

describe("AgeValidationProfiler", () => {
  const profiler = new AgeValidationProfiler();

  it("detects age/DOB mismatch", () => {
    const data = new TabularData([
      { age: 30, date_of_birth: "1994-01-01" },
      { age: 99, date_of_birth: "2000-05-15" }, // mismatch
    ]);
    const findings = profiler.profile(data);
    expect(findings.some((f) => f.check === "cross_column")).toBe(true);
  });

  it("excludes non-age columns (stage, voltage)", () => {
    const data = new TabularData([
      { stage: 1, dob: "1990-01-01" },
      { voltage: 120, dob: "1990-01-01" },
    ]);
    const findings = profiler.profile(data);
    expect(findings).toEqual([]);
  });

  it("returns empty when no age/DOB columns", () => {
    const data = new TabularData([
      { name: "Alice", score: 95 },
    ]);
    expect(profiler.profile(data)).toEqual([]);
  });
});

describe("RELATION_PROFILERS", () => {
  it("has exactly 4 profilers", () => {
    expect(RELATION_PROFILERS.length).toBe(4);
  });

  it("all run without error on simple data", () => {
    const data = new TabularData(
      Array.from({ length: 20 }, (_, i) => ({
        id: i,
        start_date: "2024-01-01",
        end_date: "2024-12-31",
        amount: i * 10,
        max_amount: 200,
      })),
    );
    for (const profiler of RELATION_PROFILERS) {
      const findings = profiler.profile(data);
      expect(Array.isArray(findings)).toBe(true);
    }
  });
});
