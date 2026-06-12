/**
 * Relation profiler registry — all cross-column profilers.
 * Mirrors RELATION_PROFILERS in goldencheck/engine/scanner.py.
 */

export { TemporalOrderProfiler } from "./temporal.js";
export { NullCorrelationProfiler } from "./null-correlation.js";
export { NumericCrossColumnProfiler } from "./numeric-cross.js";
export { AgeValidationProfiler } from "./age-validation.js";
export { IdentitySafePkProfiler } from "./identity-safe-pk.js";
export { CompositeKeyProfiler } from "./composite-key.js";
export { ApproxDuplicateProfiler } from "./approx-duplicate.js";
export { FunctionalDependencyProfiler } from "./functional-dependency.js";
export { ApproximateFDProfiler } from "./approx-fd.js";

import type { RelationProfiler } from "../profilers/base.js";
import { TemporalOrderProfiler } from "./temporal.js";
import { NullCorrelationProfiler } from "./null-correlation.js";
import { NumericCrossColumnProfiler } from "./numeric-cross.js";
import { AgeValidationProfiler } from "./age-validation.js";
import { IdentitySafePkProfiler } from "./identity-safe-pk.js";
import { CompositeKeyProfiler } from "./composite-key.js";
import { ApproxDuplicateProfiler } from "./approx-duplicate.js";
import { FunctionalDependencyProfiler } from "./functional-dependency.js";
import { ApproximateFDProfiler } from "./approx-fd.js";

/** All relation profilers in execution order. */
export const RELATION_PROFILERS: readonly RelationProfiler[] = [
  new TemporalOrderProfiler(),
  new NullCorrelationProfiler(),
  new NumericCrossColumnProfiler(),
  new AgeValidationProfiler(),
  // Preflight: warn when no stable PK column exists (goldenmatch #207).
  // Identity Graph downstreams need source_pk_column to avoid record_id
  // collisions on duplicate raw rows.
  new IdentitySafePkProfiler(),
  // Discover minimal composite keys when no single-column key exists.
  new CompositeKeyProfiler(),
  // Exact + near-duplicate (normalized) row detection.
  new ApproxDuplicateProfiler(),
  // Discover strict single-column functional dependencies.
  new FunctionalDependencyProfiler(),
  // Surface rows that BREAK a near-strict FD (likely data-entry errors).
  new ApproximateFDProfiler(),
];
