import { describe, it, expect } from "vitest";
import { parseCsv } from "../../src/core/util/csv.js";

describe("parseCsv", () => {
  it("parses a simple CSV", () => {
    const { headers, rows } = parseCsv("a,b,c\n1,2,3\n4,5,6\n");
    expect(headers).toEqual(["a", "b", "c"]);
    expect(rows).toEqual([
      { a: "1", b: "2", c: "3" },
      { a: "4", b: "5", c: "6" },
    ]);
  });

  it("handles quoted fields with commas", () => {
    const { rows } = parseCsv('name,addr\n"Smith, Bob","1 Main St, Apt 2"\n');
    expect(rows[0]).toEqual({ name: "Smith, Bob", addr: "1 Main St, Apt 2" });
  });

  it("handles escaped quotes", () => {
    const { rows } = parseCsv('q\n"He said ""hi"""\n');
    expect(rows[0]).toEqual({ q: 'He said "hi"' });
  });

  it("handles CRLF line endings", () => {
    const { rows } = parseCsv("a,b\r\n1,2\r\n3,4\r\n");
    expect(rows).toHaveLength(2);
  });

  it("handles embedded newlines in quoted fields", () => {
    const { rows } = parseCsv('note\n"line1\nline2"\n');
    expect(rows[0]).toEqual({ note: "line1\nline2" });
  });

  it("strips UTF-8 BOM", () => {
    const { headers } = parseCsv("\uFEFFid,name\n1,x\n");
    expect(headers).toEqual(["id", "name"]);
  });

  it("tolerates trailing empty row", () => {
    const { rows } = parseCsv("a\n1\n");
    expect(rows).toEqual([{ a: "1" }]);
  });

  it("returns empty on empty input", () => {
    expect(parseCsv("")).toEqual({ headers: [], rows: [] });
  });
});
