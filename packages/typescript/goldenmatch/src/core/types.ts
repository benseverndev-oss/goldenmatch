/**
 * types.ts — GoldenMatch config interfaces and result types.
 * Edge-safe: no Node.js imports, no `process`.
 */

import type {
  PreflightReport,
  PostflightReport,
} from "./autoconfigVerify.js";
import type { CorrectionStats } from "./memory/types.js";

// ---------------------------------------------------------------------------
// Primitive types
// ---------------------------------------------------------------------------

export type ColumnValue = string | number | boolean | null;
export type Row = Readonly<Record<string, unknown>>;

/** A canonical pair key in the form "minId:maxId". Only produced by pairKey(). */
export type PairKey = string & { readonly __brand: "PairKey" };

// ---------------------------------------------------------------------------
// Matchkey field config
// ---------------------------------------------------------------------------

export interface MatchkeyField {
  readonly field: string;
  readonly transforms: readonly string[];
  readonly scorer: string;
  readonly weight: number;
  readonly model?: string;
  readonly columns?: readonly string[];
  readonly columnWeights?: Readonly<Record<string, number>>;
  readonly levels?: number;
  readonly partialThreshold?: number;
  /**
   * N-level custom banding (Splink-converter). Descending similarity cutoffs;
   * level = count of satisfied thresholds (0 = disagree, levels-1 = top agree).
   * Length must equal levels-1. Absent => legacy banding (partialThreshold for
   * 2/3 levels, even k/N spacing for N>3). Mirrors Python MatchkeyField.level_thresholds.
   */
  readonly levelThresholds?: readonly number[];
  /**
   * Winkler term-frequency adjustment flag (Splink-converter). Mirrors the
   * Python `MatchkeyField.tf_adjustment`. TS scoring/EM training does not
   * currently consume this — it is a pass-through so a Splink-imported field
   * with `tf_adjustment_column` set round-trips through the TS config layer
   * without silently losing the flag.
   */
  readonly tfAdjustment?: boolean;
  /** Optional data-driven frequency table consumed by frequency-aware scorers. */
  readonly tfFreqs?: Readonly<Record<string, number>>;
}

/**
 * v1.11: a field whose disagreement subtracts from a matchkey's score.
 * Mirrors the Python ``NegativeEvidenceField`` Pydantic model. Used both on
 * weighted matchkeys (penalty subtracted from weighted-sum score) and, in
 * v1.12 Path Y, on exact matchkeys (penalty filters the binary 1.0 emit).
 */
export interface NegativeEvidenceField {
  readonly field: string;
  readonly transforms: readonly string[];
  readonly scorer: string;
  /** Similarity cutoff in [0, 1]; the NE fires when scorer similarity is
   *  STRICTLY below it (both values present post-transform). */
  readonly threshold: number;
  /** Weighted/exact only: flat 0-1 penalty subtracted from the score when
   *  this field disagrees. REQUIRED for weighted/exact and REJECTED for
   *  probabilistic matchkeys — enforced by the loader validation matrix
   *  (mirrors Python `MatchkeyConfig` schemas.py). Probabilistic matchkeys
   *  use EM-learned NE weights instead (or the `penaltyBits` override). */
  readonly penalty?: number;
  /** Probabilistic only: fixed LLR override in log2 units. When set, the NE
   *  dimension skips EM and contributes -abs(penaltyBits) when fired, else 0.
   *  Absent => the weight is EM-learned. Rejected on weighted/exact matchkeys
   *  (they use `penalty`). Unconstrained range, matching Python. */
  readonly penaltyBits?: number;
  // NOTE: Python's `derive_from` (synthesized-column NE) is deliberately NOT
  // on this type. goldenmatch-js has no derived-column materialization, so a
  // loaded derive_from NE would silently never fire — the loader rejects it
  // at parse time instead (see parseNegativeEvidenceField).
}

export interface ExactMatchkey {
  readonly name: string;
  readonly type: "exact";
  readonly fields: readonly MatchkeyField[];
  /** v1.12 Path Y: post-filter exact pairs via negative evidence. */
  readonly negativeEvidence?: readonly NegativeEvidenceField[];
  /** v1.12: when NE is set on an exact matchkey, threshold defaults to 0.5
   *  in ``promoteNegativeEvidence`` so the score-and-threshold path activates. */
  readonly threshold?: number;
}

