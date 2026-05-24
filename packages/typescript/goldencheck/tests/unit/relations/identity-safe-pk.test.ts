/**
 * Tests for IdentitySafePkProfiler (closes goldenmatch #207).
 * Ported from tests/relations/test_identity_safe_pk.py.
 */
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { IdentitySafePkProfiler } from "../../../src/core/relations/identity-safe-pk.js";
import { RELATION_PROFILERS } from "../../../src/core/relations/index.js";

const profiler = new IdentitySafePkProfiler();

describe("IdentitySafePkProfiler", () => {
  it("clean 'id' column: no warning", () => {
    const data = new TabularData([
      { id: 1, name: "Alice", email: "a@x" },
      { id: 2, name: "Bob", email: "b@x" },
      { id: 3, name: "Carol", email: "c@x" },
      { id: 4, name: "Dave", email: "d@x" },
      { id: 5, name: "Eve", email: "e@x" },
    ]);
    expect(profiler.profile(data)).toEqual([]);
  });

  it("string UUIDs in 'guid' column: no warning", () => {
    const data = new TabularData([
      { guid: "a1", value: 10 },
      { guid: "b2", value: 20 },
      { guid: "c3", value: 30 },
    ]);
    expect(profiler.profile(data)).toEqual([]);
  });

  it("no PK column: dataset-level WARNING", () => {
    const data = new TabularData([
      { first_name: "Alice", last_name: "Smith", city: "NYC" },
      { first_name: "Alice", last_name: "Smith", city: "NYC" },
      { first_name: "Bob", last_name: "Jones", city: "LA" },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBe(1);
    const f = findings[0]!;
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.check).toBe("identity_safe_pk");
    expect(f.column).toBe("__dataset__");
    expect(f.message.includes("PK")).toBe(true);
    expect(f.suggestion).toContain("source_pk_column");
  });

  it("named PK column with nulls: column-specific WARNING", () => {
    const data = new TabularData([
      { customer_id: 1, name: "A" },
      { customer_id: 2, name: "B" },
      { customer_id: null, name: "C" },
      { customer_id: 4, name: "D" },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBe(1);
    const f = findings[0]!;
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.column).toBe("customer_id");
    expect(f.message.toLowerCase()).toContain("null");
  });

  it("named PK column with duplicates: column-specific WARNING", () => {
    const data = new TabularData([
      { record_id: 1, city: "NYC" },
      { record_id: 1, city: "LA" },
      { record_id: 2, city: "NYC" },
      { record_id: 3, city: "LA" },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBe(1);
    const f = findings[0]!;
    expect(f.column).toBe("record_id");
    expect(f.message.toLowerCase()).toContain("unique");
  });

  it("unique value column (email) does not qualify", () => {
    const data = new TabularData([
      { email: "a@x", name: "Alice" },
      { email: "b@x", name: "Bob" },
      { email: "c@x", name: "Carol" },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBe(1);
    expect(findings[0]!.check).toBe("identity_safe_pk");
  });

  it("float column not eligible, but unique 'label' qualifies", () => {
    const data = new TabularData([
      { score: 0.1, label: "a" },
      { score: 0.2, label: "b" },
      { score: 0.3, label: "c" },
      { score: 0.4, label: "d" },
    ]);
    expect(profiler.profile(data)).toEqual([]);
  });

  it("boolean column not eligible: dataset-level warning", () => {
    const data = new TabularData([
      { is_active: true, city: "NYC" },
      { is_active: false, city: "LA" },
      { is_active: true, city: "NYC" },
      { is_active: false, city: "LA" },
    ]);
    const findings = profiler.profile(data);
    expect(findings.length).toBe(1);
    expect(findings[0]!.check).toBe("identity_safe_pk");
  });

  it("empty dataframe: no findings", () => {
    const data = new TabularData([]);
    expect(profiler.profile(data)).toEqual([]);
  });

  it("multiple PK candidates: no warning", () => {
    const data = new TabularData([
      { id: 1, sku: "a", name: "A" },
      { id: 2, sku: "b", name: "B" },
      { id: 3, sku: "c", name: "C" },
    ]);
    expect(profiler.profile(data)).toEqual([]);
  });

  it("is registered in RELATION_PROFILERS", () => {
    expect(RELATION_PROFILERS.some((p) => p instanceof IdentitySafePkProfiler)).toBe(true);
  });
});
