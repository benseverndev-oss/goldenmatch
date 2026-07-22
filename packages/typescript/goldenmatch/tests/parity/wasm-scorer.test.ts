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

// qgram (score_one id 5) is char-trigram Jaccard: WASM `qgram_similarity` and the
// pure-TS `qgramScore` compute the identical set math (lowercase, `##`-pad each
// side, |A∩B|/|A∪B|, with an a===b -> 1.0 short-circuit), so the WASM matrix
// equals the pure-TS fallback exactly (not merely to tolerance — it is rational
// set arithmetic, no rapidfuzz float divergence). This is the parity that lets
// qgram move to the `scorer_kernels` SHARED partition (kernel-backed on both
// Python arrow-native AND TS WASM). Reference is the pure-TS `scoreField`.
type QgramCase = readonly [a: string, b: string, note: string];
const QGRAM_CASES: readonly QgramCase[] = [
  ["hello", "hello", "identical -> 1.0"],
  ["SKU123", "sku123", "case-insensitive -> 1.0"],
  ["abcdef", "abcxyz", "partial trigram overlap"],
  ["cat", "dog", "disjoint -> low"],
  ["café", "cafe", "accented BMP, partial overlap"],
  ["ab", "abc", "short (n=3 padding still forms grams)"],
  ["", "", "both empty -> identical short-circuit"],
];

d("WASM qgram matches the pure-TS scoreField reference (exact)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [a, b, note] of QGRAM_CASES) {
    it(`qgram("${a}","${b}") ${note}`, () => {
      const ref = scoreField(a, b, "qgram");
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], "qgram");
      // Set-Jaccard is rational on both surfaces -> byte-exact, not just 4dp.
      expect(m[0]![1]!).toBe(ref!);
    });
  }
});

// date (score_one id 4) parses YYYY-MM-DD -> 8 digits and buckets the
// Damerau-Levenshtein distance (0->1.0, 1->0.90, 2->0.75, >=3->0.0), else falls
// back to plain levenshtein. The WASM kernel uses rapidfuzz TRUE DL; the pure-TS
// `dateSimilarity` uses OSA. For EQUAL-length inputs (both 8 digits) OSA and
// true-DL first diverge only at distance 3 -- which both bucket to 0.0 -- so the
// two surfaces are byte-exact on every date pair (exhaustively verified over the
// distance-<=2 region). The non-ISO branch is plain levenshtein (an exact shared
// kernel). Reference is the pure-TS `scoreField`; asserted byte-exact.
type DateCase = readonly [a: string, b: string, note: string];
const DATE_CASES: readonly DateCase[] = [
  ["1990-05-12", "1990-05-12", "same date -> 1.0"],
  ["1990-05-12", "1990-05-21", "adjacent-digit transposition -> DL 1 -> 0.90"],
  ["1990-05-12", "1990-06-12", "one digit typo -> 0.90"],
  ["1990-05-12", "1991-06-12", "two edits -> 0.75"],
  ["1990-05-12", "2003-11-28", "unrelated -> DL>=3 -> 0.0"],
  ["2001-02-13", "2010-02-31", "multi-transposition, DL still bucketed"],
  ["1990-05-12", "not-a-date", "non-ISO b -> levenshtein fallback"],
  ["12 May 1990", "12 May 1991", "both non-ISO -> levenshtein fallback"],
];

d("WASM date matches the pure-TS scoreField reference (exact)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [a, b, note] of DATE_CASES) {
    it(`date("${a}","${b}") ${note}`, () => {
      const ref = scoreField(a, b, "date");
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], "date");
      expect(m[0]![1]!).toBe(ref!);
    });
  }
});

// dice (score_one id 9) is Sorensen-Dice on two hex bloom filters:
// 2*popcount(A&B)/(popcount(A)+popcount(B)). WASM `dice_similarity` and the pure-TS
// `diceCoefficient` do the identical integer popcount + one f64 divide, so the WASM
// matrix equals the pure-TS fallback byte-exact on any valid (even-length) bloom hex
// -- the shape the `bloom_filter` transform always emits (varied byte lengths handled
// by the min-length intersection; malformed hex is the only divergence and never
// reaches a real CLK column). Inputs are valid CLK-style hex; asserted byte-exact.
type DiceCase = readonly [a: string, b: string, note: string];
const DICE_CASES: readonly DiceCase[] = [
  ["ffff", "ffff", "identical -> 1.0"],
  ["ff00", "00ff", "disjoint bits -> 0.0"],
  ["deadbeef", "deadbe", "different byte lengths (min-len intersection)"],
  ["ABCD", "abcd", "uppercase hex parses same -> 1.0"],
  ["0000", "ffff", "one side all-zero -> 0.0"],
  ["0000", "0000", "both all-zero -> total 0 -> 0.0"],
  ["f0f0a5", "a5f0f0", "partial overlap"],
];

d("WASM dice matches the pure-TS scoreField reference (exact)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [a, b, note] of DICE_CASES) {
    it(`dice("${a}","${b}") ${note}`, () => {
      const ref = scoreField(a, b, "dice");
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], "dice");
      // Integer popcount + one f64 divide on both surfaces -> byte-exact.
      expect(m[0]![1]!).toBe(ref!);
    });
  }
});

