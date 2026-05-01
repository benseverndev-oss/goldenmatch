/**
 * Parity tests — verify TypeScript output matches Python for key transforms.
 *
 * These tests run both zero-config and configured transforms on known input
 * and verify the output matches expected golden values (derived from Python).
 */

import { describe, it, expect } from "vitest";
import {
  TransformEngine,
  makeConfig,
  registry,
  profileDataframe,
  diffDataframes,
  SchemaMapper,
  learnConfig,
} from "../../src/index.js";
import type { Row } from "../../src/index.js";

// -------------------------------------------------------------------
// Test data matching Python test fixtures
// -------------------------------------------------------------------

const MESSY_DATA: Row[] = [
  {
    name: "  John Smith  ",
    email: "JOHN@EXAMPLE.COM",
    phone: "(555) 123-4567",
    state: "Pennsylvania",
    signup_date: "03/15/2024",
    price: "$1,234.56",
    status: "active",
  },
  {
    name: "DR. JANE DOE",
    email: "  jane+work@gmail.com  ",
    phone: "555.987.6543",
    state: "CA",
    signup_date: "2024-01-20",
    price: "$99.99",
    status: "ACTIVE",
  },
  {
    name: "  Bob  ",
    email: "bob@test.com",
    phone: "+1-555-456-7890",
    state: "new york",
    signup_date: "Jan 5, 2023",
    price: "$0.50",
    status: "actve",
  },
];