export interface WeightedMatchkey {
  readonly name: string;
  readonly type: "weighted";
  readonly fields: readonly MatchkeyField[];
  readonly threshold: number;
  readonly autoThreshold?: boolean;
  readonly rerank?: boolean;
  readonly rerankModel?: string;
  readonly rerankBand?: number;
  /** v1.11: negative evidence fields — subtract penalty on disagreement. */
  readonly negativeEvidence?: readonly NegativeEvidenceField[];
}

export interface ProbabilisticMatchkey {
  readonly name: string;
  readonly type: "probabilistic";
  readonly fields: readonly MatchkeyField[];
  readonly threshold?: number;
  readonly emIterations?: number;
  readonly convergenceThreshold?: number;
  readonly linkThreshold?: number;
  readonly reviewThreshold?: number;
  /** FS negative evidence — trained and scored on the FS discrete path
   *  (ported from Python #1764): EM-learned NE weights per field, or a
   *  fixed `penaltyBits` override. Training/scoring/validation/fallback all
   *  honor NE; only the continuous (Winkler) path throws
   *  `NegativeEvidenceUnsupportedError`, permanently, matching Python. */
  readonly negativeEvidence?: readonly NegativeEvidenceField[];
  /**
   * Persisted EM model path (Splink-style train-once -> reuse). Mirrors
   * Python `MatchkeyConfig.model_path`: when set and the file exists, the
   * trained EMResult is loaded and EM training is skipped; when set and
   * absent, EM runs and the result is saved there. TS scoring/EM does not
   * currently consume this itself — it is a pass-through/round-trip field so
   * a Splink-imported trained model's `model_path` survives the TS config
   * layer for the Python runtime (or a future TS consumer) to honor.
   */
  readonly modelPath?: string;
}

export type MatchkeyConfig =
  | ExactMatchkey
  | WeightedMatchkey
  | ProbabilisticMatchkey;

// ---------------------------------------------------------------------------
// Blocking config
// ---------------------------------------------------------------------------

export interface BlockingKeyConfig {
  readonly fields: readonly string[];
  readonly transforms: readonly string[];
  /**
   * Per-field transform chains (#1826/#1832). A field listed here derives its
   * block-key component with its OWN chain; fields absent here fall back to the
   * key-level `transforms`. Mirrors Python `BlockingKeyConfig.field_transforms`,
   * so a Python-written config with per-field blocking transforms produces the
   * same block membership in the TS runtime instead of silently ignoring them.
   */
  readonly fieldTransforms?: Readonly<Record<string, readonly string[]>>;
}

export interface SortKeyField {
  readonly column: string;
  readonly transforms: readonly string[];
}

export interface CanopyConfig {
  readonly fields: readonly string[];
  readonly looseThreshold: number;
  readonly tightThreshold: number;
  readonly maxCanopySize: number;
}

export interface BlockingConfig {
  readonly strategy:
    | "static"
    | "adaptive"
    | "sorted_neighborhood"
    | "multi_pass"
    | "ann"
    | "canopy"
    | "ann_pairs"
    | "learned";
  readonly keys: readonly BlockingKeyConfig[];
  readonly maxBlockSize: number;
  readonly skipOversized: boolean;
  readonly autoSuggest?: boolean;
  readonly autoSelect?: boolean;
  readonly subBlockKeys?: readonly BlockingKeyConfig[];
  readonly windowSize?: number;
  readonly sortKey?: readonly SortKeyField[];
  readonly passes?: readonly BlockingKeyConfig[];
  readonly unionMode?: boolean;
  readonly maxTotalComparisons?: number;
  readonly annColumn?: string;
  readonly annModel?: string;
  readonly annTopK?: number;
  readonly canopy?: CanopyConfig;
  readonly learnedSampleSize?: number;
  readonly learnedMinRecall?: number;
  readonly learnedMinReduction?: number;
  readonly learnedPredicateDepth?: number;
  readonly learnedCachePath?: string;
}

// ---------------------------------------------------------------------------
// Golden rules config
// ---------------------------------------------------------------------------

export interface GoldenFieldRule {
  readonly strategy:
    | "most_complete"
    | "majority_vote"
    | "source_priority"
    | "most_recent"
    | "first_non_null";
  readonly dateColumn?: string;
  readonly sourcePriority?: readonly string[];
}

