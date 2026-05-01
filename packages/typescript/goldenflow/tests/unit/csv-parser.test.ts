/**
 * CSV parser tests — exercises readFile / writeFile from the file connector.
 */

import { describe, it, expect, afterAll } from "vitest";
import { writeFileSync, unlinkSync, readdirSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { readFile, writeFile } from "../../src/node/connectors/file.js";

const TMP_DIR = join(import.meta.dirname, "../_tmp");
mkdirSync(TMP_DIR, { recursive: true });

function tmpPath(name: string): string {
  return join(TMP_DIR, name);
}

afterAll(() => {
  try {
    for (const f of readdirSync(TMP_DIR)) unlinkSync(join(TMP_DIR, f));
  } catch {
    /* ignore cleanup errors */
  }
});

// ---------------------------------------------------------------------------
// CSV read tests
// ---------------------------------------------------------------------------

describe("CSV parser — readFile", () => {
  it("reads a basic 2-row CSV", () => {
    const p = tmpPath("basic.csv");
    writeFileSync(p, "name,age\nAlice,30\nBob,25\n");
    const rows = readFile(p);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({ name: "Alice", age: 30 });
    expect(rows[1]).toEqual({ name: "Bob", age: 25 });
  });

  it("handles quoted fields with commas", () => {
    const p = tmpPath("quoted.csv");
    writeFileSync(p, 'name,city\n"Smith, John","Portland, OR"\n');
    const rows = readFile(p);
    expect(rows).toHaveLength(1);
    expect(rows[0]!["name"]).toBe("Smith, John");
    expect(rows[0]!["city"]).toBe("Portland, OR");
  });

  it("handles escaped quotes inside quoted fields", () => {
    const p = tmpPath("escaped.csv");
    writeFileSync(p, 'greeting,who\n"He said ""hello""","world"\n');
    const rows = readFile(p);
    expect(rows).toHaveLength(1);
    expect(rows[0]!["greeting"]).toBe('He said "hello"');
    expect(rows[0]!["who"]).toBe("world");
  });

  it("returns empty array for header-only CSV", () => {
    const p = tmpPath("empty.csv");
    writeFileSync(p, "a,b,c\n");
    const rows = readFile(p);
    expect(rows).toHaveLength(0);
  });

  it("preserves leading zeros as strings", () => {
    const p = tmpPath("leading_zero.csv");
    writeFileSync(p, "zip,code\n01234,00987\n");
    const rows = readFile(p);
    expect(rows[0]!["zip"]).toBe("01234");
    expect(rows[0]!["code"]).toBe("00987");
    // Ensure they are strings, not numbers
    expect(typeof rows[0]!["zip"]).toBe("string");
    expect(typeof rows[0]!["code"]).toBe("string");
  });

  it("coerces boolean strings to booleans", () => {
    const p = tmpPath("booleans.csv");
    writeFileSync(p, "a,b,c\ntrue,false,TRUE\n");
    const rows = readFile(p);
    expect(rows[0]!["a"]).toBe(true);
    expect(rows[0]!["b"]).toBe(false);
    expect(rows[0]!["c"]).toBe(true);
  });

  it("coerces numeric strings to numbers", () => {
    const p = tmpPath("numbers.csv");
    writeFileSync(p, "int,float,zero,half\n42,3.14,0,0.5\n");
    const rows = readFile(p);
    expect(rows[0]!["int"]).toBe(42);
    expect(rows[0]!["float"]).toBe(3.14);
    expect(rows[0]!["zero"]).toBe(0);
    expect(rows[0]!["half"]).toBe(0.5);
    // Verify actual types
    expect(typeof rows[0]!["int"]).toBe("number");
    expect(typeof rows[0]!["float"]).toBe("number");
    expect(typeof rows[0]!["zero"]).toBe("number");
    expect(typeof rows[0]!["half"]).toBe("number");
  });

  it("converts empty fields to null", () => {
    const p = tmpPath("nulls.csv");
    writeFileSync(p, "a,b,c\nhello,,world\n");
    const rows = readFile(p);
    expect(rows[0]!["a"]).toBe("hello");
    expect(rows[0]!["b"]).toBeNull();
    expect(rows[0]!["c"]).toBe("world");
  });
});

// ---------------------------------------------------------------------------
// Write + round-trip
// ---------------------------------------------------------------------------

describe("CSV parser — writeFile + round-trip", () => {
  it("writes rows then reads them back identically", () => {
    const p = tmpPath("roundtrip.csv");
    const original = [
      { name: "Alice", age: 30, active: true },
      { name: "Bob", age: 25, active: false },
    ];
    writeFile(original, p);
    const result = readFile(p);
    expect(result).toHaveLength(2);
    expect(result[0]!["name"]).toBe("Alice");
    expect(result[0]!["age"]).toBe(30);
    expect(result[0]!["active"]).toBe(true);
    expect(result[1]!["name"]).toBe("Bob");
    expect(result[1]!["age"]).toBe(25);
    expect(result[1]!["active"]).toBe(false);
  });

  it("round-trips null values", () => {
    const p = tmpPath("roundtrip_null.csv");
    const original = [{ a: "hello", b: null, c: 42 }];
    writeFile(original, p);
    const result = readFile(p);
    expect(result[0]!["b"]).toBeNull();
    expect(result[0]!["a"]).toBe("hello");
    expect(result[0]!["c"]).toBe(42);
  });

  it("round-trips fields containing commas and quotes", () => {
    const p = tmpPath("roundtrip_special.csv");
    const original = [{ greeting: 'He said "hi"', city: "Portland, OR" }];
    writeFile(original, p);
    const result = readFile(p);
    expect(result[0]!["greeting"]).toBe('He said "hi"');
    expect(result[0]!["city"]).toBe("Portland, OR");
  });
});

// ---------------------------------------------------------------------------
// JSON file support
// ---------------------------------------------------------------------------

describe("CSV parser — JSON file read/write", () => {
  it("reads a JSON array file", () => {
    const p = tmpPath("data.json");
    writeFileSync(p, JSON.stringify([{ x: 1, y: "hello" }, { x: 2, y: "world" }]));
    const rows = readFile(p);
    expect(rows).toHaveLength(2);
    expect(rows[0]!["x"]).toBe(1);
    expect(rows[1]!["y"]).toBe("world");
  });

  it("writes and reads back a JSON file", () => {
    const p = tmpPath("roundtrip.json");
    const original = [{ id: 1, label: "test" }];
    writeFile(original, p);
    const result = readFile(p);
    expect(result).toEqual(original);
  });
});
