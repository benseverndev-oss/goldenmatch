/**
 * Relation profiler registry — all cross-column profilers.
 * Mirrors RELATION_PROFILERS in goldencheck/engine/scanner.py.
 */

export { TemporalOrderProfiler } from "./temporal.js";
export { NullCorrelationProfiler } from "./null-correlation.js";
export { NumericCrossColumnProfiler } from "./numeric-cross.js";
export { AgeValidationProfiler } from "./age-validation.js";

import type { RelationProfiler } from "../profilers/base.js";
import { TemporalOrderProfiler } from "./temporal.js";
import { NullCorrelationProfiler } from "./null-correlation.js";
import { NumericCrossColumnProfiler } from "./numeric-cross.js";
import { AgeValidationProfiler } from "./age-validation.js";

/** All relation profilers in execution order. */
export const RELATION_PROFILERS: readonly RelationProfiler[] = [
  new TemporalOrderProfiler(),
  new NullCorrelationProfiler(),
  new NumericCrossColumnProfiler(),
  new AgeValidationProfiler(),
];
