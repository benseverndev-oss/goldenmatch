/**
 * complexityProfile.ts — Stage-specific complexity sub-profiles + rollup.
 *
 * TS port of Python ``goldenmatch/core/complexity_profile.py`` (v1.7+v1.8).
 * Emitted by the auto-config controller and consumed by the refit policy /
 * rule table.
 *
 * Edge-safe: no `node:` imports.
 */

import type { Row } from "./types.js";

// ---------------------------------------------------------------------------
// Enums (Python parity: string-valued enums)
// ---------------------------------------------------------------------------

/** Health rollup. Matches Python ``HealthVerdict`` string values. */
export const HealthVerdict = {
  GREEN: "green",
  YELLOW: "yellow",
  RED: "red",
} as const;
export type HealthVerdict = (typeof HealthVerdict)[keyof typeof HealthVerdict];

/** Why the controller stopped iterating. Matches Python ``StopReason``. */
export const StopReason = {
  GREEN: "green",
  CONVERGED: "converged",
  BUDGET_ITERATIONS: "budget_iterations",
  BUDGET_TIME: "budget_time",
  POLICY_SATISFIED: "policy_satisfied",
  POLICY_NO_PROGRESS: "policy_no_progress",
  OSCILLATING: "oscillating",
  CANCELLED: "cancelled",
} as const;
export type StopReason = (typeof StopReason)[keyof typeof StopReason];

/** Python ``ColumnType`` literal. */
export type ColumnType =
  | "text"
  | "numeric"
  | "id-like"
  | "date"
  | "geo"
  | "phone"
  | "email"
  | "name"
  | "unknown";

// ---------------------------------------------------------------------------
// Indicator-related types (Wave 1 keeps only Wave-2/3 field shapes; populated
// lazily by Wave-2 indicator context).
// ---------------------------------------------------------------------------

export interface ColumnPrior {
  readonly identityScore: number;
  readonly corruptionScore: number;
}

export interface SparsityVerdict {
  readonly isSparse: boolean;
  readonly estimatedNTruePairs: number;
}

export interface CollisionSignal {
  readonly rate: number;
  readonly witnessUsed: string;
}

export interface IndicatorsProfile {
  readonly fullPopMatchkeyHitRate?: number;
  readonly crossBlockingOverlap?: number;
}

// ---------------------------------------------------------------------------
// Sub-profiles — interfaces + factory functions
// ---------------------------------------------------------------------------

export interface DataProfile {
  readonly nRows: number;
  readonly nCols: number;
  readonly columnTypes: Readonly<Record<string, ColumnType>>;
  readonly cardinalityRatio: Readonly<Record<string, number>>;
  readonly nullRate: Readonly<Record<string, number>>;
  readonly valueLengthP50: Readonly<Record<string, number>>;
  readonly valueLengthP99: Readonly<Record<string, number>>;
  readonly columnPriors?: Readonly<Record<string, ColumnPrior>>;
}

export function makeDataProfile(p: Partial<DataProfile> = {}): DataProfile {
  return {
    nRows: p.nRows ?? 0,
    nCols: p.nCols ?? 0,
    columnTypes: p.columnTypes ?? {},
    cardinalityRatio: p.cardinalityRatio ?? {},
    nullRate: p.nullRate ?? {},
    valueLengthP50: p.valueLengthP50 ?? {},
    valueLengthP99: p.valueLengthP99 ?? {},
    ...(p.columnPriors !== undefined ? { columnPriors: p.columnPriors } : {}),
  };
}

export function dataHealth(dp: DataProfile): HealthVerdict {
  if (dp.nRows === 0) return HealthVerdict.RED;
  const distinctTypes = new Set(Object.values(dp.columnTypes));
  if (dp.nCols === 1 || distinctTypes.size === 1) return HealthVerdict.YELLOW;
  return HealthVerdict.GREEN;
}

export interface DomainProfile {
  readonly detectedDomain?: string;
  readonly confidence: number;
  readonly derivedColumns: readonly string[];
  readonly lowConfidenceRowCount: number;
}

