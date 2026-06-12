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
  exact: 3,
};

/**
 * Scorers the WASM matrix path accelerates in slice 1. token_sort (id 2) is
 * deferred — its normalization parity is unresolved (see the design spec).
 */
export const WASM_COVERED_SCORERS: ReadonlySet<string> = new Set(
  Object.keys(SCORER_ID),
);

/** A WASM-backed (or stub) NxN matrix scorer. Null handling is the caller's. */
export interface ScorerBackend {
  /** Row-major NxN similarity matrix for `values` under `scorerName`. */
  scoreMatrix(values: readonly string[], scorerName: string): Float64Array;
}

let _backend: ScorerBackend | null = null;

export function setScorerBackend(b: ScorerBackend | null): void {
  _backend = b;
}

export function getScorerBackend(): ScorerBackend | null {
  return _backend;
}
