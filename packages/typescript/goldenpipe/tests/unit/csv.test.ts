/**
 * Unit tests for the minimal CSV parser.
 */

import { describe, it, expect } from "vitest";
import { parseCsv } from "../../src/node/index.js";

describe("parseCsv", () => {
  it("parses a simple table", () => {
    const rows = parseCsv("a,b\n1,2\n3,4\n");
    expect(rows).toEqual([
      { a: "1", b: "2" },
      { a: "3", b: "4" },
    ]);
  });

  it("handles quoted fields with commas and quotes", () => {
    const rows = parseCsv('name,note\n"Smith, John","said ""hi"""\n');
    expect(rows).toEqual([{ name: "Smith, John", note: 'said "hi"' }]);
  });

  it("handles embedded newlines inside quotes", () => {
    const rows = parseCsv('a,b\n"line1\nline2",x\n');
    expect(rows).toEqual([{ a: "line1\nline2", b: "x" }]);
  });

  it("handles CRLF line endings and a trailing line without newline", () => {
    const rows = parseCsv("a,b\r\n1,2\r\n3,4");
    expect(rows).toEqual([
      { a: "1", b: "2" },
      { a: "3", b: "4" },
    ]);
  });

  it("pads short rows and returns [] for empty input", () => {
    expect(parseCsv("")).toEqual([]);
    expect(parseCsv("a,b\n1\n")).toEqual([{ a: "1", b: "" }]);
  });
});
