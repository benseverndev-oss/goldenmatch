import { describe, it, expect } from "vitest";
import { runIncremental } from "../../src/core/incremental.js";
import type { GoldenMatchConfig, MatchkeyConfig, Row } from "../../src/core/types.js";

// A config with BOTH an exact matchkey (email) and a fuzzy matchkey (name).
const EXACT_MK: MatchkeyConfig = {
  name: "exact_email",
  type: "exact",
  fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1 }],
};
const FUZZY_MK: MatchkeyConfig = {
  name: "fuzzy_name",
  type: "weighted",
  threshold: 0.85,
  fields: [{ field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 }],
};
const CONFIG: GoldenMatchConfig = { matchkeys: [EXACT_MK, FUZZY_MK] };

const BASE: Row[] = [
  { email: "a@x.com", name: "Alice Alpha" }, // base row_id 0
  { email: "b@x.com", name: "Bob Beta" }, // base row_id 1
];
const NEW: Row[] = [
  // matches base 0 ONLY on exact email; names are far apart (fuzzy misses).
  { email: "a@x.com", name: "Zephyr Quux" }, // new row_id 2
  // matches base 1 ONLY on fuzzy name (email differs).
  { email: "c@x.com", name: "Bob Beta" }, // new row_id 3
];

describe("runIncremental — exact vs fuzzy split", () => {
  it("LANDMINE: the exact-only match comes from the exact matchkey, NOT the fuzzy path", () => {
    // With ONLY the fuzzy matchkey, new 0 (same email, far-apart name) is NOT
    // matched — the fuzzy scorer misses it. A naive impl that resolves only the
    // fuzzy matchkeys (skipping exact) therefore SILENTLY DROPS this pair.
    const fuzzyOnly = runIncremental(BASE, NEW, { matchkeys: [FUZZY_MK] });
    const fuzzyPairs = new Set(
      fuzzyOnly.matches.map((m) => `${m.new_row_id}->${m.base_row_id}`),
    );
    expect(fuzzyPairs.has("2->0")).toBe(false);

    // Adding the exact matchkey (resolved via the hash join) recovers it.
    const withExact = runIncremental(BASE, NEW, CONFIG);
    const allPairs = new Set(
      withExact.matches.map((m) => `${m.new_row_id}->${m.base_row_id}`),
    );
    expect(allPairs.has("2->0")).toBe(true);
  });

  it("finds BOTH the exact-only match (via join) and the fuzzy-only match (via matchOne)", () => {
    const result = runIncremental(BASE, NEW, CONFIG);

    expect(result.base_records).toBe(2);
    expect(result.new_records).toBe(2);

    const pairs = new Set(
      result.matches.map((m) => `${m.new_row_id}->${m.base_row_id}`),
    );
    // Exact-only pair (would be dropped by a matchOne-only impl).
    expect(pairs.has("2->0")).toBe(true);
    // Fuzzy-only pair.
    expect(pairs.has("3->1")).toBe(true);

    expect(result.matched_to_base).toBe(2);
    expect(result.new_entities).toBe(0);
    expect(result.total_pairs).toBe(result.matches.length);

    // Response-shape parity with Python run_incremental.
    for (const m of result.matches) {
      expect(Object.keys(m).sort()).toEqual(
        ["base_row_id", "new_row_id", "score"].sort(),
      );
      expect(typeof m.score).toBe("number");
    }
    expect(Object.keys(result).sort()).toEqual(
      [
        "base_records",
        "matched_to_base",
        "matches",
        "new_entities",
        "new_records",
        "total_pairs",
      ].sort(),
    );
  });

  it("counts unmatched new records as new_entities", () => {
    const newOnly: Row[] = [{ email: "z@z.com", name: "Nobody Here" }];
    const result = runIncremental(BASE, newOnly, CONFIG);
    expect(result.new_records).toBe(1);
    expect(result.matched_to_base).toBe(0);
    expect(result.new_entities).toBe(1);
    expect(result.matches).toEqual([]);
  });

  it("threshold override loosens the fuzzy matchkey", () => {
    // Names differ enough to miss at 0.85 but hit at a very low threshold.
    const base: Row[] = [{ email: "p@x.com", name: "Jonathan" }];
    const news: Row[] = [{ email: "q@x.com", name: "Jonathon" }];
    const cfg: GoldenMatchConfig = { matchkeys: [FUZZY_MK] };
    const strict = runIncremental(base, news, cfg);
    const loose = runIncremental(base, news, cfg, 0.1);
    expect(loose.matched_to_base).toBeGreaterThanOrEqual(strict.matched_to_base);
    expect(loose.matched_to_base).toBe(1);
  });
});
