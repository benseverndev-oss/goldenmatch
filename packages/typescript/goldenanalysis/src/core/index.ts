// GoldenAnalysis core — edge-safe (no node: imports).

export { analyze } from "./analyze.js";
export type { AnalyzeOptions } from "./analyze.js";
export { toJson, toMarkdown } from "./render.js";
export { availableAnalyzers, loadAnalyzer, frameCompatibleAnalyzers } from "./registry.js";
export { FrameSummaryAnalyzer } from "./analyzers/frameSummary.js";
export * as aggregate from "./aggregate.js";
export { SCHEMA_VERSION } from "./types.js";
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
