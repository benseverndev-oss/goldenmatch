/**
 * GoldenAnalysis node entry — persistence that needs `node:fs` (`ReportHistory`).
 *
 * The pure cross-run logic (`buildTrend` / `detectRegressions` / `buildNarrative` and
 * the regression models) is edge-safe and lives in `src/core`; it is re-exported here
 * for convenience so a Node consumer has one import.
 *
 *   import { ReportHistory } from "goldenanalysis/node";
 *   const hist = new ReportHistory({ path: ".golden/analysis.jsonl" });
 *   hist.append(report);
 *   const flagged = hist.detectRegressions("customers");
 */

export { ReportHistory, SCHEMA_VERSION } from "./history.js";
export type { QueryOptions, ReportHistoryOptions } from "./history.js";

export { buildTrend, detectRegressions } from "../core/history.js";
export type { DetectOptions } from "../core/history.js";
export { buildNarrative } from "../core/narrative.js";
export {
  baselineValue,
  defaultPolicy,
  deltaPct,
  isRegression,
  policyThreshold,
} from "../core/regressions.js";
export type { Baseline, Regression, RegressionPolicy, TrendSeries } from "../core/regressions.js";
