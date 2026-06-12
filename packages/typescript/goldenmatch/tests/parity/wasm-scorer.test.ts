/**
 * WASM-vs-pure-TS-vs-Python parity for the COVERED scorers. Skipped when the
 * built artifact is absent (default checkout / no toolchain); the CI lane
 * builds it first and runs this un-skipped. 4-decimal tolerance, matching the
 * existing scorer ground-truth contract.
 */
import { describe, it, expect, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { scoreMatrix } from "../../src/core/scorer.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/score_wasm_bg.wasm", import.meta.url),
);
const hasArtifact = existsSync(artifact);
const d = hasArtifact ? describe : describe.skip;

// Corpus: Winkler canon + accented BMP (café = U+00E9, one UTF-16 unit) + the
// empty string. KNOWN-DIVERGENCE / OUT OF SCOPE: astral-plane (non-BMP) inputs
// like 😀 are deliberately EXCLUDED. The pure-TS scorers index UTF-16 code units
// (`a[i]`, `a.length`) whereas the rapidfuzz WASM kernel — and the Python golden
// source — operate on Unicode codepoints, so a surrogate-pair char makes them
// disagree (a pre-existing pure-TS↔Python gap WASM merely exposes). Person-name
// ER data is BMP; codepoint-correcting the core scorers is a tracked follow-up,
// not part of the WASM opt-in slice. Parity here is asserted on the BMP domain.
const VALUES = [
  "MARTHA", "MARHTA", "DIXON", "DICKSONX", "John", "Jon",
  "kitten", "sitting", "saturday", "sunday", "abc", "abd",
  "café", "cafe", "", "x",
];
const SCORERS = ["jaro_winkler", "levenshtein", "exact"] as const;

d("WASM scorer parity", () => {
  afterAll(() => disableWasm());

  for (const scorer of SCORERS) {
    it(`${scorer}: WASM matrix matches pure-TS matrix (4dp)`, async () => {
      disableWasm();
      const pure = scoreMatrix(VALUES, scorer); // pure-TS
      const ok = await enableWasm();
      expect(ok).toBe(true); // artifact present in this lane
      const wasm = scoreMatrix(VALUES, scorer); // backend active
      disableWasm();
      for (let i = 0; i < VALUES.length; i++) {
        for (let j = 0; j < VALUES.length; j++) {
          expect(wasm[i]![j]!).toBeCloseTo(pure[i]![j]!, 4);
        }
      }
    });
  }
});
