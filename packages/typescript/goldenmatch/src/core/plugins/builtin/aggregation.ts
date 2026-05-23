/**
 * Aggregation / telemetry golden strategies (Phase 5 Part 4/N --
 * closes the final 3 of 22 plugins from goldenmatch issue #208).
 *
 * These return SYNTHESIZED scalar metrics about the cluster (not picks
 * from cluster members). For merge-audit columns like
 * `n_sources_agreeing` or `confidence_score`.
 */

import type {
  GoldenStrategyMergeOpts,
  GoldenStrategyPlugin,
  GoldenStrategyResult,
} from "../base.js";

export class CountDistinctStrategy implements GoldenStrategyPlugin {
  readonly name = "count_distinct";

  merge(
    values: ReadonlyArray<unknown>,
    _opts?: GoldenStrategyMergeOpts,
  ): GoldenStrategyResult {
    const nonNull = values.filter((v) => v !== null && v !== undefined);
    if (nonNull.length === 0) return [null, 0.0] as const;
    // JS `new Set(...)` deduplicates by SameValueZero -- matches
    // Python's `set()` for hashable scalars (strings, numbers, bools).
    const distinct = new Set(nonNull);
    return [distinct.size, 1.0, 0] as const;
  }
}

export class CountNonNullStrategy implements GoldenStrategyPlugin {
  readonly name = "count_non_null";

  merge(
    values: ReadonlyArray<unknown>,
    _opts?: GoldenStrategyMergeOpts,
  ): GoldenStrategyResult {
    let count = 0;
    for (const v of values) {
      if (v !== null && v !== undefined) count++;
    }
    // Python returns (0, 1.0) on all-null -- the count itself is
    // well-defined data. Match that explicitly.
    return [count, 1.0, 0] as const;
  }
}

export class AgreementRateStrategy implements GoldenStrategyPlugin {
  readonly name = "agreement_rate";

  merge(
    values: ReadonlyArray<unknown>,
    _opts?: GoldenStrategyMergeOpts,
  ): GoldenStrategyResult {
    const nonNull = values.filter((v) => v !== null && v !== undefined);
    if (nonNull.length === 0) return [null, 0.0] as const;
    const counts = new Map<unknown, number>();
    for (const v of nonNull) counts.set(v, (counts.get(v) ?? 0) + 1);
    let modeCount = 0;
    for (const c of counts.values()) {
      if (c > modeCount) modeCount = c;
    }
    const rate = modeCount / nonNull.length;
    const conf = nonNull.length / values.length;
    return [rate, conf, 0] as const;
  }
}

export const AGGREGATION_BUILTINS: readonly GoldenStrategyPlugin[] = [
  new CountDistinctStrategy(),
  new CountNonNullStrategy(),
  new AgreementRateStrategy(),
] as const;
