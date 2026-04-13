/**
 * Profiler registry — all column profilers in execution order.
 * Mirrors COLUMN_PROFILERS in goldencheck/engine/scanner.py.
 */

export type { Profiler, RelationProfiler } from "./base.js";

export { TypeInferenceProfiler } from "./type-inference.js";
export { NullabilityProfiler } from "./nullability.js";
export { UniquenessProfiler } from "./uniqueness.js";
export { FormatDetectionProfiler } from "./format-detection.js";
export { RangeDistributionProfiler } from "./range-distribution.js";
export { CardinalityProfiler } from "./cardinality.js";
export { PatternConsistencyProfiler, generalize } from "./pattern-consistency.js";
export { EncodingDetectionProfiler } from "./encoding-detection.js";
export { SequenceDetectionProfiler } from "./sequence-detection.js";
export { DriftDetectionProfiler } from "./drift-detection.js";

import type { Profiler } from "./base.js";
import { TypeInferenceProfiler } from "./type-inference.js";
import { NullabilityProfiler } from "./nullability.js";
import { UniquenessProfiler } from "./uniqueness.js";
import { FormatDetectionProfiler } from "./format-detection.js";
import { RangeDistributionProfiler } from "./range-distribution.js";
import { CardinalityProfiler } from "./cardinality.js";
import { PatternConsistencyProfiler } from "./pattern-consistency.js";
import { EncodingDetectionProfiler } from "./encoding-detection.js";
import { SequenceDetectionProfiler } from "./sequence-detection.js";
import { DriftDetectionProfiler } from "./drift-detection.js";

/** All column profilers in execution order. */
export const COLUMN_PROFILERS: readonly Profiler[] = [
  new TypeInferenceProfiler(),
  new NullabilityProfiler(),
  new UniquenessProfiler(),
  new FormatDetectionProfiler(),
  new RangeDistributionProfiler(),
  new CardinalityProfiler(),
  new PatternConsistencyProfiler(),
  new EncodingDetectionProfiler(),
  new SequenceDetectionProfiler(),
  new DriftDetectionProfiler(),
];
