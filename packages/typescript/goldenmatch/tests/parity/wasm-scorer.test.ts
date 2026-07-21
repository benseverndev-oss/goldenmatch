/**
 * WASM scorer parity vs the Python / rapidfuzz source of truth.
 *
 * The WASM kernel IS rapidfuzz (it wraps the `score-core` crate, the same crate
 * the Python `native` wheel and the DataFusion UDFs wrap), so this gate asserts
 * the opt-in WASM path reproduces the canonical rapidfuzz values to 4 decimals.
 * That proves the whole boundary chain end-to-end: scorer-id mapping (0/1/2/3),
 * the `\x1e`-joined batch marshalling, the row-major NxN layout, and UTF-8
 * round-tripping (incl. the BMP non-ASCII `café`). A wrong id / layout / encoding
 * would shift these values.
 *
 * It deliberately does NOT compare against the pure-TS scorers: the hand-rolled
 * pure-TS Jaro-Winkler has small KNOWN divergences from rapidfuzz on some inputs
 * (the Winkler 0.7 boost threshold; transposition counting on repeated-character
 * words like "saturday"). Those sit well below typical match thresholds, so
 * enabling WASM does not change dedup decisions — but it does shift such
 * borderline scores toward the Python values. Aligning the pure-TS scorers with
 * rapidfuzz is a tracked follow-up, not part of this opt-in-WASM slice.
 *
 * Goldens below are the exact `score_core::score_one` outputs (rapidfuzz 0.5.0),
 * rounded; tolerance is 4 decimals, matching scorer-ground-truth.test.ts.
 *
 * Skipped when the built artifact is absent (default checkout / no toolchain);
 * the CI `wasm_score` lane builds it first and runs this un-skipped.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { scoreMatrix, scoreField } from "../../src/core/scorer.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/score_wasm_bg.wasm", import.meta.url),
);
const hasArtifact = existsSync(artifact);
const d = hasArtifact ? describe : describe.skip;

type Golden = readonly [scorer: string, a: string, b: string, expected: number];

// rapidfuzz-verified (cargo: score_one). Covers: high-similarity, low-similarity
// (below the boost threshold), boost applied, repeated-char Jaro ("saturday"),
// and accented BMP ("café" — UTF-8 marshalling across the boundary).
const GOLDENS: readonly Golden[] = [
  ["jaro_winkler", "MARTHA", "MARHTA", 0.9611],
  ["jaro_winkler", "John", "Jon", 0.9333],
  ["jaro_winkler", "sitting", "saturday", 0.5119],
  ["jaro_winkler", "saturday", "sunday", 0.7775],
  ["jaro_winkler", "café", "cafe", 0.8833],
  ["jaro_winkler", "abc", "abd", 0.8222],
  ["levenshtein", "kitten", "sitting", 0.5714],
  ["levenshtein", "café", "cafe", 0.75],
  ["levenshtein", "abc", "abc", 1.0],
  ["levenshtein", "saturday", "sunday", 0.625],
  ["exact", "abc", "abc", 1.0],
  ["exact", "abc", "abd", 0.0],
  // token_sort (id 2) routes through score-core's token_sort_normalized_ratio:
  // lowercase + strip non-alnum + token-sort, then Indel — matching the pure-TS
  // tokenSortRatio. Exercises order-invariance, case + punctuation stripping.
  ["token_sort", "New York Mets", "Mets New York", 1.0],
  ["token_sort", "John Smith", "Smith Johnson", 0.8696],
  ["token_sort", "John SMITH", "smith john", 1.0],
  ["token_sort", "John, Smith!", "smith john.", 1.0],
];

d("WASM scorer matches rapidfuzz/Python goldens", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [scorer, a, b, expected] of GOLDENS) {
    it(`${scorer}("${a}","${b}") ≈ ${expected}`, () => {
      // The covered scorer routes through the WASM backend (one boundary
      // crossing); the off-diagonal cell is the pair score.
      const m = scoreMatrix([a, b], scorer);
      expect(m[0]![1]!).toBeCloseTo(expected, 4);
    });
  }
});

// The two fs-core name scorers (ids 20/21) score over the census / alias tables
// the loader injects at enableWasm(). Their reference is the pure-TS `scoreField`
// name branch (NOT a rapidfuzz golden), matched to 4dp — the surface bar (WASM
// rapidfuzz JW vs the pure-TS JW differ within tolerance; #879 aligned them).
// These cases also PROVE injection ran: without the census table the borderline
// common-surname pair would score plain JW (~0.90) instead of the down-weighted
// value (~0.56), and without the alias table William/Bill would score ~0.55
// instead of 1.0 — both far outside 4dp.
type NameCase = readonly [scorer: string, a: string, b: string, note: string];
const NAME_CASES: readonly NameCase[] = [
  ["given_name_aliased_jw", "William", "Bill", "alias -> 1.0 (proves alias inject)"],
  ["given_name_aliased_jw", "Robert", "Bob", "alias -> 1.0"],
  ["given_name_aliased_jw", "William", "Walter", "non-alias -> plain JW"],
  ["name_freq_weighted_jw", "Smith", "Smyth", "borderline common -> down-weighted (proves census inject)"],
  ["name_freq_weighted_jw", "Smith", "Smith", "exact (jw>=0.95) -> plain JW = 1.0"],
  ["name_freq_weighted_jw", "Xzzyqwb", "Xzzyqwc", "OOV borderline -> plain JW"],
];

d("WASM name scorers match the pure-TS scoreField reference (4dp)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [scorer, a, b, note] of NAME_CASES) {
    it(`${scorer}("${a}","${b}") ${note}`, () => {
      // scoreField never consults the WASM backend — it is the pure-TS
      // reference regardless of enable state.
      const ref = scoreField(a, b, scorer);
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], scorer);
      expect(m[0]![1]!).toBeCloseTo(ref!, 4);
    });
  }
});