export function makeDomainProfile(p: Partial<DomainProfile> = {}): DomainProfile {
  return {
    ...(p.detectedDomain !== undefined ? { detectedDomain: p.detectedDomain } : {}),
    confidence: p.confidence ?? 0.0,
    derivedColumns: p.derivedColumns ?? [],
    lowConfidenceRowCount: p.lowConfidenceRowCount ?? 0,
  };
}

export function domainHealth(d: DomainProfile): HealthVerdict {
  if (d.confidence < 0.3 && d.derivedColumns.length > 0) return HealthVerdict.YELLOW;
  return HealthVerdict.GREEN;
}

export interface FieldStats {
  readonly postTransformCardinalityRatio: number;
  readonly postTransformNullRate: number;
  readonly postTransformValueLengthP50: number;
}

export interface MatchkeyProfile {
  readonly perField: Readonly<Record<string, FieldStats>>;
}

export function makeMatchkeyProfile(p: Partial<MatchkeyProfile> = {}): MatchkeyProfile {
  return { perField: p.perField ?? {} };
}

export function matchkeyHealth(mk: MatchkeyProfile): HealthVerdict {
  const verdicts: HealthVerdict[] = [];
  for (const fs of Object.values(mk.perField)) {
    if (fs.postTransformCardinalityRatio === 0.0) verdicts.push(HealthVerdict.RED);
    else if (fs.postTransformCardinalityRatio > 0.95) verdicts.push(HealthVerdict.YELLOW);
  }
  return maxSeverity(...verdicts);
}

export interface BlockingProfile {
  readonly keysUsed: readonly (readonly string[])[];
  readonly nBlocks: number;
  readonly totalComparisons: number;
  readonly reductionRatio: number;
  readonly blockSizesP50: number;
  readonly blockSizesP95: number;
  readonly blockSizesP99: number;
  readonly blockSizesMax: number;
  readonly singletonBlockCount: number;
  readonly oversizedBlockCount: number;
}

export function makeBlockingProfile(p: Partial<BlockingProfile> = {}): BlockingProfile {
  return {
    keysUsed: p.keysUsed ?? [],
    nBlocks: p.nBlocks ?? 0,
    totalComparisons: p.totalComparisons ?? 0,
    reductionRatio: p.reductionRatio ?? 0.0,
    blockSizesP50: p.blockSizesP50 ?? 0,
    blockSizesP95: p.blockSizesP95 ?? 0,
    blockSizesP99: p.blockSizesP99 ?? 0,
    blockSizesMax: p.blockSizesMax ?? 0,
    singletonBlockCount: p.singletonBlockCount ?? 0,
    oversizedBlockCount: p.oversizedBlockCount ?? 0,
  };
}

export function blockingHealth(b: BlockingProfile, nRows: number): HealthVerdict {
  if (b.nBlocks === 0) return HealthVerdict.RED;
  const avg = nRows / Math.max(b.nBlocks, 1);
  if (b.blockSizesP99 > 10 * avg) return HealthVerdict.RED;
  if (b.reductionRatio < 0.5) return HealthVerdict.RED;
  if (b.singletonBlockCount / b.nBlocks > 0.5) return HealthVerdict.YELLOW;
  return HealthVerdict.GREEN;
}

export interface ScoringProfile {
  readonly nPairsScored: number;
  readonly candidatesCompared: number;
  readonly scoreHistogram: readonly number[];
  readonly dipStatistic: number;
  readonly massAboveThreshold: number;
  readonly massInBorderline: number;
  readonly perFieldScoreVariance: Readonly<Record<string, number>>;
  readonly randomPairAboveThresholdRate?: number;
}

export function makeScoringProfile(p: Partial<ScoringProfile> = {}): ScoringProfile {
  return {
    nPairsScored: p.nPairsScored ?? 0,
    candidatesCompared: p.candidatesCompared ?? 0,
    scoreHistogram: p.scoreHistogram ?? new Array(20).fill(0),
    dipStatistic: p.dipStatistic ?? 0.0,
    massAboveThreshold: p.massAboveThreshold ?? 0.0,
    massInBorderline: p.massInBorderline ?? 0.0,
    perFieldScoreVariance: p.perFieldScoreVariance ?? {},
    ...(p.randomPairAboveThresholdRate !== undefined
      ? { randomPairAboveThresholdRate: p.randomPairAboveThresholdRate }
      : {}),
  };
}

