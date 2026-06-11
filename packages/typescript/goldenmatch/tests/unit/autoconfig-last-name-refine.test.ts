import { describe, it, expect } from "vitest";
import { autoConfigureRows } from "../../src/core/autoconfig.js";

function scorerFor(cfg: ReturnType<typeof autoConfigureRows>, field: string): string | undefined {
  for (const mk of cfg.matchkeys ?? []) {
    for (const f of mk.fields) if (f.field === field) return f.scorer;
  }
  return undefined;
}

describe("autoconfig last-name refdata refine", () => {
  const rows = Array.from({ length: 12 }, (_, i) => ({
    first_name: ["William", "Bill", "Robert", "Bob", "James", "Jim"][i % 6],
    last_name: ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia"][i % 6],
    city: ["Austin", "Austin", "Dallas", "Dallas", "Houston", "Houston"][i % 6],
  }));

  it("last_name column is refined to name_freq_weighted_jw", () => {
    const cfg = autoConfigureRows(rows);
    expect(scorerFor(cfg, "last_name")).toBe("name_freq_weighted_jw");
  });
  it("first_name column is still refined to given_name_aliased_jw", () => {
    const cfg = autoConfigureRows(rows);
    expect(scorerFor(cfg, "first_name")).toBe("given_name_aliased_jw");
  });
  it("a non-name fuzzy column keeps its base scorer", () => {
    const cfg = autoConfigureRows(rows);
    const s = scorerFor(cfg, "city");
    expect(s).not.toBe("name_freq_weighted_jw");
    expect(s).not.toBe("given_name_aliased_jw");
  });
});
