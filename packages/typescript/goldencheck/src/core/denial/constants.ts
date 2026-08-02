/**
 * Tunables for denial-constraint discovery (Stage 1) — parity port of
 * `packages/python/goldencheck/goldencheck/denial/constants.py`.
 */

/** Only mine equality literals on columns with <= this many distinct values. */
export const MAX_LITERAL_CARD = 50;
/** A literal/predicate must apply to >= this fraction of rows. */
export const MIN_SUPPORT = 0.01;
/** Per evidence pass; a satisfaction mask fits one u64. */
export const MAX_PREDICATES = 64;
/** S rows for the pairwise pass. */
export const DEFAULT_SAMPLE = 2000;
/** Bounded sample for cross-tuple g1 validation. */
export const VALIDATION_SAMPLE = 20000;
/** g1 threshold: keep DCs violated by <= eps of elements. */
export const DEFAULT_EPS = 0.05;
/** Top-N reported. */
export const MAX_CONSTRAINTS = 20;
/** Skip discovery below this row count. */
export const MIN_ROWS = 100;
/** Max predicates per DC (tractability + interestingness bound). */
export const MAX_REPORT_ARITY = 2;
