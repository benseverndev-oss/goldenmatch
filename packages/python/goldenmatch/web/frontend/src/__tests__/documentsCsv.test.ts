import { describe, it, expect } from "vitest";
import { recordsToCsv } from "../lib/documentsCsv";

describe("recordsToCsv", () => {
  it("emits header + rows in column order, quoting commas/quotes", () => {
    const cols = ["full_name", "email", "_extract_confidence"];
    const rows = [
      { full_name: "Ada, L", email: 'a"@x.io', _extract_confidence: 0.9 },
      { full_name: "Bo", email: null, _extract_confidence: 0 },
    ];
    const csv = recordsToCsv(rows, cols);
    expect(csv).toBe(
      'full_name,email,_extract_confidence\r\n' +
      '"Ada, L","a""@x.io",0.9\r\n' +
      'Bo,,0\r\n'
    );
  });
});
