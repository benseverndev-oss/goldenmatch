/**
 * Deep profiling baseline — learn-once, monitor-forever.
 * TypeScript port of goldencheck/baseline/__init__.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { TabularData } from "../data.js";
import type { BaselineProfile, PatternGrammar } from "./models.js";
import { profileStatistical } from "./statistical.js";
import { mineConstraints } from "./constraints.js";
import { analyzeCorrelations } from "./correlation.js";
import { inducePatterns } from "./patterns.js";

// ---------------------------------------------------------------------------
// Re-exports
// ---------------------------------------------------------------------------

export type {
  StatProfile,
  FunctionalDependency,
  TemporalOrder,
  CorrelationEntry,
  PatternGrammar,
  ConfidencePrior,
  BaselineProfile,
} from "./models.js";

export { serializeBaseline, deserializeBaseline } from "./models.js";
export { profileStatistical } from "./statistical.js";
export { mineConstraints, type ConstraintResult } from "./constraints.js";
export { analyzeCorrelations } from "./correlation.js";
export { inducePatterns, induceColumnGrammars } from "./patterns.js";
export { buildPriors, applyPrior } from "./priors.js";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Create a deep-profiling BaselineProfile from TabularData.
 *
 * Orchestrates all profiling techniques:
 * 1. Statistical profiles (distribution, entropy, bounds, Benford's)
 * 2. Constraint mining (FDs, candidate keys, temporal orders)
 * 3. Correlation analysis (Pearson, Cramer's V)
 * 4. Pattern grammar induction
 *
 * Note: Confidence priors are NOT computed here because that requires running
 * the full scan pipeline (circular dependency). Use buildPriors() separately
 * after a scan, then attach to the profile.
 *
 * @param data - Input TabularData.
 * @param sourceFilename - Optional filename to embed in the profile.
 * @param dateColumns - Optional list of date column names for temporal order mining.
 * @returns A fully-populated BaselineProfile.
 */
export function createBaseline(
  data: TabularData,
  sourceFilename: string | null = null,
  dateColumns: string[] = [],
): BaselineProfile {
  // 1. Statistical profiles
  const stats = profileStatistical(data);

  // 2. Constraints
  const constraints = mineConstraints(data, 0.95, dateColumns);

  // 3. Correlations
  const correlations = analyzeCorrelations(data);

  // 4. Patterns
  const patternsRaw = inducePatterns(data);

  // Build semantic types stub (empty — full semantic classification is in semantic module)
  const semanticTypes: Record<string, string> = {};

  // Detect date columns from dtype if not explicitly provided
  const detectedDateCols: string[] = [];
  for (const col of data.columns) {
    const dt = data.dtype(col);
    if (dt === "date" || dt === "datetime") {
      detectedDateCols.push(col);
    }
  }

  return {
    sourceFilename,
    rowCount: data.rowCount,
    columnCount: data.columns.length,
    stats,
    functionalDeps: constraints.functionalDeps,
    candidateKeys: constraints.candidateKeys,
    temporalOrders: constraints.temporalOrders,
    correlations,
    patterns: patternsRaw,
    confidencePriors: {},
    semanticTypes,
  };
}