export interface GoldenRulesConfig {
  readonly defaultStrategy: string;
  readonly fieldRules: Readonly<Record<string, GoldenFieldRule>>;
  readonly maxClusterSize: number;
  readonly autoSplit: boolean;
  readonly qualityWeighting: boolean;
  readonly weakClusterThreshold: number;
}

// ---------------------------------------------------------------------------
// Standardization, validation, quality, transform
// ---------------------------------------------------------------------------

export interface StandardizationConfig {
  readonly rules: Readonly<Record<string, readonly string[]>>;
}

export interface ValidationRuleConfig {
  readonly column: string;
  readonly ruleType:
    | "regex"
    | "min_length"
    | "max_length"
    | "not_null"
    | "in_set"
    | "format";
  readonly params: Readonly<Record<string, unknown>>;
  readonly action: "null" | "quarantine" | "flag";
}

export interface ValidationConfig {
  readonly rules: readonly ValidationRuleConfig[];
  readonly autoFix: boolean;
}

export interface QualityConfig {
  readonly enabled: boolean;
  readonly mode: "silent" | "announced" | "disabled";
  readonly fixMode: "safe" | "moderate" | "none";
  readonly domain?: string;
}

export interface TransformConfig {
  readonly enabled: boolean;
  readonly mode: "silent" | "announced" | "disabled";
}

// ---------------------------------------------------------------------------
// LLM scorer & budget
// ---------------------------------------------------------------------------

export interface BudgetConfig {
  readonly maxCostUsd?: number;
  readonly maxCalls?: number;
  readonly escalationModel?: string;
  readonly escalationBand?: readonly number[];
  readonly escalationBudgetPct?: number;
  readonly warnAtPct?: number;
}

export interface LLMScorerConfig {
  readonly enabled: boolean;
  readonly provider?: string;
  readonly model?: string;
  readonly autoThreshold: number;
  readonly candidateLo: number;
  readonly candidateHi: number;
  readonly batchSize: number;
  readonly maxWorkers: number;
  readonly budget?: BudgetConfig;
  readonly mode: "pairwise" | "cluster";
  readonly clusterMaxSize?: number;
  readonly clusterMinSize?: number;
}

// ---------------------------------------------------------------------------
// Domain config
// ---------------------------------------------------------------------------

export interface DomainConfig {
  readonly enabled: boolean;
  readonly mode?: string;
  readonly confidenceThreshold: number;
  readonly llmValidation: boolean;
  readonly budget?: BudgetConfig;
}

// ---------------------------------------------------------------------------
// Memory & learning
// ---------------------------------------------------------------------------

export interface LearningConfig {
  readonly thresholdMinCorrections: number;
  readonly weightsMinCorrections: number;
}

export interface MemoryConfig {
  readonly enabled: boolean;
  readonly backend: "memory" | "sqlite";
  readonly path?: string;
  readonly dataset?: string | null;
  readonly reanchor?: boolean;
  readonly trust?: { human: number; agent: number };
  readonly learning: LearningConfig;
}

// ---------------------------------------------------------------------------
// Input & output config
// ---------------------------------------------------------------------------

export interface InputFileConfig {
  readonly path: string;
  readonly idColumn?: string;
  readonly sourceLabel?: string;
  readonly sourceName?: string;
  readonly columnMap?: Readonly<Record<string, string>>;
  readonly delimiter?: string;
  readonly encoding?: string;
  readonly sheet?: string;
  readonly parseMode?: string;
  readonly headerRow?: number;
  readonly hasHeader?: boolean;
  readonly skipRows?: readonly number[];
}

export interface InputConfig {
  readonly files: readonly InputFileConfig[];
  readonly fileA?: InputFileConfig;
  readonly fileB?: InputFileConfig;
}

export interface OutputConfig {
  readonly path?: string;
  readonly format?: string;
  readonly directory?: string;
  readonly runName?: string;
}

// ---------------------------------------------------------------------------
// Top-level config
// ---------------------------------------------------------------------------

