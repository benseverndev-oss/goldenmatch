import { describe, it, expect } from "vitest";
import { scanData, TabularData } from "../src/core/index.js";

describe("scanData with schema", () => {
  it("emits unmapped_column finding for unknown-typed columns", () => {
    const data = new TabularData([
      { account_number: "A1234", zzz_unknown: "foo" },
      { account_number: "A5678", zzz_unknown: "bar" },
    ]);
    const result = scanData(data, {
      schema: {
        domain: "finance",
        fields: {
          account_number: { type: "account_number" },
          zzz_unknown: { type: "unknown" },
        },
      },
    });
    const codes = new Set(result.findings.map((f) => f.check));
    expect(codes.has("unmapped_column")).toBe(true);
    const unmapped = result.findings.filter((f) => f.check === "unmapped_column");
    expect(unmapped.some((f) => f.column === "zzz_unknown")).toBe(true);
  });

  it("legacy mode (no schema) does not emit unmapped_column", () => {
    const data = new TabularData([
      { a: "1" },
      { a: "2" },
    ]);
    const result = scanData(data);
    const codes = new Set(result.findings.map((f) => f.check));
    expect(codes.has("unmapped_column")).toBe(false);
  });
});
