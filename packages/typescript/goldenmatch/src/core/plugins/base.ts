/**
 * Plugin protocols for the goldenmatch TS port.
 *
 * Mirrors `goldenmatch.plugins.base` from the Python sibling. Phase 5
 * of v1.18 surface-sync roadmap: ship the GoldenStrategyPlugin protocol
 * first; ScorerPlugin / TransformPlugin / ConnectorPlugin land in
 * follow-up PRs as the TS port grows the matching execution paths.
 *
 * Spec: docs/superpowers/specs/2026-05-22-phase-5-typescript-port-design.md
 */

/**
 * Result shape returned by `GoldenStrategyPlugin.merge`.
 *
 * - `[value, confidence]` — confidence in [0, 1]; idx absent
 *   (synthesized value with no real source provenance)
 * - `[value, confidence, idx]` — `idx` is the position in the input
 *   `values` array that the chosen value came from. -1 sentinel for
 *   "synthesized; no idx" matches Python's idx=0 convention.
 *
 * Callers should treat both shapes uniformly; just read `[0]` /
 * `[1]` / `[2]` as needed.
 */
export type GoldenStrategyResult =
  | readonly [unknown, number]
  | readonly [unknown, number, number];

export interface GoldenStrategyMergeOpts {
  /** Per-value source names (positional with `values`). Used by
   *  source-priority / system-of-record strategies. */
  readonly sources?: ReadonlyArray<string | null>;
  /** Per-value date strings (positional with `values`). Used by
   *  most_recent / freshness_with_max_age / weighted_by_recency. */
  readonly dates?: ReadonlyArray<unknown>;
  /** Per-value quality scores in [0, 1]. Used by
   *  confidence_majority / numeric_weighted_average. */
  readonly qualityWeights?: ReadonlyArray<number>;
  /** Pair scores from the matcher; used by confidence-aware
   *  strategies. Map key shape mirrors Python's `(min_id, max_id)`. */
  readonly pairScores?: ReadonlyMap<string, number>;
  /** Strategy-specific kwargs (max_age_days, regex pattern, etc.). */
  readonly ruleKwargs?: Readonly<Record<string, unknown>>;
}

/**
 * Golden-record consolidation strategy: pick a single representative
 * value from a cluster's per-member values for a single field.
 *
 * Implementations must be PURE — no side effects, no mutation of
 * inputs. The Python sibling enforces this implicitly via
 * `runtime_checkable`; TS port relies on convention + parity tests.
 */
export interface GoldenStrategyPlugin {
  readonly name: string;
  merge(values: ReadonlyArray<unknown>, opts?: GoldenStrategyMergeOpts): GoldenStrategyResult;
}
