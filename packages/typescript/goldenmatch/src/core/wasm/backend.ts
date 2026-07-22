/**
 * backend.ts — opt-in WASM scorer backend registry. Edge-safe: no node:* here.
 *
 * The active backend (if any) is consulted by scorer.ts's `scoreMatrix` for the
 * COVERED scorers only; everything else stays pure-TS. Mirrors the
 * setSyncEmbedder(null) module-singleton pattern for test isolation.
 */

/**
 * Scorer ids understood by the score-wasm kernel. Ids 0..=3 match
 * score-core::score_one; ids 20/21 are the score-wasm-only name scorers (over
 * fs-core's `given_name_aliased_sim` / `name_freq_weighted_sim`, ≥20 to leave
 * headroom above score_one's 0..=8 — see score-wasm/src/lib.rs). The name
 * scorers need their census / alias reference-data tables injected once at
 * `enableWasm()` (the loader does this); until then the kernel degrades them to
 * plain Jaro-Winkler, the same table-absent fallback the pure-TS path takes.
 *
 * `qgram` (id 5) routes through score-core's `qgram_similarity` (char-trigram
 * Jaccard: lowercase, `##`-pad each side, set intersection/union). The pure-TS
 * `qgramScore` computes the identical set math, so the WASM matrix is byte-exact
 * with the fallback on the ASCII/Latin inputs q-gram targets (short codes / SKUs
 * / names) — the same parity bar the four core rapidfuzz scorers hold.
 *
 * `date` (id 4) routes through score-core's `date_similarity`: parse `YYYY-MM-DD`
 * to 8 digits, Damerau-Levenshtein bucketed 0->1.0 / 1->0.90 / 2->0.75 / >=3->0.0,
 * else `levenshtein` on the raw strings. The kernel uses rapidfuzz TRUE DL and the
 * pure-TS `dateSimilarity` uses OSA — but for EQUAL-length inputs (both sides are
 * 8 digits) OSA and true-DL first diverge only at distance 3, which both bucket to
 * 0.0, so the two are byte-exact for every date pair (exhaustively verified; the
 * non-ISO branch is plain `levenshtein`, itself an exact shared kernel).
 *
 * `dice` (id 9) routes through score-core's `dice_similarity` (Sorensen-Dice on two
 * hex bloom filters: `2*popcount(A&B) / (popcount(A)+popcount(B))`). The pure-TS
 * `diceCoefficient` does the identical integer popcount + one f64 divide, so the
 * WASM matrix is byte-exact with the fallback on any valid (even-length) bloom hex —
 * the shape the `bloom_filter` transform always emits (malformed hex is the only
 * divergence, and never reaches a real PPRL/CLK column).
 *
 * `jaccard` (id 10) routes through score-core's `jaccard_similarity` (bloom-filter
 * Jaccard: `popcount(A&B) / popcount(A|B)`). The kernel computes the union by
 * inclusion-exclusion (`popcount(A)+popcount(B)-popcount(A&B)`) while the pure-TS
 * `jaccardSimilarity` popcounts the actual bit-OR — algebraically identical for
 * bloom filters — so the WASM matrix is byte-exact with the fallback on any valid
 * (even-length) bloom hex, same as dice.
 *
 * `soundex_match` (id 6) routes through score-core's `soundex_match` (1.0 iff a
 * NON-EMPTY canonical soundex code is shared, else 0.0 — the empty-code guard means
 * garbage/empty never matches). The kernel's `soundex` and the pure-TS `soundex`
 * transform are the SAME Unicode-folding standard-Soundex spec (separators break the
 * coding run; NFKD folds accents; no-letter -> ""), so the WASM matrix is byte-exact
 * with the pure-TS `soundexMatch` fallback. Like `exact`, the bucket path intercepts
 * soundex_match with an O(n) hash-group specialization (`buildScoreMatrix`), so this
 * id is exercised only when `scoreMatrix` is called directly — but the kernel is
 * reachable and byte-exact, which is the "kernel-backed / shared" contract.
 *
 * `phash` (id 11) is perceptual-hash similarity: `1 - hamming/nbits` over two hex
 * pHash strings. The kernel and the pure-TS `phashSimilarity` do the identical strict
 * hex decode + XOR popcount + one f64 divide, so the WASM matrix is byte-exact with the
 * fallback on any valid hex (a non-hex value -> 0.0 on both). Same integer-popcount
 * shape as dice/jaccard.
 *
 * `ensemble` (id 12) is `max(jaro_winkler, token_sort, 0.8*soundex_match)`. score-wasm
 * OVERRIDES id 12 (like id 2) to recompose it with the TS-parity NORMALIZED token_sort
 * (score_one(12)'s `ensemble_similarity` maxes over the un-normalized score_one(2)),
 * so the WASM matrix matches the pure-TS `ensembleScore` to 4dp — the same tolerance
 * its `jaro_winkler` / `token_sort` components already hold vs rapidfuzz (its
 * soundex_match component is byte-exact). Like exact/soundex_match, `buildScoreMatrix`
 * keeps the O(n) pure-TS `ensembleScoreMatrix` intercept; this id is reached only via a
 * direct `scoreMatrix` call, where the kernel is reachable + parity-proven.
 */
export const SCORER_ID: Readonly<Record<string, number>> = {
  jaro_winkler: 0,
  levenshtein: 1,
  token_sort: 2,
  exact: 3,
  date: 4,
  qgram: 5,
  soundex_match: 6,
  dice: 9,
  jaccard: 10,
  phash: 11,
  ensemble: 12,
  given_name_aliased_jw: 20,
  name_freq_weighted_jw: 21,
};

/**
 * Scorers the WASM matrix path accelerates. token_sort (id 2) routes through
 * score-core's `token_sort_normalized_ratio` (the TS-parity lowercase + strip +
 * token-sort normalize), so the WASM result matches the pure-TS `tokenSortRatio`
 * (NOT score-core's un-normalized `score_one(2)`). The two name scorers
 * (ids 20/21) reproduce the pure-TS `scoreField` name branches to 4dp (WASM
 * rapidfuzz JW vs the pure-TS JW differ within the surface's tolerance).
 */
export const WASM_COVERED_SCORERS: ReadonlySet<string> = new Set(
  Object.keys(SCORER_ID),
);

/** A WASM-backed (or stub) NxN matrix scorer. Null handling is the caller's. */
export interface ScorerBackend {
  /**
   * Similarity matrix for `values` under `scorerName`.
   *
   * CONTRACT: the returned `Float64Array` MUST be a FULL row-major NxN matrix
   * (length `values.length ** 2`) and symmetric — the caller reads only the
   * upper triangle (`flat[i * n + j]`, j > i) and mirrors it, so a packed
   * upper-triangle buffer would silently yield wrong scores for the lower half.
   * The diagonal is ignored. The caller masks null cells to 0 (this never sees
   * nulls — `values` is already non-null strings).
   */
  scoreMatrix(values: readonly string[], scorerName: string): Float64Array;
}

import { createBackendRegistry } from "goldenmatch-wasm-runtime";

const _registry = createBackendRegistry<ScorerBackend>();

export function setScorerBackend(b: ScorerBackend | null): void {
  _registry.set(b);
}

export function getScorerBackend(): ScorerBackend | null {
  return _registry.get();
}
