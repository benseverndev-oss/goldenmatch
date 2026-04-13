/**
 * TransformEngine edge cases and error handling tests.
 */

import { describe, it, expect } from "vitest";
import {
  TransformEngine,
  makeConfig,
  selectTransforms,
  profileDataframe,
  makeColumnProfile,
  getTransform,
} from "../../src/index.js";
import type { Row } from "../../src/index.js";

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe("TransformEngine — error handling", () => {
  it("records error for unknown transform name and does not crash", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "name", ops: ["nonexistent_transform"] }],
    });
    const rows: Row[] = [{ name: "Alice" }, { name: "Bob" }];
    const result = engine.transformDf(rows);
    // Should still return all rows
    expect(result.rows).toHaveLength(2);
    // Error should be recorded in manifest
    expect(result.manifest.errors.length).toBeGreaterThanOrEqual(1);
    const err = result.manifest.errors.find(
      (e) => e.transform === "nonexistent_transform",
    );
    expect(err).toBeDefined();
    expect(err!.error).toContain("not found");
  });

  it("handles empty input rows without crashing", () => {
    const engine = new TransformEngine();
    const result = engine.transformDf([]);
    expect(result.rows).toHaveLength(0);
    expect(result.columns).toHaveLength(0);
  });

  it("silently skips when config references a missing column", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "foo", ops: ["strip"] }],
    });
    const rows: Row[] = [{ bar: "hello" }];
    const result = engine.transformDf(rows);
    // Row should be unchanged, no crash
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0]!["bar"]).toBe("hello");
    // No errors for missing column (silently skipped)
    expect(result.manifest.errors).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Parameterized transforms
// ---------------------------------------------------------------------------

describe("TransformEngine — parameterized transforms", () => {
  it("truncate:10 truncates strings to 10 characters", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "text", ops: ["truncate:10"] }],
    });
    const rows: Row[] = [
      { text: "This is a very long string that should be truncated" },
      { text: "Short" },
    ];
    const result = engine.transformDf(rows);
    expect((result.rows[0]!["text"] as string).length).toBeLessThanOrEqual(10);
    expect(result.rows[1]!["text"]).toBe("Short");
  });
});

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

describe("TransformEngine — filters", () => {
  it("filter not_null removes rows where column is null", () => {
    const engine = new TransformEngine({
      filters: [{ column: "email", condition: "not_null" }],
    });
    const rows: Row[] = [
      { email: "a@b.com", name: "Alice" },
      { email: null, name: "Bob" },
      { email: "c@d.com", name: "Charlie" },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows).toHaveLength(2);
    expect(result.rows.every((r) => r["email"] !== null)).toBe(true);
  });

  it("filter after: date string comparison", () => {
    const engine = new TransformEngine({
      filters: [{ column: "date", condition: "after:2024-06-01" }],
    });
    const rows: Row[] = [
      { date: "2024-01-15", value: 1 },
      { date: "2024-07-20", value: 2 },
      { date: "2024-12-01", value: 3 },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows).toHaveLength(2);
    expect(result.rows[0]!["value"]).toBe(2);
    expect(result.rows[1]!["value"]).toBe(3);
  });

  it("filter before: date string comparison", () => {
    const engine = new TransformEngine({
      filters: [{ column: "date", condition: "before:2024-06-01" }],
    });
    const rows: Row[] = [
      { date: "2024-01-15", value: 1 },
      { date: "2024-07-20", value: 2 },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0]!["value"]).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Dedup
// ---------------------------------------------------------------------------

describe("TransformEngine — dedup", () => {
  it("dedup keep:last keeps the last occurrence", () => {
    const engine = new TransformEngine({
      dedup: { columns: ["email"], keep: "last" },
    });
    const rows: Row[] = [
      { email: "a@b.com", name: "First" },
      { email: "c@d.com", name: "Unique" },
      { email: "a@b.com", name: "Last" },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows).toHaveLength(2);
    // The last occurrence of a@b.com should be kept
    const aRow = result.rows.find((r) => r["email"] === "a@b.com");
    expect(aRow!["name"]).toBe("Last");
  });
});

// ---------------------------------------------------------------------------
// Date transforms
// ---------------------------------------------------------------------------

describe("TransformEngine — date transforms", () => {
  it("date_iso8601 produces YYYY-MM-DD in UTC", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "d", ops: ["date_iso8601"] }],
    });
    const rows: Row[] = [{ d: "2024-01-15" }];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["d"]).toBe("2024-01-15");
  });

  it("extract_year produces an integer year", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "d", ops: ["extract_year"] }],
    });
    const rows: Row[] = [{ d: "2024-01-15" }, { d: "1999-12-31" }];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["d"]).toBe(2024);
    expect(result.rows[1]!["d"]).toBe(1999);
    expect(typeof result.rows[0]!["d"]).toBe("number");
  });
});

// ---------------------------------------------------------------------------
// Numeric transforms
// ---------------------------------------------------------------------------

