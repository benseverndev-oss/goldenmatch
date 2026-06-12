/**
 * GoldenAnalysis — read-only cross-cutting analysis/metrics/reporting (TypeScript).
 *
 * Phase 3a ships the generic frame path with cross-surface parity to the Python
 * package (the `AnalysisReport`/`Metric` wire types are snake_case to match the
 * JSON wire). Suite analyzers + cross-run land in later phases.
 *
 *   import { analyze, toMarkdown } from "goldenanalysis";
 *   const report = analyze(rows, ["frame.summary"]);
 *   console.log(toMarkdown(report));
 */

export const VERSION = "0.1.0";

export * from "./core/index.js";