export interface GoldenMatchConfig {
  readonly matchkeys?: readonly MatchkeyConfig[];
  readonly matchSettings?: readonly MatchkeyConfig[];
  readonly blocking?: BlockingConfig;
  readonly threshold?: number;
  readonly goldenRules?: GoldenRulesConfig;
  readonly standardization?: StandardizationConfig;
  readonly validation?: ValidationConfig;
  readonly quality?: QualityConfig;
  readonly transform?: TransformConfig;
  readonly llmScorer?: LLMScorerConfig;
  readonly domain?: DomainConfig;
  readonly memory?: MemoryConfig;
  readonly input?: InputConfig;
  readonly output?: OutputConfig;
  readonly backend?: string;
  readonly llmAuto?: boolean;
  readonly llmBoost?: boolean;

  /** Internal: auto-config hand-off. Do not read from outside the library.
   *  Non-readonly so preflight / postflight can populate. Stripped by
   *  stripConventionPrivate before YAML/JSON export.
   *
   *  This list is CLOSED. Future internal state should use a side-table
   *  pattern (WeakMap) instead — see spec §11 risks. Adding more underscore
   *  fields here weakens the readonly contract for every consumer. */
  _preflightReport?: PreflightReport;
  _strictAutoconfig?: boolean;
  _domainProfile?: import("./domain.js").DomainProfile;
}

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

export interface ScoredPair {
  readonly idA: number;
  readonly idB: number;
  readonly score: number;
}

export interface ClusterInfo {
  readonly members: readonly number[];
  readonly size: number;
  readonly oversized: boolean;
  readonly pairScores: ReadonlyMap<PairKey, number>;
  readonly confidence: number;
  readonly bottleneckPair: readonly [number, number] | null;
  readonly clusterQuality: "strong" | "weak" | "split";
}

export interface DedupeStats {
  readonly totalRecords: number;
  readonly totalClusters: number;
  readonly matchRate: number;
  readonly matchedRecords: number;
  readonly uniqueRecords: number;
}

/**
 * Wire shape of a single config suggestion as it rides on {@link DedupeResult}.
 * Mirrors the Python healer's `serialize_suggestions` output
 * (`{id, kind, target, rationale, verified, patch}`) — the user-facing subset
 * of the kernel's full `Suggestion`. `verified` is caller-supplied (true on the
 * `suggest`/`heal` paths, false on the free default-pipeline hint).
 */
export interface SerializedSuggestion {
  readonly id: string;
  readonly kind: string;
  readonly target: string;
  readonly rationale: string;
  readonly verified: boolean;
  readonly patch: Readonly<Record<string, unknown>>;
}

export interface DedupeResult {
  readonly goldenRecords: readonly Row[];
  readonly clusters: ReadonlyMap<number, ClusterInfo>;
  readonly dupes: readonly Row[];
  readonly unique: readonly Row[];
  readonly stats: DedupeStats;
  readonly scoredPairs: readonly ScoredPair[];
  /** Probabilistic pairs in [reviewThreshold, linkThreshold); never clustered. */
  readonly reviewCandidates?: readonly ScoredPair[];
  readonly config: GoldenMatchConfig;
  readonly postflightReport?: PostflightReport;
  /** Learning Memory outcome for this run. Null when memory was disabled
   *  or no corrections existed. Set by `_applyMemoryPost` in pipeline.ts. */
  readonly memoryStats?: CorrectionStats | null;
  /** Config suggestions surfaced by the healer. Always `[]` unless the opt-in
   *  wasm backend is registered AND (for the default path) the free trigger
   *  fires; populated by `dedupe()`'s advisory block. Default `[]`. */
  readonly suggestions: readonly SerializedSuggestion[];
  /** Applied heal trail (one entry per accepted step), set only on the
   *  `dedupe({ heal: true })` path; undefined otherwise. */
  readonly healTrail?: readonly SerializedSuggestion[];
}

export interface MatchResult {
  readonly matched: readonly Row[];
  readonly unmatched: readonly Row[];
  readonly stats: Readonly<Record<string, unknown>>;
  readonly reviewCandidates?: readonly ScoredPair[];
  readonly postflightReport?: PostflightReport;
  /** Learning Memory outcome for this run. See `DedupeResult.memoryStats`. */
  readonly memoryStats?: CorrectionStats | null;
}

export interface FieldProvenance {
  readonly value: unknown;
  readonly sourceRowId: number;
  readonly strategy: string;
  readonly confidence: number;
  readonly candidates: readonly Readonly<Record<string, unknown>>[];
}

