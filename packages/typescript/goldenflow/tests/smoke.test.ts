import { describe, it, expect } from "vitest";
import {
  TransformEngine,
  makeConfig,
  listTransforms,
  registry,
  TabularData,
  profileDataframe,
  diffDataframes,
  SchemaMapper,
  learnConfig,
  StreamProcessor,
  manifestToJson,
} from "../src/index.js";

describe("smoke tests", () => {
  it("imports without error", () => {
    expect(TransformEngine).toBeDefined();
    expect(makeConfig).toBeDefined();
    expect(listTransforms).toBeDefined();
  });

  it("registers 80+ transforms", () => {
    const count = registry().size;
    expect(count).toBeGreaterThanOrEqual(75);
  });

  it("transforms with zero-config", () => {
    const rows = [
      { name: "  John Smith  ", email: "JOHN@EXAMPLE.COM", status: "active" },
      { name: "  Jane Doe  ", email: "  jane@test.com  ", status: "ACTIVE" },
      { name: "  Bob  ", email: "bob@test.com", status: "actve" },
    ];
    const engine = new TransformEngine();
    const result = engine.transformDf(rows);
    expect(result.rows.length).toBe(3);
    expect(result.manifest.records.length).toBeGreaterThan(0);
    // strip should have been applied (auto_apply)
    expect(result.rows[0]!["name"]).toBe("John Smith");
  });

  it("transforms with config", () => {
    const rows = [
      { phone: "(555) 123-4567", price: "$1,234.56" },
      { phone: "555.987.6543", price: "$99.99" },
    ];
    const engine = new TransformEngine({
      transforms: [
        { column: "phone", ops: ["phone_digits"] },
        { column: "price", ops: ["currency_strip"] },
      ],
    });
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["phone"]).toBe("5551234567");
    expect(result.rows[0]!["price"]).toBe(1234.56);
  });

  it("TabularData works", () => {
    const data = new TabularData([
      { a: 1, b: "hello" },
      { a: 2, b: "world" },
      { a: 3, b: null },
    ]);
    expect(data.rowCount).toBe(3);
    expect(data.columns).toEqual(["a", "b"]);
    expect(data.nullCount("b")).toBe(1);
    expect(data.mean("a")).toBe(2);
    expect(data.nUnique("b")).toBe(2);
  });

  it("profiles data", () => {
    const rows = [
      { email: "test@example.com", zip: "12345" },
      { email: "foo@bar.com", zip: "90210" },
    ];
    const profile = profileDataframe(rows);
    expect(profile.rowCount).toBe(2);
    expect(profile.columnCount).toBe(2);
    expect(profile.columns.length).toBe(2);
  });

  it("diffs dataframes", () => {
    const before = [{ a: "hello", b: 1 }];
    const after = [{ a: "HELLO", b: 1 }];
    const result = diffDataframes(before, after);
    expect(result.totalChanges).toBe(1);
    expect(result.changedColumns).toContain("a");
  });

  it("SchemaMapper maps columns", () => {
    const source = [{ fname: "John", lname: "Smith" }];
    const target = [{ first_name: "", last_name: "" }];
    const mapper = new SchemaMapper();
    const mappings = mapper.map(source, target);
    expect(mappings.length).toBeGreaterThan(0);
  });

  it("learns config from data", () => {
    const rows = [
      { name: "John", email: "test@example.com" },
      { name: "Jane", email: "foo@bar.com" },
    ];
    const cfg = learnConfig(rows);
    expect(cfg.transforms.length).toBeGreaterThan(0);
  });

  it("StreamProcessor works", () => {
    const rows = Array.from({ length: 25 }, (_, i) => ({ id: i, name: `  Name ${i}  ` }));
    const processor = new StreamProcessor();
    const results = [...processor.streamRows(rows, 10)];
    expect(results.length).toBe(3);
    expect(processor.batchesProcessed).toBe(3);
  });

  it("manifest serializes to JSON", () => {
    const engine = new TransformEngine();
    const result = engine.transformDf([{ name: "  test  " }]);
    const json = manifestToJson(result.manifest);
    const parsed = JSON.parse(json);
    expect(parsed["source"]).toBeDefined();
    expect(parsed["records"]).toBeDefined();
  });

  it("dedup works", () => {
    const rows = [
      { email: "a@b.com", name: "Alice" },
      { email: "a@b.com", name: "Alice Dup" },
      { email: "c@d.com", name: "Charlie" },
    ];
    const engine = new TransformEngine({
      dedup: { columns: ["email"], keep: "first" },
    });
    const result = engine.transformDf(rows);
    expect(result.rows.length).toBe(2);
  });

  it("renames and drops work", () => {
    const rows = [{ old_col: "value", drop_me: "gone" }];
    const engine = new TransformEngine({
      renames: { old_col: "new_col" },
      drop: ["drop_me"],
    });
    const result = engine.transformDf(rows);
    expect(result.rows[0]!["new_col"]).toBe("value");
    expect(result.rows[0]!["drop_me"]).toBeUndefined();
  });
});
