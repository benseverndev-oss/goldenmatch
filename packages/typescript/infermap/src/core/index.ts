// Edge-safe public surface.
export * from "./types.js";
export * from "./assignment/hungarian.js";
export type { Scorer } from "./scorers/base.js";
export { ExactScorer } from "./scorers/exact.js";
export { AliasScorer, DEFAULT_ALIASES } from "./scorers/alias.js";
export {
  PatternTypeScorer,
  SEMANTIC_TYPES,
  classifyField,
} from "./scorers/pattern-type.js";
export { ProfileScorer } from "./scorers/profile.js";
export { FuzzyNameScorer } from "./scorers/fuzzy-name.js";
export { LLMScorer } from "./scorers/llm.js";
export type { LLMAdapter, LLMScorerOptions } from "./scorers/llm.js";
export { defaultScorers, defineScorer } from "./scorers/registry.js";
export {
  jaroSimilarity,
  jaroWinklerSimilarity,
  levenshteinDistance,
} from "./util/string-distance.js";
export { InitialismScorer } from "./scorers/initialism.js";
export { buildLookup } from "./scorers/alias.js";
export {
  IdentityCalibrator,
  IsotonicCalibrator,
  PlattCalibrator,
  loadCalibrator,
  saveCalibrator,
} from "./calibration.js";
export type { Calibrator, CalibratorJSON } from "./calibration.js";
export {
  availableDomains,
  loadDomain,
  mergeDomains,
  UnknownDomainError,
} from "./dictionaries/index.js";
export { MapEngine, MIN_CONTRIBUTORS, commonAffixTokens, populateCanonicalNames } from "./engine.js";
export type { MapEngineOptions, MapSchemasOptions } from "./engine.js";

// Providers (edge-safe: operate on in-memory data or text)
export { parseCsv } from "./util/csv.js";
export type { CsvParseResult } from "./util/csv.js";
export {
  inferDtype,
  profileColumn,
  profileFieldFromValues,
  isNullLike,
} from "./util/profile.js";
export {
  InMemoryProvider,
  inferSchemaFromRecords,
} from "./providers/in-memory.js";
export type { InMemoryOptions } from "./providers/in-memory.js";
export {
  inferSchemaFromCsvText,
  inferSchemaFromJsonText,
} from "./providers/file.js";
export type { FileProviderOptions } from "./providers/file.js";
export {
  parseSchemaDefinition,
  SchemaParseError,
} from "./providers/schema-file.js";

// Config + public API
export {
  loadEngineConfig,
  applyScorerOverrides,
  fromConfig,
  mapResultToConfigJson,
  ConfigError,
} from "./config.js";
export type {
  EngineConfig,
  ScorerOverride,
  MapResultConfig,
} from "./config.js";
export { map, toSchemaInfo } from "./map.js";
export type { MapInput, MapOptions } from "./map.js";
