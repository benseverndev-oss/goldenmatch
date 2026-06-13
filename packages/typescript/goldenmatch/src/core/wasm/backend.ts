/**
 * backend.ts — opt-in WASM scorer backend registry. Edge-safe: no node:* here.
 *
 * The active backend (if any) is consulted by scorer.ts's `scoreMatrix` for the
 * COVERED scorers only; everything else stays pure-TS. Mirrors the
 * setSyncEmbedder(null) module-singleton pattern for test isolation.
 */

/** Scorer ids understood by the score-wasm kernel (match score-core::score_one). */
export const SCORER_ID: Readonly<Record<string, number>> = {
  jaro_winkler: 0,
  levenshtein: 1,
  token_sort: 2,
  exact: 3,
};

/**
 * Scorers the WASM matrix path accelerates. token_sort (id 2) routes through
 * score-core's `token_sort_normalized_ratio` (the TS-parity lowercase + strip +
 * token-sort normalize), so the WASM result matches the pure-TS `tokenSortRatio`
 * (NOT score-core's un-normalized `score_one(2)`).
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
