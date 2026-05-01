/**
 * Core public API — edge-safe (no Node dependencies).
 */

// Types
export type {
  TransformMode,
  TransformInfo,
  TransformFunction,
  ColumnValue,
  Row,
  Dtype,
  TransformRecord,
  TransformError,
  Manifest,
  TransformResult,
  ColumnProfile,
  DatasetProfile,
  ColumnDiffDetail,
  DiffResult,
  TransformSpec,
  SplitSpec,
  FilterSpec,
  DedupSpec,
  MappingSpec,
  GoldenFlowConfig,
  ColumnMapping,
  DomainPack,
  RunRecord,
} from "./types.js";

// Factory functions
export {
  makeTransformRecord,
  makeManifest,
  MutableManifest,
  makeColumnProfile,
  makeConfig,
} from "./types.js";

// Data layer
export { TabularData, isNullish, toColumnValue } from "./data.js";

// Transform registry (imports all transform modules for side-effect registration)
export {
  registerTransform,
  getTransform,
  listTransforms,
  parseTransformName,
  registry,
} from "./transforms/index.js";

// Engine
export { TransformEngine } from "./engine/transformer.js";
export { profileDataframe } from "./engine/profiler-bridge.js";
export {
  selectTransforms,
  selectFromFindings,
  FINDING_TRANSFORM_MAP,
} from "./engine/selector.js";
export { diffDataframes } from "./engine/differ.js";
export { StreamProcessor } from "./engine/streaming.js";

// Config
export { validateConfig, loadConfigFromString, saveConfigToString, mergeConfigs } from "./config/index.js";
export { learnConfig } from "./config/learner.js";

// Mapping
export { nameSimilarity } from "./mapping/name-similarity.js";
export { profileSimilarity } from "./mapping/profile-similarity.js";
export { SchemaMapper } from "./mapping/schema-mapper.js";

// Domains
export { loadDomain, listDomains } from "./domains/index.js";

// Reporters
export { manifestToJson } from "./reporters/json-reporter.js";
export { printProfile, printManifest, printDiff } from "./reporters/console.js";

// LLM
export { applyLlmCorrections, prepareLlmCorrections } from "./llm/index.js";

// Notebook
export { transformResultToHtml, manifestToHtml, profileToHtml } from "./notebook.js";
