// GoldenAnalysis core — edge-safe (no node: imports).

export { analyze, analyzeMatch, analyzePipeline, artifactCompatibleAnalyzers } from "./analyze.js";
export type { AnalyzeMatchOptions, AnalyzeOptions } from "./analyze.js";
export { toJson, toMarkdown } from "./render.js";
export { availableAnalyzers, loadAnalyzer, frameCompatibleAnalyzers } from "./registry.js";
export { FrameSummaryAnalyzer } from "./analyzers/frameSummary.js";
export { MatchRatesAnalyzer } from "./analyzers/matchRates.js";
export { ClusterDistributionAnalyzer } from "./analyzers/clusterDist.js";
export { QualityRollupAnalyzer } from "./analyzers/qualityRollup.js";
export * as aggregate from "./aggregate.js";
export { enableAnalysisWasm, disableAnalysisWasm } from "./wasm/index.js";
export type { AnalysisBackend, EnableAnalysisWasmOptions } from "./wasm/index.js";
export { SCHEMA_VERSION } from "./types.js";

// Suite artifact adapters (edge-safe; duck-typed producer normalizers).
export { matchArtifacts, normalizeCert } from "./adapters/match.js";
export type { MatchAdapterOptions } from "./adapters/match.js";
export { flowArtifacts } from "./adapters/flow.js";
export { checkArtifacts } from "./adapters/check.js";
export { pipeArtifacts } from "./adapters/pipe.js";

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