// jaccard (score_one id 10) is bloom-filter Jaccard: popcount(A&B)/popcount(A|B).
// The kernel derives the union by inclusion-exclusion (pcA+pcB-inter) and the pure-TS
// `jaccardSimilarity` popcounts the actual bit-OR -- algebraically identical for bloom
// filters -- so the WASM matrix is byte-exact with the fallback on any valid
// even-length bloom hex (the min-length intersection covers differing byte lengths;
// malformed hex is the only divergence and never reaches a real CLK column). Asserted
// byte-exact.
type JaccardCase = readonly [a: string, b: string, note: string];
const JACCARD_CASES: readonly JaccardCase[] = [
  ["ffff", "ffff", "identical -> 1.0"],
  ["ff00", "00ff", "disjoint bits -> 0.0"],
  ["deadbeef", "deadbe", "different byte lengths (union over the longer)"],
  ["ABCD", "abcd", "uppercase hex parses same -> 1.0"],
  ["0000", "ffff", "one side all-zero -> union>0, inter 0 -> 0.0"],
  ["0000", "0000", "both all-zero -> union 0 -> 0.0"],
  ["f0f0a5", "a5f0f0", "partial overlap"],
];

d("WASM jaccard matches the pure-TS scoreField reference (exact)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [a, b, note] of JACCARD_CASES) {
    it(`jaccard("${a}","${b}") ${note}`, () => {
      const ref = scoreField(a, b, "jaccard");
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], "jaccard");
      expect(m[0]![1]!).toBe(ref!);
    });
  }
});

// soundex_match (score_one id 6): 1.0 iff a NON-EMPTY canonical soundex code is
// shared, else 0.0 (empty-code guard). The kernel `soundex` and the pure-TS
// `soundex` transform are the SAME Unicode-folding standard-Soundex spec
// (separators break the coding run; NFKD folds accents; no-letter -> ""), so the
// WASM matrix is byte-exact with the pure-TS `soundexMatch` fallback -- including
// the multi-token separator cases the strip variant regressed. Asserted byte-exact.
type SoundexCase = readonly [a: string, b: string, note: string];
const SOUNDEX_CASES: readonly SoundexCase[] = [
  ["Robert", "Rupert", "collide on R163 -> 1.0"],
  ["Smith", "Smyth", "collide on S530 -> 1.0"],
  ["Robert", "Smith", "different codes -> 0.0"],
  ["joseph bradshaw", "joseph bradshaw", "multi-token J211 -> 1.0 (separator-aware)"],
  ["Muñoz", "Munoz", "accent folds to M520 -> 1.0"],
  ["123", "456", "both no-letter -> '' -> empty-guard 0.0"],
  ["123", "123", "identical garbage -> '' -> 0.0 (never self-matches)"],
  ["", "", "both empty -> 0.0"],
  ["café", "cafe", "accented vowel folds -> collide"],
];

d("WASM soundex_match matches the pure-TS scoreField reference (exact)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [a, b, note] of SOUNDEX_CASES) {
    it(`soundex_match("${a}","${b}") ${note}`, () => {
      const ref = scoreField(a, b, "soundex_match");
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], "soundex_match");
      expect(m[0]![1]!).toBe(ref!);
    });
  }
});

// ensemble (score_one id 12, overridden in score_matrix_impl): max of jaro_winkler,
// the NORMALIZED token_sort, and 0.8*soundex_match -- the same three the pure-TS
// `ensembleScore` maxes. The soundex component is byte-exact; jw and token_sort are
// rapidfuzz (kernel) vs the hand-rolled pure-TS, which agree to 4dp -- so ensemble is
// asserted to 4dp vs the pure-TS reference (the same bar jaro_winkler / token_sort
// hold individually), each case chosen so a different component dominates the max.
type EnsembleCase = readonly [a: string, b: string, note: string];
const ENSEMBLE_CASES: readonly EnsembleCase[] = [
  ["John SMITH", "smith john", "normalized token_sort dominates -> 1.0"],
  ["Robert", "Rupert", "soundex R163 bonus 0.8 dominates lowish jw"],
  ["MARTHA", "MARHTA", "jaro_winkler dominates (0.9611)"],
  ["New York Mets", "Mets New York", "token_sort reorder -> 1.0"],
  ["abc", "abd", "jw dominates a no-soundex-match pair"],
  ["café", "cafe", "jw on accented input"],
];

d("WASM ensemble matches the pure-TS scoreField reference (4dp)", () => {
  beforeAll(async () => {
    const ok = await enableWasm();
    if (!ok) throw new Error("artifact present but enableWasm() failed");
  });
  afterAll(() => disableWasm());

  for (const [a, b, note] of ENSEMBLE_CASES) {
    it(`ensemble("${a}","${b}") ${note}`, () => {
      const ref = scoreField(a, b, "ensemble");
      expect(ref).not.toBeNull();
      const m = scoreMatrix([a, b], "ensemble");
      expect(m[0]![1]!).toBeCloseTo(ref!, 4);
    });
  }
});
