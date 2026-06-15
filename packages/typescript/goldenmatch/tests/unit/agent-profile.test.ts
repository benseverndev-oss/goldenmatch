import { describe, it, expect } from "vitest";
import { profileForAgent } from "../../src/core/agent/strategy.js";

describe("profileForAgent", () => {
  it("computes uniqueness, null_rate, avg_length, type per field", () => {
    const rows = [
      { id: "1", name: "Alice", note: null },
      { id: "2", name: "Alice", note: "x" },
      { id: "3", name: "Bob", note: "yy" },
      { id: "4", name: "Carol", note: "zzz" },
    ];
    const p = profileForAgent(rows);
    expect(p.row_count).toBe(4);
    const id = p.fields.find((f) => f.name === "id")!;
    expect(id.uniqueness).toBeCloseTo(1.0, 4); // 4 unique / 4
    expect(id.null_rate).toBeCloseTo(0.0, 4);
    const name = p.fields.find((f) => f.name === "name")!;
    expect(name.uniqueness).toBeCloseTo(0.75, 4); // Alice,Bob,Carol = 3/4
    const note = p.fields.find((f) => f.name === "note")!;
    expect(note.null_rate).toBeCloseTo(0.25, 4); // 1 null / 4
    expect(name.type).toBe("string");
  });

  it("flags sensitive columns by name pattern", () => {
    expect(profileForAgent([{ ssn: "x" }]).has_sensitive).toBe(true);
    expect(profileForAgent([{ date_of_birth: "x" }]).has_sensitive).toBe(true);
    expect(profileForAgent([{ name: "x" }]).has_sensitive).toBe(false);
  });

  it("returns an empty profile for zero rows", () => {
    const p = profileForAgent([]);
    expect(p.row_count).toBe(0);
    expect(p.fields).toEqual([]);
    expect(p.has_sensitive).toBe(false);
  });
});