export function scoringHealth(s: ScoringProfile): HealthVerdict {
  if (s.candidatesCompared === 0 && s.nPairsScored === 0) return HealthVerdict.RED;
  if (s.massAboveThreshold === 0.0 && s.candidatesCompared > 0) return HealthVerdict.RED;
  if (s.massAboveThreshold === 0.0) return HealthVerdict.RED;
  if (s.dipStatistic < 0.005 && s.nPairsScored > 0) return HealthVerdict.RED;
  if (s.massInBorderline > 0.3) return HealthVerdict.YELLOW;
  return HealthVerdict.GREEN;
}

export interface ClusterProfile {
  readonly nClusters: number;
  readonly clusterSizeP50: number;
  readonly clusterSizeP99: number;
  readonly clusterSizeMax: number;
  readonly transitivityRate: number;
  readonly edgeConfidenceP50: number;
  readonly edgeConfidenceMin: number;
  readonly oversizedClusterCount: number;
}

export function makeClusterProfile(p: Partial<ClusterProfile> = {}): ClusterProfile {
  return {
    nClusters: p.nClusters ?? 0,
    clusterSizeP50: p.clusterSizeP50 ?? 0,
    clusterSizeP99: p.clusterSizeP99 ?? 0,
    clusterSizeMax: p.clusterSizeMax ?? 0,
    transitivityRate: p.transitivityRate ?? 1.0,
    edgeConfidenceP50: p.edgeConfidenceP50 ?? 0.0,
    edgeConfidenceMin: p.edgeConfidenceMin ?? 0.0,
    oversizedClusterCount: p.oversizedClusterCount ?? 0,
  };
}

export function clusterHealth(c: ClusterProfile, nRows: number): HealthVerdict {
  if (nRows > 0 && c.clusterSizeMax > 0.1 * nRows) return HealthVerdict.RED;
  if (c.transitivityRate < 0.85) return HealthVerdict.RED;
  if (c.oversizedClusterCount > 0) return HealthVerdict.YELLOW;
  return HealthVerdict.GREEN;
}

export interface ProfileMeta {
  readonly iteration: number;
  readonly isSample: boolean;
  readonly sampleSize: number;
  readonly nRowsFull: number;
  readonly wallClockMs: number;
  readonly seed: number;
}

export function makeProfileMeta(p: Partial<ProfileMeta> = {}): ProfileMeta {
  return {
    iteration: p.iteration ?? 0,
    isSample: p.isSample ?? true,
    sampleSize: p.sampleSize ?? 0,
    nRowsFull: p.nRowsFull ?? 0,
    wallClockMs: p.wallClockMs ?? 0,
    seed: p.seed ?? 0,
  };
}

// ---------------------------------------------------------------------------
// Top-level ComplexityProfile
// ---------------------------------------------------------------------------

export interface ComplexityProfile {
  readonly data: DataProfile;
  readonly domain: DomainProfile;
  readonly matchkey: MatchkeyProfile;
  readonly blocking: BlockingProfile;
  readonly scoring: ScoringProfile;
  readonly cluster: ClusterProfile;
  readonly meta: ProfileMeta;
  readonly indicators?: IndicatorsProfile;
}

export function makeComplexityProfile(p: Partial<ComplexityProfile> = {}): ComplexityProfile {
  return {
    data: p.data ?? makeDataProfile(),
    domain: p.domain ?? makeDomainProfile(),
    matchkey: p.matchkey ?? makeMatchkeyProfile(),
    blocking: p.blocking ?? makeBlockingProfile(),
    scoring: p.scoring ?? makeScoringProfile(),
    cluster: p.cluster ?? makeClusterProfile(),
    meta: p.meta ?? makeProfileMeta(),
    ...(p.indicators !== undefined ? { indicators: p.indicators } : {}),
  };
}