export interface ClusterProvenance {
  readonly clusterId: number;
  readonly clusterQuality: string;
  readonly clusterConfidence: number;
  readonly fields: Readonly<Record<string, FieldProvenance>>;
}

export interface BlockResult {
  readonly blockKey: string;
  readonly rows: readonly Row[];
  readonly strategy: string;
  readonly depth: number;
  readonly parentKey?: string;
  readonly preScoredPairs?: readonly ScoredPair[];
}

// ---------------------------------------------------------------------------
// Valid enum sets
// ---------------------------------------------------------------------------

export const VALID_SCORERS = new Set([
  "exact",
  "jaro_winkler",
  "levenshtein",
  "date",
  "token_sort",
  "soundex_match",
  "embedding",
  "record_embedding",
  "ensemble",
  "dice",
  "jaccard",
  "qgram",
  "phash",
  "radial",
  "audio_fp",
  "initialism_match",
  "alias_match",
  "given_name_aliased_jw",
  "name_freq_weighted_jw",
] as const);

export const VALID_TRANSFORMS = new Set([
  "lowercase",
  "uppercase",
  "strip",
  "strip_all",
  "soundex",
  "metaphone",
  "digits_only",
  "alpha_only",
  "normalize_whitespace",
  "token_sort",
  "first_token",
  "last_token",
  "strip_honorifics",
] as const);

export const VALID_STRATEGIES = new Set([
  "most_recent",
  "source_priority",
  "most_complete",
  "majority_vote",
  "first_non_null",
] as const);

export const VALID_STANDARDIZERS = new Set([
  "email",
  "name_proper",
  "name_upper",
  "name_lower",
  "phone",
  "zip5",
  "address",
  "state",
  "strip",
  "trim_whitespace",
] as const);

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

/**
 * Create a ScoredPair guaranteeing idA <= idB (canonical order).
 * Always use this instead of constructing `{ idA, idB, score }` directly.
 */
export function makeScoredPair(
  a: number,
  b: number,
  score: number,
): ScoredPair {
  const lo = a < b ? a : b;
  const hi = a < b ? b : a;
  return { idA: lo, idB: hi, score };
}

/** Create a MatchkeyField with sensible defaults. */
export function makeMatchkeyField(
  partial: Partial<MatchkeyField> & Pick<MatchkeyField, "field">,
): MatchkeyField {
  return {
    transforms: [],
    scorer: "jaro_winkler",
    weight: 1.0,
    ...partial,
  };
}

/**
 * Create a NegativeEvidenceField with v1.11 defaults.
 * Mirrors the Python ``NegativeEvidenceField`` Pydantic constructor.
 * - ``threshold`` default 0.5 (per spec; eager-promote rule uses 0.4)
 * - ``penalty`` default 0.5 (per spec; eager-promote rule uses 0.3)
 *
 * Produces the weighted/exact NE shape (penalty set). Probabilistic NE
 * (EM-learned or ``penaltyBits``) should be constructed literally without
 * ``penalty`` — the loader validation matrix rejects ``penalty`` there.
 */
export function makeNegativeEvidenceField(
  partial: Partial<NegativeEvidenceField> & Pick<NegativeEvidenceField, "field" | "scorer">,
): NegativeEvidenceField {
  return {
    transforms: [],
    threshold: 0.5,
    penalty: 0.5,
    ...partial,
  };
}

/**
 * Shape accepted by `makeMatchkeyConfig`. All variant-specific fields are
 * optional; the factory picks the right variant based on `type`.
 */
export interface MakeMatchkeyConfigInput {
  readonly name: string;
  readonly type?: "exact" | "weighted" | "probabilistic";
  readonly fields?: readonly MatchkeyField[];
  readonly threshold?: number;
  readonly autoThreshold?: boolean;
  readonly rerank?: boolean;
  readonly rerankModel?: string;
  readonly rerankBand?: number;
  readonly emIterations?: number;
  readonly convergenceThreshold?: number;
  readonly linkThreshold?: number;
  readonly reviewThreshold?: number;
  /** v1.11: negative evidence (round-trips through the factory). */
  readonly negativeEvidence?: readonly NegativeEvidenceField[];
  /** Persisted EM model path (probabilistic-only; round-trips through the factory). */
  readonly modelPath?: string;
}

