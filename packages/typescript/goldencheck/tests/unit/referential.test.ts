import { describe, expect, it } from "vitest";
import { TabularData } from "../../src/core/data.js";
import {
  autoDetectMappings,
  checkReferentialIntegrity,
  parseOn,
  referentialIntegrity,
} from "../../src/core/engine/referential.js";
import { Severity } from "../../src/core/types.js";

// Parity with packages/python/goldencheck/tests (engine/referential.py behavior).

function td(rows: Array<Record<string, unknown>>): TabularData {
  return new TabularData(rows);
}

describe("referential integrity", () => {
  it("parseOn handles 'child=parent' and bare 'col'", () => {
    expect(parseOn(["customer_id=id", "sku"])).toEqual([
      ["customer_id", "id"],
      ["sku", "sku"],
    ]);
  });

  it("auto-detects same-named unique+non-null parent keys", () => {
    const child = td([{ id: 1, ref: "a" }, { id: 2, ref: "b" }]);
    const parent = td([{ ref: "a" }, { ref: "b" }, { ref: "c" }]); // unique, non-null key
    // `id` on parent is absent; only `ref` is a shared parent key.
    expect(autoDetectMappings(child, parent)).toEqual([["ref", "ref"]]);
  });

  it("does NOT auto-detect a non-unique parent column as a key", () => {
    const child = td([{ ref: "a" }]);
    const parent = td([{ ref: "a" }, { ref: "a" }]); // duplicated -> not a key
    expect(autoDetectMappings(child, parent)).toEqual([]);
  });

  it("clean references -> INFO with cardinality", () => {
    const child = td([{ cust: 1 }, { cust: 2 }, { cust: 1 }]); // N side
    const parent = td([{ id: 1 }, { id: 2 }]); // unique key -> 1
    const f = checkReferentialIntegrity(child, parent, [["cust", "id"]]);
    expect(f).toHaveLength(1);
    expect(f[0]!.severity).toBe(Severity.INFO);
    expect(f[0]!.metadata["cardinality"]).toBe("N:1");
    expect(f[0]!.metadata["orphan_rows"]).toBe(0);
  });

  it("orphans above 1% -> ERROR with rate, distinct count, and samples", () => {
    // 4 rows, 2 orphaned (values 9, 8) -> 50% > 1% -> ERROR.
    const child = td([{ cust: 1 }, { cust: 2 }, { cust: 9 }, { cust: 8 }]);
    const parent = td([{ id: 1 }, { id: 2 }]);
    const f = checkReferentialIntegrity(child, parent, [["cust", "id"]]);
    expect(f).toHaveLength(1);
    expect(f[0]!.severity).toBe(Severity.ERROR);
    expect(f[0]!.affectedRows).toBe(2);
    expect(f[0]!.metadata["distinct_orphans"]).toBe(2);
    expect(f[0]!.metadata["orphan_rate"]).toBe(0.5);
    expect(f[0]!.sampleValues).toEqual(["9", "8"]);
  });

  it("a single orphan under 1% -> WARNING", () => {
    // 200 rows, 1 orphan -> 0.5% <= 1% -> WARNING.
    const childRows = Array.from({ length: 199 }, () => ({ cust: 1 }));
    childRows.push({ cust: 999 });
    const parent = td([{ id: 1 }]);
    const f = checkReferentialIntegrity(td(childRows), parent, [["cust", "id"]]);
    expect(f[0]!.severity).toBe(Severity.WARNING);
  });

  it("nulls in the FK are ignored (not counted as orphans)", () => {
    const child = td([{ cust: 1 }, { cust: null }, { cust: 2 }]);
    const parent = td([{ id: 1 }, { id: 2 }]);
    const f = checkReferentialIntegrity(child, parent, [["cust", "id"]]);
    expect(f[0]!.severity).toBe(Severity.INFO); // only non-null FKs considered
  });

  it("orchestrator emits a guidance INFO when no mapping can be inferred", () => {
    const child = td([{ a: 1 }]);
    const parent = td([{ b: 1 }]);
    const f = referentialIntegrity(child, parent, undefined);
    expect(f).toHaveLength(1);
    expect(f[0]!.severity).toBe(Severity.INFO);
    expect(f[0]!.column).toBe("__dataset__");
  });

  it("missing column -> WARNING (cannot check)", () => {
    const child = td([{ cust: 1 }]);
    const parent = td([{ id: 1 }]);
    const f = checkReferentialIntegrity(child, parent, [["nope", "id"]]);
    expect(f[0]!.severity).toBe(Severity.WARNING);
    expect(f[0]!.message).toContain("column not found");
  });
});
