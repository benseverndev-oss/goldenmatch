import { describe, it, expect } from "vitest";
import { ProfileScorer } from "../../src/core/scorers/profile.js";
import { makeFieldInfo } from "../../src/core/types.js";

describe("ProfileScorer", () => {
  const scorer = new ProfileScorer();

  it("abstains when either side has zero rows", () => {
    const a = makeFieldInfo({ name: "a", valueCount: 0 });
    const b = makeFieldInfo({ name: "b", valueCount: 10 });
    expect(scorer.score(a, b)).toBeNull();
    expect(scorer.score(b, a)).toBeNull();
  });

  it("returns ~1.0 for identical profiles", () => {
    const a = makeFieldInfo({
      name: "a",
      dtype: "string",
      nullRate: 0.1,
      uniqueRate: 0.9,
      valueCount: 100,
      sampleValues: ["abc", "def", "ghi"],
    });
    const b = { ...a, name: "b" };
    const r = scorer.score(a, b)!;
    expect(r.score).toBeCloseTo(1.0, 5);
  });

  it("drops score on dtype mismatch", () => {
    const a = makeFieldInfo({
      name: "a",
      dtype: "string",
      nullRate: 0.1,
      uniqueRate: 0.9,
      valueCount: 100,
      sampleValues: ["abc"],
    });
    const b = { ...a, name: "b", dtype: "integer" as const };
    const r = scorer.score(a, b)!;
    // Lost 0.4 for dtype but other dims perfect → 0.6
    expect(r.score).toBeCloseTo(0.6, 5);
  });

  it("includes all dimension parts in reasoning", () => {
    const a = makeFieldInfo({
      name: "a",
      valueCount: 10,
      sampleValues: ["x"],
    });
    const b = { ...a, name: "b" };
    const r = scorer.score(a, b)!;
    for (const key of ["dtype=", "null_sim=", "uniq_sim=", "len_sim=", "card_sim="]) {
      expect(r.reasoning).toContain(key);
    }
  });
});