export function complexityHealth(p: ComplexityProfile): HealthVerdict {
  return maxSeverity(
    dataHealth(p.data),
    domainHealth(p.domain),
    matchkeyHealth(p.matchkey),
    blockingHealth(p.blocking, p.data.nRows),
    scoringHealth(p.scoring),
    clusterHealth(p.cluster, p.data.nRows),
  );
}

/** L1-distance vector for convergence detection. 8 normalized signals. */
export function normalizedSignalVector(p: ComplexityProfile): readonly number[] {
  return [
    Math.min(p.blocking.reductionRatio, 1.0),
    Math.min(p.blocking.blockSizesP99 / Math.max(p.data.nRows, 1), 1.0),
    Math.min(p.scoring.dipStatistic / 0.1, 1.0),
    p.scoring.massAboveThreshold,
    p.scoring.massInBorderline,
    p.cluster.transitivityRate,
    Math.min(p.cluster.clusterSizeMax / Math.max(p.data.nRows, 1), 1.0),
    p.cluster.nClusters > 0 ? 1.0 : 0.0,
  ];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function maxSeverity(...verdicts: HealthVerdict[]): HealthVerdict {
  if (verdicts.includes(HealthVerdict.RED)) return HealthVerdict.RED;
  if (verdicts.includes(HealthVerdict.YELLOW)) return HealthVerdict.YELLOW;
  return HealthVerdict.GREEN;
}

// ---------------------------------------------------------------------------
// Compute a DataProfile from a row array (TS-only utility — Python computes
// this from a polars DataFrame; we mirror the calculation on plain rows so
// the controller can populate a real DataProfile pre-loop).
// ---------------------------------------------------------------------------

/**
 * Build a Python-shape ``DataProfile`` from plain row dicts.
 *
 * Skips columns whose name starts with ``__`` (internal/derived).
 * Column-type inference is a simplified mirror of
 * ``AutoConfigController._compute_data_profile``:
 * looks at JS typeof on the first non-null value (string/number/Date)
 * and classifies as ``text``/``numeric``/``date``/``unknown``.
 */
export function computeDataProfile(rows: readonly Row[]): DataProfile {
  if (rows.length === 0) {
    return makeDataProfile();
  }
  const firstRow = rows[0] as Record<string, unknown>;
  const userCols = Object.keys(firstRow).filter((c) => !c.startsWith("__"));
  const nRows = rows.length;

  const columnTypes: Record<string, ColumnType> = {};
  const cardinalityRatio: Record<string, number> = {};
  const nullRate: Record<string, number> = {};
  const valueLengthP50: Record<string, number> = {};
  const valueLengthP99: Record<string, number> = {};

  for (const col of userCols) {
    const distinct = new Set<unknown>();
    let nonNullCount = 0;
    const textLengths: number[] = [];
    let sampleNonNull: unknown = null;
    for (const r of rows) {
      const v = (r as Record<string, unknown>)[col];
      if (v === null || v === undefined || v === "") continue;
      nonNullCount += 1;
      distinct.add(v);
      if (sampleNonNull === null) sampleNonNull = v;
      if (typeof v === "string") textLengths.push(v.length);
    }
    cardinalityRatio[col] = nonNullCount > 0 ? distinct.size / nonNullCount : 0.0;
    nullRate[col] = nRows > 0 ? 1 - nonNullCount / nRows : 0.0;

    let kind: ColumnType = "unknown";
    if (sampleNonNull !== null) {
      if (typeof sampleNonNull === "string") kind = "text";
      else if (typeof sampleNonNull === "number") kind = "numeric";
      else if (sampleNonNull instanceof Date) kind = "date";
    }
    columnTypes[col] = kind;

    if (kind === "text" && textLengths.length > 0) {
      textLengths.sort((a, b) => a - b);
      const p50Idx = Math.floor(textLengths.length / 2);
      const p99Idx = Math.max(0, Math.floor(0.99 * textLengths.length) - 1);
      valueLengthP50[col] = textLengths[p50Idx] ?? 0;
      valueLengthP99[col] = textLengths[p99Idx] ?? 0;
    }
  }

  return makeDataProfile({
    nRows,
    nCols: userCols.length,
    columnTypes,
    cardinalityRatio,
    nullRate,
    valueLengthP50,
    valueLengthP99,
  });
}
