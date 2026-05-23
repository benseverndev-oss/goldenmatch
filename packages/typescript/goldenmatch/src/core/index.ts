/**
 * index.ts — Core public API surface for GoldenMatch-JS.
 * Re-exports everything from core modules.
 *
 * Edge-safe: no `node:` imports.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type {
  Row,
  ColumnValue,
  PairKey,
  MatchkeyConfig,
  ExactMatchkey,
  WeightedMatchkey,
  ProbabilisticMatchkey,
  MakeMatchkeyConfigInput,
  MatchkeyField,
  NegativeEvidenceField,
  BlockingConfig,
  BlockingKeyConfig,
  SortKeyField,
  CanopyConfig,
  GoldenRulesConfig,
  GoldenFieldRule,
  StandardizationConfig,
  ValidationRuleConfig,
  ValidationConfig,
  QualityConfig,
  TransformConfig,
  BudgetConfig,
  LLMScorerConfig,
  DomainConfig,
  InputFileConfig,
  InputConfig,
  OutputConfig,
  GoldenMatchConfig,
  ScoredPair,
  ClusterInfo,
  DedupeStats,
  DedupeResult,
  MatchResult,
  FieldProvenance,
  ClusterProvenance,
  BlockResult,
} from "./types.js";

export {
  VALID_SCORERS,
  VALID_TRANSFORMS,
  VALID_STRATEGIES,
  VALID_STANDARDIZERS,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  makeMatchkeyConfig,
} from "./types.js";

export {
  applyNegativeEvidence,
  applyNegativeEvidenceToExactPairs,
  promoteNegativeEvidence,
  pickScorerForColumn,
} from "./autoconfigNegativeEvidence.js";

export {
  makeBlockingConfig,
  makeGoldenRulesConfig,
  makeConfig,
  makeScoredPair,
  getMatchkeys,
} from "./types.js";

// ---------------------------------------------------------------------------
// Data layer
// ---------------------------------------------------------------------------

export { TabularData, isNullish, toColumnValue } from "./data.js";

// ---------------------------------------------------------------------------
// Transforms
// ---------------------------------------------------------------------------

export { applyTransform, applyTransforms, soundex, metaphone } from "./transforms.js";

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

export {
  scoreField,
  scorePair,
  findExactMatches,
  findFuzzyMatches,
  scoreBlocksSequential,
  jaro,
  jaroWinkler,
  levenshteinDistance,
  levenshteinSimilarity,
  indelDistance,
  indelSimilarity,
  tokenSortRatio,
  soundexMatch,
  diceCoefficient,
  jaccardSimilarity,
  ensembleScore,
  scoreMatrix,
  asString,
} from "./scorer.js";

// ---------------------------------------------------------------------------
// Matchkey
// ---------------------------------------------------------------------------

export {
  computeMatchkeyValue,
  computeMatchkeys,
  addRowIds,
  addSourceColumn,
} from "./matchkey.js";

// ---------------------------------------------------------------------------
// Standardization
// ---------------------------------------------------------------------------

export { applyStandardizer, applyStandardization } from "./standardize.js";

// ---------------------------------------------------------------------------
// Blocking
// ---------------------------------------------------------------------------

export {
  buildBlocks,
  buildBlocksAsync,
  buildStaticBlocks,
  buildMultiPassBlocks,
  buildAdaptiveBlocks,
  selectBestBlockingKey,
} from "./blocker.js";

// ---------------------------------------------------------------------------
// Embedding + ANN + Cross-encoder
// ---------------------------------------------------------------------------

export { Embedder, getEmbedder, EmbedderError } from "./embedder.js";
export type {
  EmbedderOptions,
  EmbeddingResult,
  EmbedderProvider,
} from "./embedder.js";
export {
  ANNBlocker,
  HNSWANNBlocker,
  createANNBlocker,
  buildANNBlocks,
  buildANNPairBlocks,
  cosineSim,
  euclideanDist,
} from "./ann-blocker.js";
export type {
  ANNBlockerOptions,
  ANNBlockerBase,
  BuildANNOptions,
  HNSWOptions,
  HNSWModule,
  HNSWIndexLike,
  CreateANNBlockerOptions,
} from "./ann-blocker.js";
export {
  rerankTopPairs,
  rerankPair,
  CrossEncoderHttpError,
  CrossEncoderModel,
  _resetCrossEncoderModelCache,
} from "./cross-encoder.js";
export type {
  CrossEncoderOptions,
  CrossEncoderProvider,
  CrossEncoderReranker,
  CrossEncoderModelOptions,
} from "./cross-encoder.js";

// ---------------------------------------------------------------------------
// Clustering
// ---------------------------------------------------------------------------

export {
  UnionFind,
  buildClusters,
  buildMst,
  splitOversizedCluster,
  computeClusterConfidence,
  addToCluster,
  unmergeRecord,
  unmergeCluster,
  pairKey,
  parsePairKey,
  getClusterPairScores,
} from "./cluster.js";

// ---------------------------------------------------------------------------
// Golden records
// ---------------------------------------------------------------------------

export {
  mergeField,
  buildGoldenRecord,
  buildGoldenRecordWithProvenance,
} from "./golden.js";

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export { runDedupePipeline, runMatchPipeline } from "./pipeline.js";

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export { dedupe, match, scoreStrings, scorePairRecord } from "./api.js";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export { parseConfig, parseConfigYaml, configToYaml } from "./config/loader.js";

// ---------------------------------------------------------------------------
// LLM
// ---------------------------------------------------------------------------

export { BudgetTracker, countTokensApprox } from "./llm/budget.js";
export type { BudgetSnapshot } from "./llm/budget.js";
export { llmScorePairs, scoreStringsWithLlm } from "./llm/scorer.js";
export type { LLMScoreResult } from "./llm/scorer.js";
export { llmClusterPairs } from "./llm/cluster.js";
export { whyForCorrection, llmExplainPair } from "./llm/explain.js";
export type { WhyOptions, WhyInput } from "./llm/explain.js";

// ---------------------------------------------------------------------------
// Explain
// ---------------------------------------------------------------------------

export { explainPair, explainCluster } from "./explain.js";
export type { PairExplanation, ClusterExplanation } from "./explain.js";

// ---------------------------------------------------------------------------
// Probabilistic (Fellegi-Sunter)
// ---------------------------------------------------------------------------

export { buildComparisonVector, trainEM, scoreProbabilistic } from "./probabilistic.js";
export type { EMResult } from "./probabilistic.js";

// ---------------------------------------------------------------------------
// Evaluation
// ---------------------------------------------------------------------------

export { evaluatePairs, evaluateClusters, loadGroundTruthPairs } from "./evaluate.js";
export type { EvalResult } from "./evaluate.js";

// ---------------------------------------------------------------------------
// Streaming / match-one
// ---------------------------------------------------------------------------

export { StreamProcessor } from "./streaming.js";
export { matchOne, findExactMatchesOne } from "./match-one.js";

// ---------------------------------------------------------------------------
// Cluster comparison + sensitivity
// ---------------------------------------------------------------------------

export { compareClusters } from "./compare-clusters.js";
export type { CCMSResult } from "./compare-clusters.js";
export { runSensitivity, stabilityReport } from "./sensitivity.js";
export type { SweepParam, SweepPoint, SensitivityResult } from "./sensitivity.js";

// ---------------------------------------------------------------------------
// Quality, autofix, validation, profiling, ingest
// ---------------------------------------------------------------------------

export { scanQuality, runQualityCheck } from "./quality.js";
export type { QualityFinding } from "./quality.js";
export { autoFixRows } from "./autofix.js";
export type { AutoFixLog } from "./autofix.js";
export { validateRows } from "./validate.js";
export type { ValidationRule, ValidationReport } from "./validate.js";
export { profileRows } from "./profiler.js";
export type { ColumnProfile, DatasetProfile } from "./profiler.js";
export { applyColumnMap, validateColumns, concatRows } from "./ingest.js";

// ---------------------------------------------------------------------------
// Review queue, autoconfig, domain, lineage, learned blocking, graph ER
// ---------------------------------------------------------------------------

export { ReviewQueue, gatePairs } from "./review-queue.js";
export type { ReviewItem, GatedResult } from "./review-queue.js";
export { autoConfigureRows, autoConfigureRowsIterate } from "./autoconfig.js";
export type { AutoconfigOptions } from "./autoconfig.js";
export {
  AutoConfigController,
  ConfigValidationError as ControllerConfigValidationError,
  makeControllerBudget,
  getLastControllerRun,
} from "./autoconfigController.js";
export type {
  ControllerBudget,
  ControllerOptions,
  ControllerRunResult,
} from "./autoconfigController.js";
export {
  HeuristicRefitPolicy,
  createDefaultPolicy,
} from "./autoconfigPolicy.js";
export type {
  RefitPolicy,
  Rule,
  RuleContext,
  RuleOutcome,
} from "./autoconfigPolicy.js";
export {
  RunHistory,
  RED_PROFILE,
} from "./autoconfigHistory.js";
export type {
  PolicyDecision,
  ErrorRecord,
  HistoryEntry,
} from "./autoconfigHistory.js";
export {
  HealthVerdict,
  StopReason,
  makeComplexityProfile,
  makeDataProfile,
  makeBlockingProfile,
  makeScoringProfile,
  makeClusterProfile,
  makeProfileMeta,
  makeDomainProfile as makeComplexityDomainProfile,
  makeMatchkeyProfile,
  complexityHealth,
  computeDataProfile,
  normalizedSignalVector,
} from "./complexityProfile.js";
export type {
  ComplexityProfile,
  DataProfile,
  DomainProfile as ComplexityDomainProfile,
  MatchkeyProfile,
  BlockingProfile,
  ScoringProfile,
  ClusterProfile,
  ProfileMeta,
  IndicatorsProfile,
  ColumnPrior,
  SparsityVerdict,
  CollisionSignal,
} from "./complexityProfile.js";
export {
  DEFAULT_RULES_V1_7_V1_8,
  ruleBlockingSingletonTrap,
  ruleBlockingTooCoarse,
  ruleBlockingKeySwap,
  ruleLowReductionRatio,
  ruleLowTransitivity,
  ruleNoMatches,
  ruleUnimodalScoring,
} from "./autoconfigRules.js";
export { detectDomain, extractFeatures } from "./domain.js";
export type { DomainProfile } from "./domain.js";
export { buildLineage, lineageToJson, lineageFromJson } from "./lineage.js";
export type { LineageEdge, LineageBundle } from "./lineage.js";
export { learnBlockingRules, applyLearnedBlocks } from "./learned-blocking.js";
export type { LearnedPredicate, LearnedRules } from "./learned-blocking.js";
export { runGraphER } from "./graph-er.js";
export type { TableSchema, Relationship, GraphERResult } from "./graph-er.js";

// ---------------------------------------------------------------------------
// Memory (learning corrections)
// ---------------------------------------------------------------------------

export * from "./memory/index.js";
export * from "./identity/index.js";

// ---------------------------------------------------------------------------
// PPRL (Privacy-Preserving Record Linkage)
// ---------------------------------------------------------------------------

export { runPPRL, autoConfigurePPRL } from "./pprl/protocol.js";
export type { PPRLConfig, PPRLResult } from "./pprl/protocol.js";

// ---------------------------------------------------------------------------
// Auto-config verification (preflight + postflight)
// ---------------------------------------------------------------------------

export {
  preflight,
  postflight,
  makePreflightReport,
  stripConventionPrivate,
  ConfigValidationError,
} from "./autoconfigVerify.js";
export type {
  PreflightCheckName,
  Severity,
  PreflightFinding,
  PreflightReport,
  PostflightSignals,
  ScoreHistogram,
  BlockSizePercentiles,
  ClusterSizePercentiles,
  OversizedCluster,
  PostflightAdjustment,
  PostflightReport,
} from "./autoconfigVerify.js";


// v2.0.0 (#208): predefined golden-strategy plugin port.
export type {
  GoldenStrategyMergeOpts,
  GoldenStrategyPlugin,
  GoldenStrategyResult,
} from "./plugins/base.js";
export {
  AGGREGATION_BUILTINS,
  AgreementRateStrategy,
  CountDistinctStrategy,
  CountNonNullStrategy,
} from "./plugins/builtin/aggregation.js";
export {
  BUSINESS_BUILTINS,
  EnumCanonicalStrategy,
  FreshnessWithMaxAgeStrategy,
  LifecycleStageStrategy,
  RegexValidatedStrategy,
  SystemOfRecordStrategy,
  WeightedByRecencyStrategy,
} from "./plugins/builtin/business.js";
export {
  BooleanNormalizeStrategy,
  ConcatUniqueStrategy,
  EmailNormalizeStrategy,
  FORMAT_BUILTINS,
  PhoneDigitsOnlyStrategy,
  ShortestValueStrategy,
  UrlCanonicalStrategy,
  WhitespaceNormalizeStrategy,
} from "./plugins/builtin/format.js";
export {
  NUMERIC_BUILTINS,
  NumericMaxStrategy,
  NumericMeanStrategy,
  NumericMedianStrategy,
  NumericMinStrategy,
  NumericSumStrategy,
  NumericWeightedAverageStrategy,
} from "./plugins/builtin/numeric.js";
export type { PluginType } from "./plugins/registry.js";
export { BUILTIN_PLUGINS, PluginRegistry } from "./plugins/registry.js";
