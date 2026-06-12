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

// Corpus includes Winkler canon + non-BMP (😀) + accented (café) to catch any
// codepoint-vs-UTF16-code-unit divergence between the rapidfuzz WASM kernel and
// the hand-rolled pure-TS scorers.
const VALUES = [
  "MARTHA", "MARHTA", "DIXON", "DICKSONX", "John", "Jon",
  "kitten", "sitting", "saturday", "sunday", "abc", "abd",
  "café", "cafe", "😀ab", "😀ac", "", "x",
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
