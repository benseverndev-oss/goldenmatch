/**
 * rapidfuzz parity for the hand-rolled string scorers. The pure-TS jaro /
 * jaroWinkler / levenshtein / indel must match rapidfuzz (the engine the Rust
 * score-core, the Python wheel, and the Python goldens all use) to 4 decimals,
 * INCLUDING non-BMP, accented, sub-0.7-prefix, and repeated-char inputs.
 *
 * Goldens: tests/parity/fixtures/scorer-rapidfuzz.json (emit_scorer_parity_fixtures.py).
 */
import { describe, it, expect } from "vitest";
import { scoreField, jaro } from "../../src/core/index.js";
import fixture from "./fixtures/scorer-rapidfuzz.json" with { type: "json" };

type Case = readonly [scorer: string, a: string, b: string, expected: number];
const CASES = fixture.cases as readonly Case[];

const score = (scorer: string, a: string, b: string): number =>
  scorer === "jaro" ? jaro(a, b) : (scoreField(a, b, scorer) as number);

// Named red->green targets — one per divergence (clear failure messages).
describe("scorer rapidfuzz parity — named divergences", () => {
  it("transposition floors t/2 (jaro 'dabaeb'/'dbea' = 0.8056)", () => {
    expect(jaro("dabaeb", "dbea")).toBeCloseTo(0.8056, 4);
  });
  it("Winkler boost only above jaro>0.7 (jaro_winkler 'ad'/'abaed' = 0.5667)", () => {
    expect(scoreField("ad", "abaed", "jaro_winkler")).toBeCloseTo(0.5667, 4);
  });
  it("codepoint iteration on non-BMP (jaro '😀ab'/'😀ac' = 0.7778)", () => {
    expect(jaro("\u{1F600}ab", "\u{1F600}ac")).toBeCloseTo(0.7778, 4);
  });
});

describe("scorer rapidfuzz parity — full fixture (4dp)", () => {
  for (const [scorer, a, b, expected] of CASES) {
    it(`${scorer}(${JSON.stringify(a)}, ${JSON.stringify(b)}) ≈ ${expected}`, () => {
      expect(score(scorer, a, b)).toBeCloseTo(expected, 4);
    });
  }
});
