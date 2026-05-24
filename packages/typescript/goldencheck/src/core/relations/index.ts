/**
 * Relation profiler registry — all cross-column profilers.
 * Mirrors RELATION_PROFILERS in goldencheck/engine/scanner.py.
 */

export { TemporalOrderProfiler } from "./temporal.js";
export { NullCorrelationProfiler } from "./null-correlation.js";
export { NumericCrossColumnProfiler } from "./numeric-cross.js";
export { AgeValidationProfiler } from "./age-validation.js";
export { IdentitySafePkProfiler } from "./identity-safe-pk.js";

import type { RelationProfiler } from "../profilers/base.js";
import { TemporalOrderProfiler } from "./temporal.js";
import { NullCorrelationProfiler } from "./null-correlation.js";
import { NumericCrossColumnProfiler } from "./numeric-cross.js";
import { AgeValidationProfiler } from "./age-validation.js";
import { IdentitySafePkProfiler } from "./identity-safe-pk.js";

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
];
