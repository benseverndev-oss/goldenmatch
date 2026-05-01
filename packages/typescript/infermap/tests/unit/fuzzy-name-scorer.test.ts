import { describe, it, expect } from "vitest";
import { FuzzyNameScorer } from "../../src/core/scorers/fuzzy-name.js";
import {
  jaroSimilarity,
  jaroWinklerSimilarity,
  levenshteinDistance,
} from "../../src/core/util/string-distance.js";
import { makeFieldInfo } from "../../src/core/types.js";

describe("jaroSimilarity", () => {
  it("returns 1 for identical strings", () => {
    expect(jaroSimilarity("abc", "abc")).toBe(1);
  });
  it("returns 0 for one empty", () => {
    expect(jaroSimilarity("", "abc")).toBe(0);
    expect(jaroSimilarity("abc", "")).toBe(0);
  });
  it("matches the classic MARTHA/MARHTA reference value", () => {
    // Known textbook: Jaro("MARTHA","MARHTA") = 0.944 (3 d.p.)
    expect(jaroSimilarity("MARTHA", "MARHTA")).toBeCloseTo(0.9444, 3);
  });
  it("returns 0 for totally distinct strings", () => {
    expect(jaroSimilarity("abc", "xyz")).toBe(0);
  });
});

describe("jaroWinklerSimilarity", () => {
  it("boosts common-prefix matches above base Jaro", () => {
    const base = jaroSimilarity("MARTHA", "MARHTA");
    const jw = jaroWinklerSimilarity("MARTHA", "MARHTA");
    expect(jw).toBeGreaterThan(base);
    // Textbook: JW("MARTHA","MARHTA") ≈ 0.961
    expect(jw).toBeCloseTo(0.9611, 3);
  });
  it("returns 1 for identical strings", () => {
    expect(jaroWinklerSimilarity("abc", "abc")).toBe(1);
  });
  it("does not apply boost when base Jaro < 0.7", () => {
    const s1 = "abc";
    const s2 = "xyz";
    expect(jaroWinklerSimilarity(s1, s2)).toBe(jaroSimilarity(s1, s2));
  });
});

describe("levenshteinDistance", () => {
  it("is 0 for identical", () => {
    expect(levenshteinDistance("abc", "abc")).toBe(0);
  });
  it("handles insertions/deletions/substitutions", () => {
    expect(levenshteinDistance("kitten", "sitting")).toBe(3);
    expect(levenshteinDistance("", "abc")).toBe(3);
    expect(levenshteinDistance("abc", "")).toBe(3);
  });
});

describe("FuzzyNameScorer", () => {
  const scorer = new FuzzyNameScorer();

  it("scores 1 on identical normalized names", () => {
    const r = scorer.score(
      makeFieldInfo({ name: "first_name" }),
      makeFieldInfo({ name: "FirstName" })
    );
    expect(r.score).toBe(1);
  });

  it("scores high on close matches", () => {
    const r = scorer.score(
      makeFieldInfo({ name: "customer_id" }),
      makeFieldInfo({ name: "customer_ids" })
    );
    expect(r.score).toBeGreaterThan(0.9);
  });

  it("scores low on distinct names", () => {
    const r = scorer.score(
      makeFieldInfo({ name: "email" }),
      makeFieldInfo({ name: "country" })
    );
    expect(r.score).toBeLessThan(0.6);
  });
});
