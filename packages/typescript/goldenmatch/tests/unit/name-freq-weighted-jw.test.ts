import { describe, it, expect } from "vitest";
import { scoreField, jaroWinkler } from "../../src/core/scorer.js";
import { VALID_SCORERS } from "../../src/core/types.js";
import { surnameIdf, surnameRank } from "../../src/core/refdata/surnames.js";

const S = "name_freq_weighted_jw";

/**
 * Mirror the scorer's rule using the exported primitives, to cross-check.
 * This is the canonical expected-value function: for ANY pair it computes
 * what scoreField(a, b, S) must return.
 */
function expectedNameFreq(a: string, b: string): number {
  const jw = jaroWinkler(a, b);
  if (jw >= 0.95 || jw < 0.70) return jw;
  if (surnameRank(a) === null || surnameRank(b) === null) return jw;
  const idfA = surnameIdf(a);
  const idfB = surnameIdf(b);
  if (idfA === null || idfB === null) return jw;
  const idf = (idfA + idfB) / 2;
  return jw * (0.6 + 0.4 * idf);
}

describe("name_freq_weighted_jw scorer", () => {
  it("is a valid scorer name", () => {
    expect(VALID_SCORERS.has(S)).toBe(true);
  });

  it("null inputs return null", () => {
    expect(scoreField(null, "Smith", S)).toBeNull();
    expect(scoreField("Smith", null, S)).toBeNull();
    expect(scoreField(null, null, S)).toBeNull();
  });

  // --- jw >= 0.95 passthrough (high similarity zone) ---
  // "Smith" vs "Smith": jw = 1.0 (identical)
  it("identical strings score 1.0 (jw=1.0, high-similarity passthrough)", () => {
    expect(scoreField("Smith", "Smith", S)).toBe(1.0);
    expect(scoreField("Smith", "Smith", S)).toBe(expectedNameFreq("Smith", "Smith"));
  });

  // Johnson vs Johnston: jw=0.975 >= 0.95 → passthrough
  it("very similar pair (jw>=0.95) returns plain jw unchanged", () => {
    const a = "Johnson", b = "Johnston";
    const jw = jaroWinkler(a, b);
    expect(jw).toBeGreaterThanOrEqual(0.95);
    expect(scoreField(a, b, S)).toBeCloseTo(jw, 10);
    expect(scoreField(a, b, S)).toBeCloseTo(expectedNameFreq(a, b), 10);
  });

  // --- jw < 0.70 passthrough (low similarity zone) ---
  // "Smith" vs "Xyzqw": jw will be low
  it("very different strings return plain jw (jw<0.70 passthrough)", () => {
    const a = "Smith", b = "Xyzqw";
    const jw = jaroWinkler(a, b);
    expect(jw).toBeLessThan(0.70);
    expect(scoreField(a, b, S)).toBeCloseTo(jw, 10);
    expect(scoreField(a, b, S)).toBeCloseTo(expectedNameFreq(a, b), 10);
  });

  // --- OOV gate (one side not in census table) ---
  // "Taylor" vs "Tailor": Tailor is OOV → plain jw
  it("OOV side (Tailor not in census) falls back to plain jw", () => {
    const a = "Taylor", b = "Tailor";
    const jw = jaroWinkler(a, b);
    // Tailor is OOV so rank(b) === null → no reweighting
    expect(surnameRank(b)).toBeNull();
    expect(scoreField(a, b, S)).toBeCloseTo(jw, 10);
    expect(scoreField(a, b, S)).toBeCloseTo(expectedNameFreq(a, b), 10);
  });

  // --- Borderline zone, both sides known: reweighted ---
  // "Taylor" vs "Tyler": both in top-10k, jw=0.840 is in [0.70, 0.95)
  it("borderline both-known pair (Taylor/Tyler): scored == expectedNameFreq (cross-check)", () => {
    const a = "Taylor", b = "Tyler";
    const jw = jaroWinkler(a, b);
    expect(jw).toBeGreaterThanOrEqual(0.70);
    expect(jw).toBeLessThan(0.95);
    expect(surnameRank(a)).not.toBeNull();
    expect(surnameRank(b)).not.toBeNull();
    const got = scoreField(a, b, S)!;
    const expected = expectedNameFreq(a, b);
    // reweighted score must be strictly less than plain jw (both are common surnames → idf<1)
    expect(got).toBeLessThan(jw);
    expect(got).toBeCloseTo(expected, 10);
  });

  // "Moore" vs "More": both in top-10k, jw=0.946667 is in [0.70, 0.95)
  it("borderline both-known pair (Moore/More): scored == expectedNameFreq (cross-check)", () => {
    const a = "Moore", b = "More";
    const jw = jaroWinkler(a, b);
    expect(jw).toBeGreaterThanOrEqual(0.70);
    expect(jw).toBeLessThan(0.95);
    expect(surnameRank(a)).not.toBeNull();
    expect(surnameRank(b)).not.toBeNull();
    const got = scoreField(a, b, S)!;
    expect(got).toBeCloseTo(expectedNameFreq(a, b), 10);
  });

  // "Smith" vs "Smythe": both in top-10k, jw=0.857778 is in [0.70, 0.95)
  it("borderline both-known pair (Smith/Smythe): scored == expectedNameFreq (cross-check)", () => {
    const a = "Smith", b = "Smythe";
    const jw = jaroWinkler(a, b);
    expect(jw).toBeGreaterThanOrEqual(0.70);
    expect(jw).toBeLessThan(0.95);
    expect(surnameRank(a)).not.toBeNull();
    expect(surnameRank(b)).not.toBeNull();
    const got = scoreField(a, b, S)!;
    expect(got).toBeCloseTo(expectedNameFreq(a, b), 10);
  });

  // Invariant: scoreField(a,b,S) === expectedNameFreq(a,b) for all cases
  it("universal cross-check: scoreField matches expectedNameFreq for diverse pairs", () => {
    const pairs: [string, string][] = [
      ["Smith", "Smith"],      // identical → 1.0
      ["Johnson", "Johnston"], // high similarity → plain jw
      ["Smith", "Xyzqw"],     // low similarity → plain jw
      ["Taylor", "Tailor"],   // OOV Tailor → plain jw
      ["Taylor", "Tyler"],     // both known, borderline
      ["Moore", "More"],       // both known, borderline
      ["Smith", "Smythe"],    // both known, borderline
      ["Lee", "Lea"],          // both known (rank 21 and known), check whatever jw
      ["Brown", "Brien"],      // Brien is OOV → plain jw
    ];
    for (const [a, b] of pairs) {
      const got = scoreField(a, b, S)!;
      const expected = expectedNameFreq(a, b);
      expect(got, `pair ${a}/${b}`).toBeCloseTo(expected, 10);
    }
  });
});
