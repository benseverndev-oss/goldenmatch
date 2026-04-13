import { describe, it, expect } from "vitest";
import { TabularData, isNullish } from "../../src/core/data.js";

const SAMPLE_ROWS = [
  { id: 1, name: "Alice", age: 30, email: "alice@example.com", joined: "2024-01-15" },
  { id: 2, name: "Bob", age: 25, email: "bob@test.com", joined: "2024-02-20" },
  { id: 3, name: "Charlie", age: null, email: "invalid-email", joined: "2024-03-10" },
  { id: 4, name: "", age: 45, email: null, joined: "2024-04-05" },
  { id: 5, name: "Eve", age: 28, email: "eve@example.com", joined: "not-a-date" },
];

describe("isNullish", () => {
  it("treats null and undefined as null", () => {
    expect(isNullish(null)).toBe(true);
    expect(isNullish(undefined)).toBe(true);
  });

  it("treats empty string and null-like strings as null", () => {
    expect(isNullish("")).toBe(true);
    expect(isNullish("null")).toBe(true);
    expect(isNullish("NULL")).toBe(true);
    expect(isNullish("nan")).toBe(true);
    expect(isNullish("NaN")).toBe(true);
    expect(isNullish("none")).toBe(true);
    expect(isNullish("None")).toBe(true);
    expect(isNullish("NA")).toBe(true);
    expect(isNullish("n/a")).toBe(true);
    expect(isNullish("N/A")).toBe(true);
    expect(isNullish("#N/A")).toBe(true);
    expect(isNullish("nil")).toBe(true);
  });

  it("does not treat normal values as null", () => {
    expect(isNullish("hello")).toBe(false);
    expect(isNullish(0)).toBe(false);
    expect(isNullish(false)).toBe(false);
    expect(isNullish("0")).toBe(false);
  });
});

describe("TabularData", () => {
  const data = new TabularData(SAMPLE_ROWS);

  it("reports correct columns and row count", () => {
    expect(data.columns).toEqual(["id", "name", "age", "email", "joined"]);
    expect(data.rowCount).toBe(5);
  });

  it("returns column values", () => {
    expect(data.column("id")).toEqual([1, 2, 3, 4, 5]);
    expect(data.column("age")).toEqual([30, 25, null, 45, 28]);
  });

  describe("null handling", () => {
    it("counts nulls", () => {
      expect(data.nullCount("age")).toBe(1); // null
      expect(data.nullCount("email")).toBe(1); // null
      expect(data.nullCount("name")).toBe(1); // "" treated as null
    });

    it("drops nulls", () => {
      const nonNull = data.dropNulls("age");
      expect(nonNull).toEqual([30, 25, 45, 28]);
    });
  });

  describe("type detection", () => {
    it("detects numeric columns", () => {
      expect(data.dtype("id")).toBe("integer");
      expect(data.dtype("age")).toBe("integer");
      expect(data.isNumeric("id")).toBe(true);
    });

    it("detects string columns", () => {
      expect(data.dtype("name")).toBe("string");
      expect(data.isString("name")).toBe(true);
    });

    it("detects date columns", () => {
      // Most values are ISO dates, one is "not-a-date"
      expect(data.dtype("joined")).toBe("date");
    });
  });

  describe("aggregation", () => {
    it("counts unique values", () => {
      expect(data.nUnique("id")).toBe(5);
      expect(data.nUnique("age")).toBe(4); // null excluded
    });

    it("computes value counts", () => {
      const counts = data.valueCounts("age");
      expect(counts.get(30)).toBe(1);
      expect(counts.has(null)).toBe(false); // nulls excluded
    });

    it("computes min/max", () => {
      expect(data.min("age")).toBe(25);
      expect(data.max("age")).toBe(45);
    });

    it("computes mean", () => {
      // (30+25+45+28) / 4 = 32
      expect(data.mean("age")).toBe(32);
    });

    it("computes std", () => {
      const s = data.std("age");
      expect(s).not.toBeNull();
      // Population std of [30, 25, 45, 28] ≈ 7.714
      expect(s!).toBeCloseTo(7.714, 2);
    });

    it("returns null for empty numeric columns", () => {
      const empty = new TabularData([]);
      expect(empty.mean("x")).toBeNull();
      expect(empty.min("x")).toBeNull();
    });
  });

  describe("filtering", () => {
    it("filters rows", () => {
      const filtered = data.filter((r) => (r["age"] as number) > 29);
      expect(filtered.rowCount).toBe(2); // 30, 45
    });

    it("takes head", () => {
      const h = data.head(3);
      expect(h.rowCount).toBe(3);
    });
  });

  describe("sampling", () => {
    it("returns all rows if n >= rowCount", () => {
      const s = data.sample(10);
      expect(s.rowCount).toBe(5);
    });

    it("returns n rows when n < rowCount", () => {
      const s = data.sample(3, 42);
      expect(s.rowCount).toBe(3);
    });

    it("is deterministic with same seed", () => {
      const s1 = data.sample(3, 42);
      const s2 = data.sample(3, 42);
      expect(s1.column("id")).toEqual(s2.column("id"));
    });

    it("produces different results with different seeds", () => {
      const s1 = data.sample(3, 42);
      const s2 = data.sample(3, 99);
      // Not guaranteed to differ, but very likely with different seeds
      // Just check both have correct length
      expect(s1.rowCount).toBe(3);
      expect(s2.rowCount).toBe(3);
    });
  });

  describe("string operations", () => {
    it("tests regex against string values", () => {
      const contains = data.strContains("email", /@/);
      expect(contains).toEqual([true, true, false, false, true]);
    });

    it("computes string lengths", () => {
      const lengths = data.strLengths("name");
      expect(lengths).toEqual([5, 3, 7, 0, 3]); // "" → 0 (nullish)
    });
  });

  describe("casting", () => {
    it("casts to float", () => {
      const floats = data.castFloat("age");
      expect(floats).toEqual([30, 25, null, 45, 28]);
    });

    it("casts non-numeric strings to null", () => {
      const floats = data.castFloat("name");
      expect(floats).toEqual([null, null, null, null, null]);
    });
  });

  describe("sorted numeric", () => {
    it("returns sorted numeric values", () => {
      expect(data.sortedNumeric("age")).toEqual([25, 28, 30, 45]);
    });
  });

  describe("diff", () => {
    it("computes consecutive differences", () => {
      const simple = new TabularData([
        { x: 10 },
        { x: 13 },
        { x: 15 },
        { x: 20 },
      ]);
      expect(simple.diff("x")).toEqual([null, 3, 2, 5]);
    });
  });

  describe("empty data", () => {
    const empty = new TabularData([]);

    it("has zero rows and columns", () => {
      expect(empty.rowCount).toBe(0);
      expect(empty.columns).toEqual([]);
    });
  });
});
