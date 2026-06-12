/**
 * WASM scorer parity vs the Python / rapidfuzz source of truth.
 *
 * The WASM kernel IS rapidfuzz (it wraps the `score-core` crate, the same crate
 * the Python `native` wheel and the DataFusion UDFs wrap), so this gate asserts
 * the opt-in WASM path reproduces the canonical rapidfuzz values to 4 decimals.
 * That proves the whole boundary chain end-to-end: scorer-id mapping (0/1/3),
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
import { scoreMatrix } from "../../src/core/scorer.js";

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