/** Create a MatchkeyConfig with sensible defaults. Produces the correct variant. */
export function makeMatchkeyConfig(
  partial: MakeMatchkeyConfigInput,
): MatchkeyConfig {
  const type = partial.type ?? "weighted";
  const fields = partial.fields ?? [];
  if (type === "exact") {
    const out: ExactMatchkey = {
      name: partial.name,
      type: "exact",
      fields,
      ...(partial.negativeEvidence !== undefined
        ? { negativeEvidence: partial.negativeEvidence }
        : {}),
      ...(partial.threshold !== undefined
        ? { threshold: partial.threshold }
        : {}),
    };
    return out;
  }
  if (type === "probabilistic") {
    const out: ProbabilisticMatchkey = {
      name: partial.name,
      type: "probabilistic",
      fields,
      ...(partial.threshold !== undefined
        ? { threshold: partial.threshold }
        : {}),
      ...(partial.emIterations !== undefined
        ? { emIterations: partial.emIterations }
        : {}),
      ...(partial.convergenceThreshold !== undefined
        ? { convergenceThreshold: partial.convergenceThreshold }
        : {}),
      ...(partial.linkThreshold !== undefined
        ? { linkThreshold: partial.linkThreshold }
        : {}),
      ...(partial.reviewThreshold !== undefined
        ? { reviewThreshold: partial.reviewThreshold }
        : {}),
      ...(partial.negativeEvidence !== undefined
        ? { negativeEvidence: partial.negativeEvidence }
        : {}),
      ...(partial.modelPath !== undefined ? { modelPath: partial.modelPath } : {}),
    };
    return out;
  }
  // weighted (default)
  const out: WeightedMatchkey = {
    name: partial.name,
    type: "weighted",
    fields,
    threshold: partial.threshold ?? 0.85,
    ...(partial.autoThreshold !== undefined
      ? { autoThreshold: partial.autoThreshold }
      : {}),
    ...(partial.rerank !== undefined ? { rerank: partial.rerank } : {}),
    ...(partial.rerankModel !== undefined
      ? { rerankModel: partial.rerankModel }
      : {}),
    ...(partial.rerankBand !== undefined
      ? { rerankBand: partial.rerankBand }
      : {}),
    ...(partial.negativeEvidence !== undefined
      ? { negativeEvidence: partial.negativeEvidence }
      : {}),
  };
  return out;
}

/** Create a BlockingConfig with sensible defaults. */
export function makeBlockingConfig(
  partial?: Partial<BlockingConfig>,
): BlockingConfig {
  return {
    strategy: "static",
    keys: [],
    maxBlockSize: 5000,
    skipOversized: false,
    ...partial,
  };
}

/** Create a GoldenRulesConfig with sensible defaults. */
export function makeGoldenRulesConfig(
  partial?: Partial<GoldenRulesConfig>,
): GoldenRulesConfig {
  return {
    defaultStrategy: "most_complete",
    fieldRules: {},
    maxClusterSize: 10,
    autoSplit: true,
    qualityWeighting: true,
    weakClusterThreshold: 0.3,
    ...partial,
  };
}

/** Create a full GoldenMatchConfig with sensible defaults. */
export function makeConfig(
  partial?: Partial<GoldenMatchConfig>,
): GoldenMatchConfig {
  return {
    threshold: 0.85,
    blocking: makeBlockingConfig(partial?.blocking),
    goldenRules: makeGoldenRulesConfig(partial?.goldenRules),
    ...partial,
    // Re-apply blocking/goldenRules after spread so partial overrides win
    ...(partial?.blocking !== undefined
      ? { blocking: makeBlockingConfig(partial.blocking) }
      : {}),
    ...(partial?.goldenRules !== undefined
      ? { goldenRules: makeGoldenRulesConfig(partial.goldenRules) }
      : {}),
  };
}

/**
 * Return matchkeys from config, checking both `matchkeys` and `matchSettings`.
 * Mirrors Python's `GoldenMatchConfig.get_matchkeys()`.
 */
export function getMatchkeys(
  config: GoldenMatchConfig,
): readonly MatchkeyConfig[] {
  return config.matchkeys ?? config.matchSettings ?? [];
}
