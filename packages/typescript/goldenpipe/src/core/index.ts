/**
 * GoldenPipe core — edge-safe public API (no `node:` imports).
 *
 * Composes the edge-safe cores of GoldenCheck, GoldenFlow, and GoldenMatch
 * into one adaptive check → flow → dedupe pipeline operating on `Row[]`.
 */

// Models
export type {
  Row,
  Decision,
  StageResult,
  PipeContext,
  PipeResult,
  OnError,
  StageSpec,
  PipelineConfig,
  StageInfo,
  Stage,
} from "./models.js";
// Re-export the status objects as values too. TS merges these with the
// same-named type exports above (a type + value can share a name).
export {
  StageStatus,
  PipeStatus,
  makeDecision,
  makePipeContext,
  makeStageSpec,
  makePipelineConfig,
  stage,
} from "./models.js";

// Column context
export {
  ColumnType,
  CardinalityBand,
  MIN_CONFIDENCE,
  makeColumnContext,
  classifyByName,
  normalizeDtype,
  buildContextsFromCheck,
  enrichContextsFromFlow,
  distinctNonNull,
  nullRateOf,
} from "./columnContext.js";
export type {
  ColumnContext,
  ColumnProfileLike,
  FindingLike,
  ManifestRecordLike,
} from "./columnContext.js";

// Engine
export { StageRegistry, defaultRegistry } from "./engine/registry.js";
export { Resolver, WiringError } from "./engine/resolver.js";
export type { ExecutionPlan, PlannedStage } from "./engine/resolver.js";
export { Router } from "./engine/router.js";
export { Runner } from "./engine/runner.js";
export { Reporter } from "./engine/reporter.js";

// Decisions
export { severityGate, piiRouter, rowCountGate } from "./decisions.js";

// Adapters + registry wiring
export {
  LoadStage,
  ScanStage,
  TransformStage,
  DedupeStage,
  buildConfigFromContexts,
  buildDefaultRegistry,
} from "./adapters/index.js";

// Pipeline
export { Pipeline, runDf, runStages } from "./pipeline.js";
export type { PipelineOptions } from "./pipeline.js";
