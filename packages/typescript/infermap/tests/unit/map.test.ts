import { describe, it, expect } from "vitest";
import { map, toSchemaInfo } from "../../src/core/map.js";
import {
  makeFieldInfo,
  makeSchemaInfo,
} from "../../src/core/types.js";

describe("toSchemaInfo", () => {
  it("passes through a SchemaInfo", () => {
    const s = makeSchemaInfo({ fields: [makeFieldInfo({ name: "id" })] });
    expect(toSchemaInfo(s)).toBe(s);
  });

  it("accepts records", () => {
    const s = toSchemaInfo({
      records: [{ id: 1, email: "a@b.co" }],
      sourceName: "x",
    });
    expect(s.fields.map((f) => f.name)).toEqual(["id", "email"]);
    expect(s.sourceName).toBe("x");
  });

  it("accepts csvText", () => {
    const s = toSchemaInfo({ csvText: "id,name\n1,alice\n2,bob\n" });
    expect(s.fields.map((f) => f.name)).toEqual(["id", "name"]);
  });

  it("accepts jsonText", () => {
    const s = toSchemaInfo({
      jsonText: JSON.stringify([{ a: 1 }, { a: 2 }]),
    });
    expect(s.fields[0]!.name).toBe("a");
  });

  it("accepts schemaDefinition", () => {
    const s = toSchemaInfo({
      schemaDefinition: JSON.stringify({
        fields: [{ name: "id", required: true }],
      }),
    });
    expect(s.fields[0]!.name).toBe("id");
    expect(s.requiredFields).toEqual(["id"]);
  });
});

describe("map", () => {
  it("maps polymorphic inputs end-to-end", () => {
    const result = map(
      { csvText: "fname,lname,email\nalice,smith,a@x.co\nbob,jones,b@x.co\n" },
      {
        csvText:
          "first_name,last_name,email\nalice,smith,a@x.co\nbob,jones,b@x.co\n",
      }
    );
    const pairs = new Map(result.mappings.map((m) => [m.source, m.target]));
    expect(pairs.get("fname")).toBe("first_name");
    expect(pairs.get("lname")).toBe("last_name");
    expect(pairs.get("email")).toBe("email");
  });

  it("applies engine config — disables scorers and extends aliases", () => {
    const result = map(
      { records: [{ ord_no: 1 }, { ord_no: 2 }] },
      { records: [{ order_id: 1 }, { order_id: 2 }] },
      {
        config: {
          aliases: { order_id: ["ord_no"] },
        },
      }
    );
    expect(result.mappings).toHaveLength(1);
    expect(result.mappings[0]!.source).toBe("ord_no");
    expect(result.mappings[0]!.target).toBe("order_id");
  });

  it("forwards required + schemaFile options", () => {
    const result = map(
      { records: [{ aaa: 1 }] },
      {
        records: [
          { first_name: "x", email: "e@e.co" },
          { first_name: "y", email: "f@e.co" },
        ],
      },
      {
        required: ["email"],
        engineOptions: { minConfidence: 0.99 },
      }
    );
    expect(result.warnings.some((w) => w.includes("email"))).toBe(true);
  });
});
