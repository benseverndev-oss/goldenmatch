import { describe, it, expect } from "vitest";
import {
  InMemoryProvider,
  inferSchemaFromRecords,
} from "../../src/core/providers/in-memory.js";
import {
  inferSchemaFromCsvText,
  inferSchemaFromJsonText,
} from "../../src/core/providers/file.js";
import {
  parseSchemaDefinition,
  SchemaParseError,
} from "../../src/core/providers/schema-file.js";

describe("InMemoryProvider", () => {
  it("extracts fields from records with inferred order", () => {
    const records = [
      { id: 1, name: "alice", email: "a@x.co" },
      { id: 2, name: "bob", email: "b@x.co" },
    ];
    const schema = new InMemoryProvider().extract(records, {
      sourceName: "users",
    });
    expect(schema.sourceName).toBe("users");
    expect(schema.fields.map((f) => f.name)).toEqual(["id", "name", "email"]);
    expect(schema.fields[0]!.dtype).toBe("integer");
    expect(schema.fields[2]!.sampleValues).toEqual(["a@x.co", "b@x.co"]);
  });

  it("honors explicit column order", () => {
    const records = [{ b: 1, a: 2 }];
    const schema = inferSchemaFromRecords(records, { columns: ["a", "b"] });
    expect(schema.fields.map((f) => f.name)).toEqual(["a", "b"]);
  });

  it("computes per-column null/unique rates", () => {
    const records = [
      { x: "a" },
      { x: "" },
      { x: "b" },
      { x: null as unknown as string },
    ];
    const schema = inferSchemaFromRecords(records);
    const f = schema.fields[0]!;
    expect(f.valueCount).toBe(4);
    expect(f.nullRate).toBeCloseTo(0.5, 5);
    // 2 unique non-null (a, b) / 4 total = 0.5
    expect(f.uniqueRate).toBeCloseTo(0.5, 5);
  });
});

describe("inferSchemaFromCsvText", () => {
  it("parses CSV and infers schema", () => {
    const csv = [
      "id,name,created_at,amount",
      "1,alice,2024-01-01,9.99",
      "2,bob,2024-02-01,19.95",
      "3,charlie,2024-03-01,2.50",
    ].join("\n");
    const schema = inferSchemaFromCsvText(csv, { sourceName: "orders" });
    expect(schema.sourceName).toBe("orders");
    expect(schema.fields.map((f) => f.name)).toEqual([
      "id",
      "name",
      "created_at",
      "amount",
    ]);
    expect(schema.fields[0]!.dtype).toBe("integer");
    expect(schema.fields[1]!.dtype).toBe("string");
    expect(schema.fields[2]!.dtype).toBe("date");
    expect(schema.fields[3]!.dtype).toBe("float");
  });
});

describe("inferSchemaFromJsonText", () => {
  it("parses a JSON array of records", () => {
    const json = JSON.stringify([
      { a: 1, b: "x" },
      { a: 2, b: "y" },
    ]);
    const schema = inferSchemaFromJsonText(json);
    expect(schema.fields.map((f) => f.name)).toEqual(["a", "b"]);
  });

  it("throws on non-array root", () => {
    expect(() => inferSchemaFromJsonText('{"a":1}')).toThrow(TypeError);
  });
});

describe("parseSchemaDefinition", () => {
  it("parses a JSON schema with aliases and required flags", () => {
    const schema = parseSchemaDefinition(
      JSON.stringify({
        fields: [
          {
            name: "customer_id",
            dtype: "string",
            aliases: ["cust_id", "customer_number"],
            required: true,
          },
          { name: "email", dtype: "string" },
        ],
      })
    );
    expect(schema.fields).toHaveLength(2);
    expect(schema.fields[0]!.metadata["aliases"]).toEqual([
      "cust_id",
      "customer_number",
    ]);
    expect(schema.requiredFields).toEqual(["customer_id"]);
    expect(schema.fields[1]!.metadata["aliases"]).toBeUndefined();
  });

  it("accepts a pre-parsed object", () => {
    const schema = parseSchemaDefinition({
      fields: [{ name: "id" }],
    });
    expect(schema.fields[0]!.name).toBe("id");
    expect(schema.fields[0]!.dtype).toBe("string");
  });

  it("throws on missing fields key", () => {
    expect(() => parseSchemaDefinition('{"something":"else"}')).toThrow(
      SchemaParseError
    );
  });

  it("throws on non-array fields", () => {
    expect(() => parseSchemaDefinition('{"fields":"nope"}')).toThrow(
      SchemaParseError
    );
  });

  it("throws on field without name", () => {
    expect(() =>
      parseSchemaDefinition('{"fields":[{"dtype":"string"}]}')
    ).toThrow(SchemaParseError);
  });
});
