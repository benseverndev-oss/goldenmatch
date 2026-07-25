/**
 * Cross-language parity for the FS domain comparators `date_diff` and
 * `geo_haversine` (score-core score_one ids 17 / 18).
 *
 * The pure-TS `dateDiffSimilarity` / `geoHaversineSimilarity` (via `scoreField`)
 * must match the Python reference — which is itself byte-verified == the Rust
 * score-core kernel — to 4 decimals. This is the binding oracle that puts both
 * scorers in the `scorer_kernels` SHARED partition (kernel-backed on Python
 * native AND TS/WASM). The WASM path is pinned separately in wasm-scorer.test.ts.
 *
 * Goldens: fixtures/scorer-domain-comparators.json (emit_domain_comparator_fixtures.py).
 */
import { describe, it, expect } from "vitest";
import { scoreField } from "../../src/core/index.js";
import fixture from "./fixtures/scorer-domain-comparators.json" with { type: "json" };

type Case = readonly [scorer: string, a: string, b: string, expected: number];
const CASES = fixture.cases as unknown as readonly Case[];

describe("domain-comparator parity — full fixture (4dp)", () => {
  for (const [scorer, a, b, expected] of CASES) {
    it(`${scorer}(${JSON.stringify(a)}, ${JSON.stringify(b)}) ≈ ${expected}`, () => {
      expect(scoreField(a, b, scorer)).toBeCloseTo(expected, 4);
    });
  }
});