describe("transform parity", () => {
  it("strip removes leading/trailing whitespace", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "name", ops: ["strip"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    expect(result.rows[0]!["name"]).toBe("John Smith");
    expect(result.rows[2]!["name"]).toBe("Bob");
  });

  it("lowercase converts to lowercase", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "email", ops: ["strip", "lowercase"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    expect(result.rows[0]!["email"]).toBe("john@example.com");
  });

  it("title_case capitalizes first letter of each word", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "name", ops: ["strip", "title_case"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    expect(result.rows[0]!["name"]).toBe("John Smith");
  });

  it("phone_digits extracts only digits", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "phone", ops: ["phone_digits"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    expect(result.rows[0]!["phone"]).toBe("5551234567");
    expect(result.rows[1]!["phone"]).toBe("5559876543");
  });

  it("currency_strip extracts numeric value", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "price", ops: ["currency_strip"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    expect(result.rows[0]!["price"]).toBe(1234.56);
    expect(result.rows[1]!["price"]).toBe(99.99);
    expect(result.rows[2]!["price"]).toBe(0.5);
  });

  it("email_normalize strips tags and gmail dots", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "email", ops: ["strip", "email_normalize"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    // jane+work@gmail.com -> jane@gmail.com (strip +tag, strip dots from gmail)
    expect(result.rows[1]!["email"]).toBe("jane@gmail.com");
  });

  it("state_abbreviate converts full name to 2-letter code", () => {
    const engine = new TransformEngine({
      transforms: [{ column: "state", ops: ["state_abbreviate"] }],
    });
    const result = engine.transformDf(MESSY_DATA);
    expect(result.rows[0]!["state"]).toBe("PA");
    expect(result.rows[1]!["state"]).toBe("CA"); // already abbreviated
  });

  it("null_standardize converts N/A variants to null", () => {
    const data: Row[] = [
      { val: "N/A" },
      { val: "null" },
      { val: "none" },
      { val: "" },
      { val: "real value" },
      { val: "nan" },
    ];
    const engine = new TransformEngine({
      transforms: [{ column: "val", ops: ["null_standardize"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["val"]).toBeNull();
    expect(result.rows[1]!["val"]).toBeNull();
    expect(result.rows[2]!["val"]).toBeNull();
    expect(result.rows[3]!["val"]).toBeNull();
    expect(result.rows[4]!["val"]).toBe("real value");
    expect(result.rows[5]!["val"]).toBeNull();
  });

  it("boolean_normalize maps yes/no/true/false variants", () => {
    const data: Row[] = [
      { active: "yes" },
      { active: "no" },
      { active: "TRUE" },
      { active: "0" },
      { active: "Y" },
    ];
    const engine = new TransformEngine({
      transforms: [{ column: "active", ops: ["boolean_normalize"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["active"]).toBe(true);
    expect(result.rows[1]!["active"]).toBe(false);
    expect(result.rows[2]!["active"]).toBe(true);
    expect(result.rows[3]!["active"]).toBe(false);
    expect(result.rows[4]!["active"]).toBe(true);
  });

  it("ssn_format normalizes to XXX-XX-XXXX", () => {
    const data: Row[] = [
      { ssn: "123456789" },
      { ssn: "123-45-6789" },
      { ssn: "12345" }, // invalid — preserved
    ];
    const engine = new TransformEngine({
      transforms: [{ column: "ssn", ops: ["ssn_format"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["ssn"]).toBe("123-45-6789");
    expect(result.rows[1]!["ssn"]).toBe("123-45-6789");
    expect(result.rows[2]!["ssn"]).toBe("12345");
  });

  it("ssn_mask redacts to ***-**-XXXX", () => {
    const data: Row[] = [{ ssn: "123-45-6789" }];
    const engine = new TransformEngine({
      transforms: [{ column: "ssn", ops: ["ssn_mask"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["ssn"]).toBe("***-**-6789");
  });

  it("zip_normalize pads to 5 digits and strips +4", () => {
    const data: Row[] = [
      { zip: "1234" },
      { zip: "90210-1234" },
      { zip: "12345" },
    ];
    const engine = new TransformEngine({
      transforms: [{ column: "zip", ops: ["zip_normalize"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["zip"]).toBe("01234");
    expect(result.rows[1]!["zip"]).toBe("90210");
    expect(result.rows[2]!["zip"]).toBe("12345");
  });

  it("strip_titles removes honorific prefixes", () => {
    const data: Row[] = [
      { name: "Dr. John Smith" },
      { name: "Mrs. Jane Doe" },
      { name: "Prof. Alan Turing" },
    ];
    const engine = new TransformEngine({
      transforms: [{ column: "name", ops: ["strip_titles"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["name"]).toBe("John Smith");
    expect(result.rows[1]!["name"]).toBe("Jane Doe");
    expect(result.rows[2]!["name"]).toBe("Alan Turing");
  });

  it("collapse_whitespace replaces multiple spaces", () => {
    const data: Row[] = [{ val: "hello    world   test" }];
    const engine = new TransformEngine({
      transforms: [{ column: "val", ops: ["collapse_whitespace"] }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["val"]).toBe("hello world test");
  });

  it("split_name splits 'First Last' into two columns", () => {
    const data: Row[] = [
      { name: "John Smith" },
      { name: "Jane" },
    ];
    const engine = new TransformEngine({
      splits: [{ source: "name", target: ["first_name", "last_name"], method: "split_name" }],
    });
    const result = engine.transformDf(data);
    expect(result.rows[0]!["first_name"]).toBe("John");
    expect(result.rows[0]!["last_name"]).toBe("Smith");
    expect(result.rows[1]!["first_name"]).toBe("Jane");
  });
});

describe("profiler parity", () => {
  it("infers email type correctly", () => {
    const data: Row[] = [
      { email: "test@example.com" },
      { email: "foo@bar.com" },
      { email: "hello@world.org" },
    ];
    const profile = profileDataframe(data);
    const emailCol = profile.columns.find((c) => c.name === "email");
    expect(emailCol?.inferredType).toBe("email");
  });

  it("infers zip type correctly", () => {
    const data: Row[] = [
      { zip: "12345" },
      { zip: "90210" },
      { zip: "02134" },
    ];
    const profile = profileDataframe(data);
    const zipCol = profile.columns.find((c) => c.name === "zip");
    expect(zipCol?.inferredType).toBe("zip");
  });

  it("infers numeric type from numbers", () => {
    const data: Row[] = [{ age: 25 }, { age: 30 }, { age: 35 }];
    const profile = profileDataframe(data);
    const ageCol = profile.columns.find((c) => c.name === "age");
    expect(ageCol?.inferredType).toBe("numeric");
  });
});

describe("differ parity", () => {
  it("detects column value changes", () => {
    const before: Row[] = [{ a: "hello", b: 1 }, { a: "world", b: 2 }];
    const after: Row[] = [{ a: "HELLO", b: 1 }, { a: "world", b: 3 }];
    const diff = diffDataframes(before, after);
    expect(diff.totalChanges).toBe(2);
    expect(diff.changedColumns).toContain("a");
    expect(diff.changedColumns).toContain("b");
  });

  it("detects added and removed columns", () => {
    const before: Row[] = [{ a: 1, b: 2 }];
    const after: Row[] = [{ a: 1, c: 3 }];
    const diff = diffDataframes(before, after);
    expect(diff.addedColumns).toEqual(["c"]);
    expect(diff.removedColumns).toEqual(["b"]);
  });
});

describe("schema mapper parity", () => {
  it("maps aliased column names", () => {
    const source: Row[] = [{ fname: "John", lname: "Smith", email_address: "j@e.com" }];
    const target: Row[] = [{ first_name: "", last_name: "", email: "" }];
    const mapper = new SchemaMapper();
    const mappings = mapper.map(source, target);
    const mapped = Object.fromEntries(mappings.map((m) => [m.source, m.target]));
    expect(mapped["fname"]).toBe("first_name");
    expect(mapped["lname"]).toBe("last_name");
    expect(mapped["email_address"]).toBe("email");
  });
});

describe("config learner parity", () => {
  it("suggests auto_apply transforms for string data", () => {
    const data: Row[] = [
      { name: "  John  ", status: "active" },
      { name: "  Jane  ", status: "ACTIVE" },
      { name: "  Bob  ", status: "actve" },
    ];
    const cfg = learnConfig(data);
    expect(cfg.transforms.length).toBeGreaterThan(0);
    // strip should be suggested since it's auto_apply for strings
    const nameOps = cfg.transforms.find((t) => t.column === "name")?.ops ?? [];
    expect(nameOps).toContain("strip");
  });
});

describe("transform registry parity", () => {
  it("has the expected core transform count", () => {
    // Python core registry: 76 transforms
    // Domain-specific transforms are registered when domain modules are imported
    expect(registry().size).toBeGreaterThanOrEqual(75);
  });

  it("all transforms have required metadata", () => {
    for (const [, info] of registry()) {
      expect(info.name).toBeTruthy();
      expect(typeof info.func).toBe("function");
      expect(info.inputTypes.length).toBeGreaterThan(0);
      expect(typeof info.autoApply).toBe("boolean");
      expect(typeof info.priority).toBe("number");
      expect(["expr", "series", "dataframe"]).toContain(info.mode);
    }
  });
});
