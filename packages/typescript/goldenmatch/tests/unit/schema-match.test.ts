import { describe, it, expect } from "vitest";

import { autoMapColumns } from "../../src/core/schema-match.js";
import type { Row } from "../../src/core/types.js";

/**
 * Parity: the mapping structure must match the Python
 * `schema_match.py::auto_map_columns` for a shared fixture. The fixtures here are
 * dominated by exact_name / synonym / composite decisions (function-independent
 * of the reference-string kernel), so the mapping is byte-stable across Python's
 * rapidfuzz path and the TS jaroWinkler path.
 */
describe("schema-match: autoMapColumns", () => {
  it("maps synonymous columns (parity-stable, all synonym decisions)", () => {
    const a: Row[] = [
      { email: "x@a.com", phone: "555-1", city: "boston" },
      { email: "y@a.com", phone: "555-2", city: "austin" },
    ];
    const b: Row[] = [
      { contact_email: "x@a.com", telephone: "555-1", town: "boston" },
      { contact_email: "z@a.com", telephone: "555-9", town: "reno" },
    ];
    const mappings = autoMapColumns(a, b, 0.5);
    const byA = new Map(mappings.map((m) => [m.col_a, m]));

    expect(byA.get("email")?.col_b).toBe("contact_email");
    expect(byA.get("email")?.method).toBe("synonym");
    expect(byA.get("email")?.score).toBe(0.95);

    expect(byA.get("phone")?.col_b).toBe("telephone");
    expect(byA.get("phone")?.method).toBe("synonym");

    expect(byA.get("city")?.col_b).toBe("town");
    expect(byA.get("city")?.method).toBe("synonym");
  });

  it("detects an exact-name mapping at score 1.0", () => {
    const a: Row[] = [{ email: "a@a.com" }];
    const b: Row[] = [{ email: "a@a.com" }];
    const [m] = autoMapColumns(a, b, 0.5);
    expect(m?.col_a).toBe("email");
    expect(m?.col_b).toBe("email");
    expect(m?.method).toBe("exact_name");
    expect(m?.score).toBe(1.0);
  });

  it("detects a composite mapping (full_name -> first_name + last_name)", () => {
    // min_score 0.9 keeps the fuzzy name_sim below threshold so the greedy pass
    // leaves full_name unmapped -> the composite detector fires (parity with
    // Python: rapidfuzz ratio(full_name, first_name) is likewise < 0.9).
    const a: Row[] = [{ full_name: "Jane Doe", email: "j@a.com" }];
    const b: Row[] = [{ first_name: "Jane", last_name: "Doe", email: "j@a.com" }];
    const mappings = autoMapColumns(a, b, 0.9);
    const composite = mappings.find((m) => m.method === "composite");
    expect(composite).toBeDefined();
    expect(composite?.col_a).toBe("full_name");
    expect(composite?.col_b).toBe("first_name + last_name");
    expect(composite?.composite_cols).toEqual(["first_name", "last_name"]);
    expect(composite?.score).toBe(0.9);
    // email still maps exactly.
    expect(mappings.some((m) => m.col_a === "email" && m.method === "exact_name")).toBe(true);
  });

  it("assigns each column at most once (greedy best-match)", () => {
    const a: Row[] = [{ email: "1" }, { email: "2" }];
    const b: Row[] = [
      { contact_email: "1", email: "9" },
      { contact_email: "2", email: "1" },
    ];
    const mappings = autoMapColumns(a, b, 0.5);
    // email(A) should take the exact email(B), not the synonym contact_email.
    const emailMap = mappings.find((m) => m.col_a === "email");
    expect(emailMap?.col_b).toBe("email");
    // each B column used at most once
    const bUsed = mappings.map((m) => m.col_b);
    expect(new Set(bUsed).size).toBe(bUsed.length);
  });

  it("returns an empty list for empty inputs", () => {
    expect(autoMapColumns([], [], 0.5)).toEqual([]);
    expect(autoMapColumns([{ a: "1" }], [], 0.5)).toEqual([]);
  });

  it("mapping objects carry the Python-parity snake_case shape", () => {
    const mappings = autoMapColumns([{ email: "a" }], [{ contact_email: "a" }], 0.5);
    expect(mappings.length).toBe(1);
    const m = mappings[0]!;
    expect(Object.keys(m).sort()).toEqual(["col_a", "col_b", "method", "score"].sort());
    expect(typeof m.score).toBe("number");
    expect(typeof m.method).toBe("string");
  });
});
