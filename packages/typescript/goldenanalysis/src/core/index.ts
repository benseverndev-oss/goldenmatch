// GoldenAnalysis core — edge-safe (no node: imports).

export { analyze } from "./analyze.js";
export type { AnalyzeOptions } from "./analyze.js";
export { toJson, toMarkdown } from "./render.js";
export { availableAnalyzers, loadAnalyzer, frameCompatibleAnalyzers } from "./registry.js";
export { FrameSummaryAnalyzer } from "./analyzers/frameSummary.js";
export * as aggregate from "./aggregate.js";
export { SCHEMA_VERSION } from "./types.js";

// Cross-run (edge-safe): regression decision logic, report-level queries, narrative.
export {
  baselineValue,
  defaultPolicy,
  deltaPct,
  isRegression,
  policyThreshold,
} from "./regressions.js";
export type { Baseline, Regression, RegressionPolicy, TrendSeries } from "./regressions.js";
export { buildTrend, detectRegressions } from "./history.js";
export type { DetectOptions } from "./history.js";
export { buildNarrative } from "./narrative.js";
export type {
  Analyzer,
  AnalyzerInfo,
  AnalyzerInput,
  AnalyzerResult,
  AnalysisReport,
  AnalysisTable,
  Direction,
  FrameRows,
  Metric,
} from "./types.js";