describe("TransformEngine — numeric transforms", () => {
  it("round:2 rounds to 2 decimal places", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "val", ops: ["round:2"] }],
    });
    const rows: Row[] = [{ val: 3.14159 }, { val: 2.71828 }];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["val"]).toBe(3.14);
    expect(result.rows[1]!["val"]).toBe(2.72);
  });

  it("clamp:0:100 clamps values to range", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "val", ops: ["clamp:0:100"] }],
    });
    const rows: Row[] = [{ val: -10 }, { val: 50 }, { val: 200 }];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["val"]).toBe(0);
    expect(result.rows[1]!["val"]).toBe(50);
    expect(result.rows[2]!["val"]).toBe(100);
  });

  it("abs_value converts negative numbers to positive", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "val", ops: ["abs_value"] }],
    });
    const rows: Row[] = [{ val: -42 }, { val: 7 }, { val: 0 }];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["val"]).toBe(42);
    expect(result.rows[1]!["val"]).toBe(7);
    expect(result.rows[2]!["val"]).toBe(0);
  });

  it("fill_zero replaces null with 0", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "val", ops: ["fill_zero"] }],
    });
    const rows: Row[] = [{ val: null }, { val: 5 }, { val: null }];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["val"]).toBe(0);
    expect(result.rows[1]!["val"]).toBe(5);
    expect(result.rows[2]!["val"]).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Address transforms
// ---------------------------------------------------------------------------

describe("TransformEngine — address transforms", () => {
  it("address_standardize converts Street to St", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "addr", ops: ["address_standardize"] }],
    });
    const rows: Row[] = [
      { addr: "123 Main Street" },
      { addr: "456 Oak Avenue" },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["addr"]).toContain("St");
    expect(result.rows[0]!["addr"]).not.toContain("Street");
    expect(result.rows[1]!["addr"]).toContain("Ave");
    expect(result.rows[1]!["addr"]).not.toContain("Avenue");
  });
});

// ---------------------------------------------------------------------------
// Phone transforms
// ---------------------------------------------------------------------------

describe("TransformEngine — phone transforms", () => {
  it("phone_digits extracts only digits", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "phone", ops: ["phone_digits"] }],
    });
    const rows: Row[] = [
      { phone: "(555) 123-4567" },
      { phone: "555.987.6543" },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["phone"]).toBe("5551234567");
    expect(result.rows[1]!["phone"]).toBe("5559876543");
  });

  it("phone_validate returns booleans", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "phone", ops: ["phone_validate"] }],
    });
    const rows: Row[] = [
      { phone: "(555) 123-4567" }, // valid 10-digit
      { phone: "123" }, // too short
    ];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["phone"]).toBe(true);
    expect(result.rows[1]!["phone"]).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Email transforms
// ---------------------------------------------------------------------------

describe("TransformEngine — email transforms", () => {
  it("email_validate returns booleans for valid and invalid emails", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "email", ops: ["email_validate"] }],
    });
    const rows: Row[] = [
      { email: "user@example.com" },
      { email: "not-an-email" },
      { email: "also@valid.org" },
    ];
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["email"]).toBe(true);
    expect(result.rows[1]!["email"]).toBe(false);
    expect(result.rows[2]!["email"]).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Selector
// ---------------------------------------------------------------------------

describe("selectTransforms — high-cardinality suppression", () => {
  it("excludes category_auto_correct when uniquePct > 0.1", () => {
    const profile = makeColumnProfile({
      name: "status",
      inferredType: "string",
      rowCount: 100,
      nullCount: 0,
      nullPct: 0,
      uniqueCount: 50,
      uniquePct: 0.5, // 50% unique — high cardinality
    });
    const selected = selectTransforms(profile);
    const names = selected.map((t) => t.name);
    expect(names).not.toContain("category_auto_correct");
  });

  it("includes category_auto_correct when uniquePct <= 0.1", () => {
    const profile = makeColumnProfile({
      name: "status",
      inferredType: "string",
      rowCount: 100,
      nullCount: 0,
      nullPct: 0,
      uniqueCount: 5,
      uniquePct: 0.05, // 5% unique — low cardinality
    });
    const selected = selectTransforms(profile);
    const names = selected.map((t) => t.name);
    expect(names).toContain("category_auto_correct");
  });
});

// ---------------------------------------------------------------------------
// Null propagation
// ---------------------------------------------------------------------------

describe("TransformEngine — null propagation", () => {
  it("zero-config on data with nulls in every column does not crash", () => {
    const rows: Row[] = [
      { name: null, email: null, phone: null, zip: null, amount: null },
      { name: "Alice", email: "a@b.com", phone: "5551234567", zip: "12345", amount: 99 },
      { name: null, email: null, phone: null, zip: null, amount: null },
    ];
    const engine = new TransformEngine();
    const result = engine.transformDf(rows);
    // Should not crash, should return same number of rows
    expect(result.rows).toHaveLength(3);
    // Nulls should remain null (not turned into strings or other values)
    expect(result.rows[0]!["name"]).toBeNull();
  });
});
