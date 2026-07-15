/**
 * probabilistic.ts — Fellegi-Sunter probabilistic matching with EM-trained
 * parameters. Ports `goldenmatch/core/probabilistic.py` (discrete path).
 *
 * Implements:
 * - Comparison vectors (2/3/N-level field agreements)
 * - Splink-style EM: u estimated from random pairs (fixed), m trained via EM
 * - Blocking fields get fixed neutral priors
 * - Match weights as log2(m/u) log-likelihood ratios, normalized to [0,1]
 *
 * Edge-safe: no `node:` imports, no numpy. Uses typed arrays where helpful.
 */

import type {
  Row,
  MatchkeyConfig,
  MatchkeyField,
  NegativeEvidenceField,
  ScoredPair,
} from "./types.js";
import { makeScoredPair } from "./types.js";
import { scoreField, asString } from "./scorer.js";
import { applyTransforms } from "./transforms.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EMOptions {
  readonly maxIterations?: number;
  readonly convergence?: number;
  readonly blockingFields?: readonly string[];
  readonly seed?: number;
  readonly nSamplePairs?: number;
}

export interface EMResult {
  /** P(level | match) per field. */
  readonly m: Readonly<Record<string, readonly number[]>>;
  /** P(level | non-match) per field. */
  readonly u: Readonly<Record<string, readonly number[]>>;
  /** log2(m / u) per level per field. Score weights. */
  readonly matchWeights: Readonly<Record<string, readonly number[]>>;
  /** Estimated p(match) in the sampled population. */
  readonly proportionMatched: number;
  readonly iterations: number;
  readonly converged: boolean;
  /** Term-frequency (Winkler) adjustment data, populated only for fields with
   * tf_adjustment=True on the Python side. TS scoring does not consume these;
   * they are PRESERVED through JSON round-trips (emResultToJson/emResultFromJson)
   * so a Python-trained model file is never corrupted by a TS re-save.
   * field -> {transformed_value -> relative frequency}. */
  readonly tfFreqs?: Readonly<Record<string, Readonly<Record<string, number>>>> | null;
  /** field -> sum(freq(v)^2) (expected exact-match collision rate baseline). */
  readonly tfCollision?: Readonly<Record<string, number>> | null;
}

// ---------------------------------------------------------------------------
// Public: EMResult JSON (de)serialization — byte-compatible with Python's
// EMResult.to_dict()/from_dict() (goldenmatch/core/probabilistic.py, schema
// v1). A trained-model JSON file must be loadable by either surface, so the
// wire shape (snake_case keys, __type__/__version__ markers) is authoritative
// on the Python side; this is a faithful mirror, not a TS-native shape.
// ---------------------------------------------------------------------------

/** Current EMResult JSON schema version. Bumped when the wire shape changes
 *  incompatibly (mirrors Python `EMResult.SCHEMA_VERSION`). */
export const EM_RESULT_SCHEMA_VERSION = 1;

/**
 * A persisted FS model is incompatible with the matchkey being scored, or
 * with a JSON blob that fails to parse as an EMResult. Mirrors Python
 * `FSModelMismatchError` / `EMResult.from_dict`'s `ValueError`s.
 */
export class FSModelMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FSModelMismatchError";
  }
}

/**
 * FS negative evidence is not scored on this path. Thrown loudly when a
 * probabilistic matchkey carries non-empty `negativeEvidence` on an entry
 * point that cannot honor it, so a Python-authored NE config can never
 * silently mis-score in TS. The whole discrete FS API path (training,
 * scoring, validation, fallback) covers NE. Two surfaces throw: the
 * continuous (Winkler) path, PERMANENTLY, matching Python; and the
 * pipeline (dedupe/match), whose probabilistic scoring is a simplified
 * weighted-style average (pre-existing TS scope gap) that cannot apply
 * the veto — use the FS API (trainEM + scoreProbabilistic) directly.
 */
export class NegativeEvidenceUnsupportedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NegativeEvidenceUnsupportedError";
  }
}

/**
 * Throw loudly when a probabilistic matchkey carries negative evidence.
 * Only the two continuous (Winkler) entry points call this — they never
 * support NE (mirrors Python's continuous path rejecting NE); the discrete
 * path trains and scores NE natively.
 */
function assertNoNegativeEvidence(mk: MatchkeyConfig, path: string): void {
  if (mk.type !== "probabilistic") return;
  const ne = mk.negativeEvidence;
  if (ne && ne.length > 0) {
    const names = ne.map((n) => n.field).join(", ");
    throw new NegativeEvidenceUnsupportedError(
      `${path}: the continuous (Winkler) path does not support negative ` +
        `evidence (${names}), matching Python -- use the discrete FS path`,
    );
  }
}

/**
 * Serialize an EMResult to the exact JSON shape Python's `EMResult.to_dict()`
 * produces: snake_case keys, `__type__`/`__version__` markers, `tf_freqs`/
 * `tf_collision` as `null` when absent (not omitted — matches Python's
 * dataclass default of `None` surviving `json.dump`).
 */
export function emResultToJson(em: EMResult): Record<string, unknown> {
  return {
    __type__: "goldenmatch.EMResult",
    __version__: EM_RESULT_SCHEMA_VERSION,
    m_probs: em.m,
    u_probs: em.u,
    match_weights: em.matchWeights,
    converged: em.converged,
    iterations: em.iterations,
    proportion_matched: em.proportionMatched,
    tf_freqs: em.tfFreqs ?? null,
    tf_collision: em.tfCollision ?? null,
  };
}

/**
 * Reconstruct an EMResult from JSON previously produced by `emResultToJson`
 * (TS) or `EMResult.to_dict()` (Python) — the two must be interchangeable.
 * Mirrors Python `EMResult.from_dict`: rejects a schema version newer than
 * this build supports, and raises a clear error naming the first missing
 * required key.
 *
 * `tf_freqs`/`tf_collision` round-trip as `null` when the source set them to
 * `null` (rather than being coerced to `undefined`), so
 * `emResultToJson(emResultFromJson(x))` is byte-identical to `x` for both the
 * tf-present and tf-absent cases.
 */
export function emResultFromJson(data: unknown): EMResult {
  if (typeof data !== "object" || data === null) {
    throw new FSModelMismatchError("FS model dict is missing required key: data must be an object");
  }
  const obj = data as Record<string, unknown>;

  const version = typeof obj["__version__"] === "number" ? obj["__version__"] : 1;
  if (version > EM_RESULT_SCHEMA_VERSION) {
    throw new FSModelMismatchError(
      `FS model schema version ${version} is newer than this goldenmatch ` +
        `supports (${EM_RESULT_SCHEMA_VERSION}); upgrade goldenmatch.`,
    );
  }

  const requiredKeys = [
    "m_probs",
    "u_probs",
    "match_weights",
    "converged",
    "iterations",
    "proportion_matched",
  ] as const;
  for (const key of requiredKeys) {
    if (!(key in obj)) {
      throw new FSModelMismatchError(`FS model dict is missing required key: '${key}'`);
    }
  }

  const tfFreqsRaw = obj["tf_freqs"];
  const tfCollisionRaw = obj["tf_collision"];

  return {
    m: obj["m_probs"] as Readonly<Record<string, readonly number[]>>,
    u: obj["u_probs"] as Readonly<Record<string, readonly number[]>>,
    matchWeights: obj["match_weights"] as Readonly<Record<string, readonly number[]>>,
    converged: obj["converged"] as boolean,
    iterations: obj["iterations"] as number,
    proportionMatched: obj["proportion_matched"] as number,
    tfFreqs:
      tfFreqsRaw === null || tfFreqsRaw === undefined
        ? (tfFreqsRaw as null | undefined) ?? null
        : (tfFreqsRaw as Readonly<Record<string, Readonly<Record<string, number>>>>),
    tfCollision:
      tfCollisionRaw === null || tfCollisionRaw === undefined
        ? (tfCollisionRaw as null | undefined) ?? null
        : (tfCollisionRaw as Readonly<Record<string, number>>),
  };
}

/**
 * Field level count as EM/scoring sees it: `levelThresholds` (when present)
 * always wins — its length + 1 is the authoritative level count, matching
 * the loader's `levels === levelThresholds.length + 1` validation invariant.
 */
function matchkeyFieldLevelCount(f: MatchkeyField): number {
  return f.levelThresholds !== undefined ? f.levelThresholds.length + 1 : (f.levels ?? 2);
}

/**
 * Raise if `em` can't score `mk` (field / level mismatch). Ports Python
 * `EMResult.validate_for`: a persisted model is only reusable against the
 * same matchkey shape — every field must have a match-weight vector whose
 * length equals the field's level count. Mismatch means the config changed
 * since training, so fail loudly rather than silently scoring with a stale
 * model.
 */
export function validateEmResultFor(em: EMResult, mk: MatchkeyConfig): void {
  for (const f of mk.fields) {
    const weights = em.matchWeights[f.field];
    if (weights === undefined) {
      throw new FSModelMismatchError(
        `Persisted FS model has no weights for field '${f.field}'. ` +
          `The matchkey changed since training -- retrain or clear the model_path.`,
      );
    }
    const expected = matchkeyFieldLevelCount(f);
    if (weights.length !== expected) {
      throw new FSModelMismatchError(
        `Persisted FS model for field '${f.field}' has ${weights.length} ` +
          `levels but the matchkey expects ${expected}. Retrain or clear ` +
          `the model_path.`,
      );
    }
  }
  // NE fields without penaltyBits require an EM-learned 2-element
  // [fired, not_fired] entry under `__ne__<field>` (Python validate_for).
  for (const ne of mk.negativeEvidence ?? []) {
    if (ne.penaltyBits !== undefined) continue; // fixed override -- no EM entry needed
    const key = `__ne__${ne.field}`;
    const weights = em.matchWeights[key];
    if (weights === undefined) {
      throw new FSModelMismatchError(
        `Persisted FS model has no weights for negative_evidence field ` +
          `'${ne.field}' (expected key '${key}'). The matchkey added this ` +
          `NE field since training -- retrain the model, or set ` +
          `\`penaltyBits\` on the negative_evidence entry to skip EM for ` +
          `this field.`,
      );
    }
    if (weights.length !== 2) {
      throw new FSModelMismatchError(
        `Persisted FS model for negative_evidence field '${ne.field}' ` +
          `(key '${key}') has ${weights.length} entries but NE weights must ` +
          `be a 2-element [fired, not_fired] list. Retrain or clear the ` +
          `model_path.`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Deterministic RNG (xorshift32) — avoids relying on Math.random's seedability
// ---------------------------------------------------------------------------

function makeRng(seed: number): () => number {
  let x = seed | 0 || 1;
  return () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    // Return in [0, 1): divide by 2^32 (not 2^32-1) so the value cannot reach 1.0.
    return (x >>> 0) / 0x100000000;
  };
}

// ---------------------------------------------------------------------------
// Field levels helper
// ---------------------------------------------------------------------------

function fieldLevels(f: MatchkeyField): number {
  return f.levels ?? 2;
}

function fieldPartialThreshold(f: MatchkeyField): number {
  return f.partialThreshold ?? 0.7;
}

// ---------------------------------------------------------------------------
// Public: buildComparisonVector
// ---------------------------------------------------------------------------

/**
 * Build a comparison vector: one integer level per field.
 *   levels=2: 0=disagree, 1=agree
 *   levels=3: 0=disagree, 1=partial, 2=agree (>= 0.95)
 *   levels=N: evenly spaced thresholds k/N for k in 1..N-1
 *   levelThresholds set: custom descending cutoffs; level = count satisfied
 *   (takes priority over the levels-based legacy banding above).
 */
export function buildComparisonVector(
  rowA: Row,
  rowB: Row,
  fields: readonly MatchkeyField[],
): readonly number[] {
  const levels: number[] = [];
  for (const f of fields) {
    let valA = asString(rowA[f.field]);
    let valB = asString(rowB[f.field]);
    if (f.transforms.length > 0) {
      valA = applyTransforms(valA, f.transforms);
      valB = applyTransforms(valB, f.transforms);
    }
    const s = scoreField(valA, valB, f.scorer);
    const n = fieldLevels(f);
    const partial = fieldPartialThreshold(f);

    if (s === null) {
      levels.push(0);
      continue;
    }

    if (f.levelThresholds !== undefined) {
      let level = 0;
      for (const t of f.levelThresholds) if (s >= t) level += 1;
      levels.push(level);
    } else if (n === 2) {
      levels.push(s >= partial ? 1 : 0);
    } else if (n === 3) {
      if (s >= 0.95) levels.push(2);
      else if (s >= partial) levels.push(1);
      else levels.push(0);
    } else {
      let level = 0;
      for (let k = 1; k < n; k++) {
        if (s >= k / n) level = k;
      }
      levels.push(level);
    }
  }
  return levels;
}

// ---------------------------------------------------------------------------
// Public: neFired
// ---------------------------------------------------------------------------

/**
 * Return true iff a Fellegi-Sunter negative-evidence field FIRES for a pair.
 *
 * Mirrors Python `_ne_fired` (core/probabilistic.py): fires when BOTH values
 * are present (post-transform, non-empty) AND the scorer similarity is
 * STRICTLY below `ne.threshold`. Any missing/empty value on either side
 * (including nulls — the deliberate NE null-handling that differs from
 * regular FS fields, where null -> disagree/level-0) means the dimension does
 * NOT fire: negative evidence never boosts a pair, so an inconclusive
 * comparison must not count against a match either.
 *
 * Uses the same transform/scorer machinery as the weighted-NE path
 * (`applyNegativeEvidence` in autoconfigNegativeEvidence.ts):
 * asString -> applyTransforms -> scoreField, with unknown scorers skipped
 * defensively (not fired).
 *
 * Deliberate divergence from `applyNegativeEvidence`: the weighted-NE path
 * scores post-transform EMPTY strings, while FS NE treats them as
 * inconclusive (not fired) -- which is why the two firing checks must NOT
 * be DRY'd into one shared helper.
 */
export function neFired(
  rowA: Row,
  rowB: Row,
  ne: NegativeEvidenceField,
): boolean {
  let valA = asString(rowA[ne.field]);
  let valB = asString(rowB[ne.field]);
  if (valA === null || valB === null) return false;
  if (ne.transforms.length > 0) {
    valA = applyTransforms(valA, ne.transforms);
    valB = applyTransforms(valB, ne.transforms);
  }
  if (!valA || !valB) return false;
  let sim: number | null;
  try {
    sim = scoreField(valA, valB, ne.scorer);
  } catch {
    // Unknown scorer -> not fired. This mirrors the weighted-NE idiom
    // (`applyNegativeEvidence` skips unknown scorers), NOT Python's FS path,
    // which has no guard here and would raise.
    return false;
  }
  if (sim === null) return false;
  return sim < ne.threshold;
}

/**
 * One NE field's scalar weight contribution for a single pair. Mirrors
 * Python `_ne_scalar_contribution`: 0 unless the field FIRES (`neFired`);
 * when fired, `-abs(penaltyBits)` for the fixed override, else the
 * EM-learned `__ne__<field>` fired weight (`matchWeights[...][0]`).
 *
 * A missing `__ne__<field>` entry at scoring time is a programming error --
 * `validateEmResultFor` enforces its presence before scoring. Python's
 * direct index raises KeyError there; the TS contract is the same "fail
 * loudly, never silently contribute 0", surfaced as FSModelMismatchError
 * (with the retrain / penaltyBits remedies) since TS index access would
 * otherwise yield undefined.
 *
 * `key` is the precomputed `__ne__<field>` lookup key -- callers scoring
 * many pairs hoist it outside the pair loop; omitted, it is derived here.
 */
function neContribution(
  rowA: Row,
  rowB: Row,
  ne: NegativeEvidenceField,
  em: EMResult,
  key?: string,
): number {
  if (!neFired(rowA, rowB, ne)) return 0;
  if (ne.penaltyBits !== undefined) return -Math.abs(ne.penaltyBits);
  const entry = em.matchWeights[key ?? `__ne__${ne.field}`];
  if (entry === undefined || entry.length !== 2) {
    throw new FSModelMismatchError(
      `Persisted FS model has no weights for negative_evidence field ` +
        `'${ne.field}' (expected key '__ne__${ne.field}') -- ` +
        `validateEmResultFor should have rejected this model. Retrain the ` +
        `model, or set \`penaltyBits\` on the negative_evidence entry.`,
    );
  }
  return entry[0]!;
}

// ---------------------------------------------------------------------------
// Random-pair sampling (used for u estimation)
// ---------------------------------------------------------------------------

function samplePairs(
  rows: readonly Row[],
  nPairs: number,
  rand: () => number,
): Array<readonly [number, number]> {
  const ids: number[] = [];
  for (const r of rows) {
    const id = r["__row_id__"];
    if (typeof id === "number") ids.push(id);
  }
  if (ids.length < 2) return [];

  const maxPossible = (ids.length * (ids.length - 1)) / 2;
  if (maxPossible <= nPairs) {
    const out: Array<readonly [number, number]> = [];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        out.push([ids[i]!, ids[j]!] as const);
      }
    }
    return out;
  }

  const seen = new Set<string>();
  const pairs: Array<readonly [number, number]> = [];
  const maxAttempts = nPairs * 10;
  let attempts = 0;
  while (pairs.length < nPairs && attempts < maxAttempts) {
    attempts++;
    const i = Math.floor(rand() * ids.length);
    let j = Math.floor(rand() * ids.length);
    if (j === i) j = (j + 1) % ids.length;
    const a = Math.min(ids[i]!, ids[j]!);
    const b = Math.max(ids[i]!, ids[j]!);
    const key = `${a}:${b}`;
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push([a, b] as const);
  }
  return pairs;
}

function buildComparisonMatrix(
  pairs: ReadonlyArray<readonly [number, number]>,
  rowById: ReadonlyMap<number, Row>,
  fields: readonly MatchkeyField[],
): number[][] {
  const out: number[][] = [];
  for (const [a, b] of pairs) {
    const rowA = rowById.get(a) ?? {};
    const rowB = rowById.get(b) ?? {};
    const vec = buildComparisonVector(rowA, rowB, fields);
    out.push([...vec]);
  }
  return out;
}

/**
 * Continuous (Winkler-extension) field scores for a pair: raw scorer output
 * per field in [0,1], preserving the continuous signal instead of
 * discretizing into levels. Null scores become 0.0. Mirrors Python
 * ``probabilistic.continuous_scores``.
 */
export function continuousScores(
  rowA: Row,
  rowB: Row,
  fields: readonly MatchkeyField[],
): readonly number[] {
  const out: number[] = [];
  for (const f of fields) {
    let valA = asString(rowA[f.field]);
    let valB = asString(rowB[f.field]);
    if (f.transforms.length > 0) {
      valA = applyTransforms(valA, f.transforms);
      valB = applyTransforms(valB, f.transforms);
    }
    const s = scoreField(valA, valB, f.scorer);
    out.push(s ?? 0.0);
  }
  return out;
}

function buildContinuousMatrix(
  pairs: ReadonlyArray<readonly [number, number]>,
  rowById: ReadonlyMap<number, Row>,
  fields: readonly MatchkeyField[],
): number[][] {
  const out: number[][] = [];
  for (const [a, b] of pairs) {
    const rowA = rowById.get(a) ?? {};
    const rowB = rowById.get(b) ?? {};
    out.push([...continuousScores(rowA, rowB, fields)]);
  }
  return out;
}

/** Median of a numeric array (matches numpy.median: average of the two
 *  middle elements on even length). */
function median(values: readonly number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid]!;
  return (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/** Weighted average: sum(w_i * x_i) / sum(w_i). Mirrors numpy.average. */
function weightedAverage(
  values: readonly number[],
  weights: readonly number[],
): number {
  let num = 0;
  let den = 0;
  for (let i = 0; i < values.length; i++) {
    num += values[i]! * weights[i]!;
    den += weights[i]!;
  }
  return den === 0 ? 0 : num / den;
}

// ---------------------------------------------------------------------------
// Public: ContinuousEMResult + trainEMContinuous (Winkler extension)
// ---------------------------------------------------------------------------

export interface ContinuousEMResult {
  /** field -> mean score for matches. */
  readonly mMean: Readonly<Record<string, number>>;
  /** field -> variance for matches. */
  readonly mVar: Readonly<Record<string, number>>;
  /** field -> mean score for non-matches. */
  readonly uMean: Readonly<Record<string, number>>;
  /** field -> variance for non-matches. */
  readonly uVar: Readonly<Record<string, number>>;
  readonly converged: boolean;
  readonly iterations: number;
  readonly proportionMatched: number;
}

/**
 * Train Fellegi-Sunter with continuous scores (Winkler extension). Models
 * P(score|match) and P(score|non-match) as per-field Gaussians instead of
 * discretizing into levels. Ports Python ``train_em_continuous``.
 */
export function trainEMContinuous(
  rows: readonly Row[],
  mk: MatchkeyConfig,
  options?: EMOptions,
): ContinuousEMResult {
  assertNoNegativeEvidence(mk, "trainEMContinuous");
  const emIterations =
    mk.type === "probabilistic" ? mk.emIterations : undefined;
  const convergenceThreshold =
    mk.type === "probabilistic" ? mk.convergenceThreshold : undefined;
  const maxIterations = options?.maxIterations ?? emIterations ?? 20;
  const convergence = options?.convergence ?? convergenceThreshold ?? 0.001;
  const blockingFields = new Set(options?.blockingFields ?? []);
  const seed = options?.seed ?? 42;
  const nSamplePairs = options?.nSamplePairs ?? 10000;

  const fields = mk.fields;

  const rand = makeRng(seed);
  const rowById = new Map<number, Row>();
  for (const r of rows) {
    const id = r["__row_id__"];
    if (typeof id === "number") rowById.set(id, r);
  }

  const pairs = samplePairs(rows, nSamplePairs, rand);

  if (pairs.length < 10) {
    const mMean: Record<string, number> = {};
    const mVar: Record<string, number> = {};
    const uMean: Record<string, number> = {};
    const uVar: Record<string, number> = {};
    for (const f of fields) {
      mMean[f.field] = 0.9;
      mVar[f.field] = 0.01;
      uMean[f.field] = 0.2;
      uVar[f.field] = 0.04;
    }
    return {
      mMean,
      mVar,
      uMean,
      uVar,
      converged: false,
      iterations: 0,
      proportionMatched: 0.05,
    };
  }

  const scoreMatrix = buildContinuousMatrix(pairs, rowById, fields);
  const nPairs = pairs.length;

  let pMatch = 0.02;

  // Initialize: matches score high (tight), non-matches at the per-field median.
  const mMean: Record<string, number> = {};
  const mVar: Record<string, number> = {};
  const uMean: Record<string, number> = {};
  const uVar: Record<string, number> = {};
  for (let j = 0; j < fields.length; j++) {
    const f = fields[j]!;
    const col = scoreMatrix.map((row) => row[j]!);
    const med = blockingFields.has(f.field) ? 0.3 : median(col);
    mMean[f.field] = 0.9;
    mVar[f.field] = 0.01;
    uMean[f.field] = med;
    uVar[f.field] = 0.05;
  }
  // Override blocking fields.
  for (const f of fields) {
    if (blockingFields.has(f.field)) {
      mMean[f.field] = 0.99;
      mVar[f.field] = 0.001;
      uMean[f.field] = 0.99;
      uVar[f.field] = 0.001;
    }
  }

  let converged = false;
  let iterations = 0;
  for (let iter = 0; iter < maxIterations; iter++) {
    iterations = iter + 1;
    const oldMMean: Record<string, number> = { ...mMean };
    const oldUMean: Record<string, number> = { ...uMean };

    // E-step: Gaussian log-likelihoods.
    const posteriors = new Float64Array(nPairs);
    for (let i = 0; i < nPairs; i++) {
      let logM = Math.log(Math.max(pMatch, 1e-10));
      let logU = Math.log(Math.max(1 - pMatch, 1e-10));
      for (let j = 0; j < fields.length; j++) {
        const f = fields[j]!;
        if (blockingFields.has(f.field)) continue;
        const s = scoreMatrix[i]![j]!;
        const varM = Math.max(mVar[f.field]!, 1e-6);
        const varU = Math.max(uVar[f.field]!, 1e-6);
        logM +=
          (-0.5 * (s - mMean[f.field]!) ** 2) / varM - 0.5 * Math.log(varM);
        logU +=
          (-0.5 * (s - uMean[f.field]!) ** 2) / varU - 0.5 * Math.log(varU);
      }
      const maxLog = Math.max(logM, logU);
      const em = Math.exp(logM - maxLog);
      const eu = Math.exp(logU - maxLog);
      posteriors[i] = em / (em + eu);
    }

    // M-step.
    let totalMatch = 0;
    for (let i = 0; i < nPairs; i++) totalMatch += posteriors[i]!;
    const totalNonmatch = nPairs - totalMatch;
    pMatch = Math.max(totalMatch / nPairs, 1e-6);

    for (let j = 0; j < fields.length; j++) {
      const f = fields[j]!;
      if (blockingFields.has(f.field)) continue;
      const scores = scoreMatrix.map((row) => row[j]!);
      if (totalMatch > 1e-6) {
        const wMatch = Array.from(posteriors);
        const mm = weightedAverage(scores, wMatch);
        mMean[f.field] = mm;
        const sqDiff = scores.map((s) => (s - mm) ** 2);
        mVar[f.field] = weightedAverage(sqDiff, wMatch) + 1e-6;
      }
      if (totalNonmatch > 1e-6) {
        const wNon = Array.from(posteriors, (p) => 1 - p);
        const um = weightedAverage(scores, wNon);
        uMean[f.field] = um;
        const sqDiff = scores.map((s) => (s - um) ** 2);
        uVar[f.field] = weightedAverage(sqDiff, wNon) + 1e-6;
      }
    }

    // Convergence on means.
    let maxDelta = 0;
    for (const f of fields) {
      if (blockingFields.has(f.field)) continue;
      maxDelta = Math.max(maxDelta, Math.abs(mMean[f.field]! - oldMMean[f.field]!));
      maxDelta = Math.max(maxDelta, Math.abs(uMean[f.field]! - oldUMean[f.field]!));
    }
    if (maxDelta < convergence) {
      converged = true;
      break;
    }
  }

  return {
    mMean,
    mVar,
    uMean,
    uVar,
    converged,
    iterations,
    proportionMatched: pMatch,
  };
}

/**
 * Score pairs using continuous Fellegi-Sunter (Winkler extension). Computes
 * Gaussian log-likelihood ratios and maps to [0,1] via sigmoid. Ports Python
 * ``score_probabilistic_continuous``.
 *
 * Numeric note: Python's ``math.exp`` raises OverflowError on extreme
 * negative log-ratios; JS ``Math.exp`` returns Infinity (sigmoid -> 0) with
 * no error. The committed parity fixtures use score-compressed datasets that
 * never reach that branch, so both sides agree. With raw production data a
 * non-match pair can drive the ratio past Python's overflow point — the TS
 * port degrades gracefully to 0 where Python would raise.
 */
export function scoreProbabilisticContinuous(
  rows: readonly Row[],
  mk: MatchkeyConfig,
  em: ContinuousEMResult,
  options?: ProbScoreOptions,
): ScoredPair[] {
  assertNoNegativeEvidence(mk, "scoreProbabilisticContinuous");
  const fields = mk.fields;
  if (fields.length === 0) return [];

  const excludePairs = options?.excludePairs ?? new Set<string>();
  const threshold = options?.threshold ?? 0.5;

  const rowIds: number[] = [];
  const rowLookup: Row[] = [];
  for (const r of rows) {
    const id = r["__row_id__"];
    if (typeof id === "number") {
      rowIds.push(id);
      rowLookup.push(r);
    }
  }

  const results: ScoredPair[] = [];
  for (let i = 0; i < rowIds.length; i++) {
    for (let j = i + 1; j < rowIds.length; j++) {
      const a = Math.min(rowIds[i]!, rowIds[j]!);
      const b = Math.max(rowIds[i]!, rowIds[j]!);
      const key = `${a}:${b}`;
      if (excludePairs.has(key)) continue;

      const scores = continuousScores(rowLookup[i]!, rowLookup[j]!, fields);

      let logRatio = 0;
      for (let k = 0; k < fields.length; k++) {
        const f = fields[k]!;
        const s = scores[k]!;
        const varM = Math.max(em.mVar[f.field]!, 1e-6);
        const varU = Math.max(em.uVar[f.field]!, 1e-6);
        const logM =
          (-0.5 * (s - em.mMean[f.field]!) ** 2) / varM - 0.5 * Math.log(varM);
        const logU =
          (-0.5 * (s - em.uMean[f.field]!) ** 2) / varU - 0.5 * Math.log(varU);
        logRatio += logM - logU;
      }

      const normalized = 1.0 / (1.0 + Math.exp(-logRatio));
      if (normalized >= threshold) {
        results.push(makeScoredPair(a, b, Math.round(normalized * 10000) / 10000));
      }
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// Public: trainEM
// ---------------------------------------------------------------------------

/**
 * Splink-style EM training:
 *   1. Estimate u from random pairs (fixed throughout).
 *   2. Train m via EM starting from exponential priors.
 *   3. Blocking fields bypass EM and receive fixed neutral u + linear weights.
 *   4. penaltyBits-free NE fields join as constrained 2-state dims carried in
 *      a SEPARATE NE matrix (0 = fired, 1 = not-fired incl. nulls/empties);
 *      full likelihood inside the loop, [wFired, 0.0] clamp at storage only,
 *      stored under `__ne__<field>` (mirrors Python train_em, #1764).
 */
export function trainEM(
  rows: readonly Row[],
  mk: MatchkeyConfig,
  options?: EMOptions,
): EMResult {
  // Probabilistic-only parameters; fall through to defaults for other variants.
  const emIterations =
    mk.type === "probabilistic" ? mk.emIterations : undefined;
  const convergenceThreshold =
    mk.type === "probabilistic" ? mk.convergenceThreshold : undefined;
  const maxIterations = options?.maxIterations ?? emIterations ?? 20;
  const convergence = options?.convergence ?? convergenceThreshold ?? 0.001;
  const blockingFields = new Set(options?.blockingFields ?? []);
  const seed = options?.seed ?? 42;
  const nSamplePairs = options?.nSamplePairs ?? 10000;

  const fields = mk.fields;
  if (fields.length === 0) return fallbackResult(mk);

  // NE fields that participate in EM (mirrors Python `_em_ne_fields`):
  // penaltyBits overrides skip EM entirely and contribute a fixed weight at
  // scoring time.
  const emNeFields =
    mk.type === "probabilistic"
      ? (mk.negativeEvidence ?? []).filter((ne) => ne.penaltyBits === undefined)
      : [];

  const rand = makeRng(seed);
  const rowById = new Map<number, Row>();
  for (const r of rows) {
    const id = r["__row_id__"];
    if (typeof id === "number") rowById.set(id, r);
  }

  // Step 1: u from random pairs.
  const sampleForU = samplePairs(rows, Math.min(nSamplePairs, 5000), rand);
  if (sampleForU.length < 10) return fallbackResult(mk);
  const uMatrix = buildComparisonMatrix(sampleForU, rowById, fields);

  // SEPARATE NE matrix over the SAME pairs (Python `_build_ne_matrix`):
  // 0 = fired, 1 = not-fired INCLUDING nulls/empties (`neFired` returns
  // false there). NE columns must NOT be appended to the comparison matrix —
  // its consumers assume `row.length === fields.length`.
  const neMatrix: number[][] = sampleForU.map(([a, b]) => {
    const rowA = rowById.get(a) ?? {};
    const rowB = rowById.get(b) ?? {};
    return emNeFields.map((ne) => (neFired(rowA, rowB, ne) ? 0 : 1));
  });

  const u: Record<string, number[]> = {};
  fields.forEach((f, j) => {
    const n = fieldLevels(f);
    const counts = new Array<number>(n).fill(0);
    for (const row of uMatrix) {
      const lvl = row[j]!;
      if (lvl >= 0 && lvl < n) counts[lvl]! += 1;
    }
    const total = counts.reduce((a, b) => a + b, 0) + n * 1e-6;
    u[f.field] = counts.map((c) => (c + 1e-6) / total);
  });

  // Blocking fields get neutral u.
  for (const f of fields) {
    if (blockingFields.has(f.field)) {
      const n = fieldLevels(f);
      if (n === 2) u[f.field] = [0.5, 0.5];
      else u[f.field] = [0.34, 0.33, ...new Array<number>(n - 2).fill(0.33 / Math.max(1, n - 2))];
    }
  }

  // NE u: [fired, not_fired] rates from the NE matrix with the same +1e-6
  // smoothing idiom as regular u (Python `_ne_u_probs_from_matrix`). NO
  // blocking-field neutralization — an NE dimension is never a blocking key
  // by construction.
  const uNe: Record<string, number[]> = {};
  emNeFields.forEach((ne, j) => {
    let fired = 0;
    let notFired = 0;
    for (const row of neMatrix) {
      if (row[j] === 0) fired += 1;
      else notFired += 1;
    }
    const total = fired + notFired + 2 * 1e-6;
    uNe[ne.field] = [(fired + 1e-6) / total, (notFired + 1e-6) / total];
  });

  // Step 2: m priors (exponential: highest level gets most mass).
  const m: Record<string, number[]> = {};
  for (const f of fields) {
    const n = fieldLevels(f);
    const raw: number[] = [];
    for (let k = 0; k < n; k++) raw.push(2 ** k);
    const sum = raw.reduce((a, b) => a + b, 0);
    m[f.field] = raw.map((r) => r / sum);
  }

  // NE m init: fired is rare in true matches (a match usually agrees on the
  // NE field), so seed with a low fired-probability prior (Python: [0.05, 0.95]).
  const mNe: Record<string, number[]> = {};
  for (const ne of emNeFields) mNe[ne.field] = [0.05, 0.95];

  // Use the same random-pair matrix for EM. In Python, blocked pairs are
  // preferred when available; we don't have blocks in this entry point, so
  // we train on the random sample (the fallback path).
  const compMatrix = uMatrix;
  const nPairs = compMatrix.length;

  let pMatch = 0.02;
  let converged = false;
  let iterations = 0;

  for (let iter = 0; iter < maxIterations; iter++) {
    iterations = iter + 1;
    const oldM: Record<string, number[]> = {};
    for (const k of Object.keys(m)) oldM[k] = [...m[k]!];
    const oldMNe: Record<string, number[]> = {};
    for (const k of Object.keys(mNe)) oldMNe[k] = [...mNe[k]!];

    // E-step. NE dims contribute their FULL 2-state likelihood — for a
    // not-fired event the model term is log(m1)/log(u1), never a zeroed
    // weight. The [wFired, 0] clamp is STORAGE-ONLY (applied after the
    // loop); clamping here would bias m.
    const posteriors = new Float64Array(nPairs);
    for (let i = 0; i < nPairs; i++) {
      let logM = Math.log(Math.max(pMatch, 1e-10));
      let logU = Math.log(Math.max(1 - pMatch, 1e-10));
      for (let j = 0; j < fields.length; j++) {
        const f = fields[j]!;
        const level = compMatrix[i]![j]!;
        const mProb = Math.max(m[f.field]![level] ?? 1e-10, 1e-10);
        const uProb = Math.max(u[f.field]![level] ?? 1e-10, 1e-10);
        logM += Math.log(mProb);
        logU += Math.log(uProb);
      }
      for (let j = 0; j < emNeFields.length; j++) {
        const ne = emNeFields[j]!;
        const ev = neMatrix[i]![j]!; // 0 = fired, 1 = not-fired
        logM += Math.log(Math.max(mNe[ne.field]![ev]!, 1e-10));
        logU += Math.log(Math.max(uNe[ne.field]![ev]!, 1e-10));
      }
      const maxLog = Math.max(logM, logU);
      const em = Math.exp(logM - maxLog);
      const eu = Math.exp(logU - maxLog);
      posteriors[i] = em / (em + eu);
    }

    // M-step (m only).
    let totalMatch = 0;
    for (let i = 0; i < nPairs; i++) totalMatch += posteriors[i]!;
    pMatch = Math.max(totalMatch / nPairs, 1e-6);

    for (let j = 0; j < fields.length; j++) {
      const f = fields[j]!;
      if (blockingFields.has(f.field)) continue;
      const n = fieldLevels(f);
      const newM = new Array<number>(n).fill(0);
      for (let i = 0; i < nPairs; i++) {
        const level = compMatrix[i]![j]!;
        if (level >= 0 && level < n) newM[level]! += posteriors[i]!;
      }
      const denom = totalMatch + n * 1e-6;
      for (let k = 0; k < n; k++) {
        newM[k] = (newM[k]! + 1e-6) / denom;
      }
      m[f.field] = newM;
    }

    // NE dims: same M-step update. Blocking-field neutralization does NOT
    // apply — an NE dimension is never a blocking key.
    for (let j = 0; j < emNeFields.length; j++) {
      const ne = emNeFields[j]!;
      const newM = [0, 0];
      for (let i = 0; i < nPairs; i++) newM[neMatrix[i]![j]!]! += posteriors[i]!;
      const denom = totalMatch + 2 * 1e-6;
      mNe[ne.field] = [(newM[0]! + 1e-6) / denom, (newM[1]! + 1e-6) / denom];
    }

    // Convergence: max m delta INCLUDING NE dims (mirrors Python train_em).
    let maxDelta = 0;
    for (const f of fields) {
      if (blockingFields.has(f.field)) continue;
      const n = fieldLevels(f);
      for (let k = 0; k < n; k++) {
        const d = Math.abs(m[f.field]![k]! - oldM[f.field]![k]!);
        if (d > maxDelta) maxDelta = d;
      }
    }
    for (const ne of emNeFields) {
      for (let k = 0; k < 2; k++) {
        const d = Math.abs(mNe[ne.field]![k]! - oldMNe[ne.field]![k]!);
        if (d > maxDelta) maxDelta = d;
      }
    }
    if (maxDelta < convergence) {
      converged = true;
      break;
    }
  }

  // Match weights = log2(m/u), with fixed linear weights for blocking fields.
  const matchWeights: Record<string, number[]> = {};
  for (const f of fields) {
    const n = fieldLevels(f);
    if (blockingFields.has(f.field)) {
      const w: number[] = [];
      for (let k = 0; k < n; k++) {
        w.push(n > 1 ? -3.0 + (6.0 * k) / (n - 1) : 3.0);
      }
      matchWeights[f.field] = w;
      continue;
    }
    const w: number[] = [];
    for (let k = 0; k < n; k++) {
      const mVal = Math.max(m[f.field]![k]!, 1e-10);
      const uVal = Math.max(u[f.field]![k]!, 1e-10);
      w.push(Math.log2(mVal / uVal));
    }
    matchWeights[f.field] = w;
  }

  // NE dims: store under __ne__<field> — [wFired, 0.0]. The 0.0 for
  // not-fired is the NEGATIVE-EVIDENCE CLAMP (not log2(m1/u1)): agreement
  // or an inconclusive comparison never boosts the score, only a confident
  // disagreement subtracts from it. Applied at STORAGE ONLY — the EM loop
  // above always saw the full 2-state likelihood.
  // Porting note: Python's train_em ends with an optional monotonicity
  // repair that SKIPS `__ne__` keys (they are [fired, not_fired]-ordered,
  // not level-ordered). TS has no monotonicity guard today; any future port
  // of it must skip `__ne__` keys the same way.
  for (const ne of emNeFields) {
    const key = `__ne__${ne.field}`;
    m[key] = [...mNe[ne.field]!];
    u[key] = [...uNe[ne.field]!];
    const m0 = Math.max(mNe[ne.field]![0]!, 1e-10);
    const u0 = Math.max(uNe[ne.field]![0]!, 1e-10);
    matchWeights[key] = [Math.log2(m0 / u0), 0.0];
  }

  return {
    m: m as Readonly<Record<string, readonly number[]>>,
    u: u as Readonly<Record<string, readonly number[]>>,
    matchWeights: matchWeights as Readonly<Record<string, readonly number[]>>,
    proportionMatched: pMatch,
    iterations,
    converged,
  };
}

// ---------------------------------------------------------------------------
// Public: fsWeightRange
// ---------------------------------------------------------------------------

/**
 * Achievable Fellegi-Sunter total-weight range for normalization.
 *
 * Mirrors Python `fs_weight_range` (core/probabilistic.py): centralizes the
 * min/max weight-sum computation previously hand-rolled at every scoring
 * site — a missed site silently produced out-of-[0,1] normalized scores as
 * soon as an NE field fired.
 *
 * Regular fields: sum(min)/sum(max) over `em.matchWeights[f.field]`, with
 * missing/empty entries skipped (unchanged pre-NE behavior).
 *
 * NE fields (`mk.negativeEvidence`):
 * - `penaltyBits` set: contributes `(-abs(penaltyBits), 0)` directly — the
 *   fixed-override case needs no EM entry.
 * - else: min/max over the EM-learned `matchWeights["__ne__<field>"]` entry
 *   (stored as `[wFired, 0]`, so this reproduces `(min(wFired, 0),
 *   max(wFired, 0))` regardless of sign).
 * - An NE field with neither (shouldn't survive `validateEmResultFor`) is
 *   defensively skipped — contributes `(0, 0)` rather than throwing.
 */
export function fsWeightRange(
  em: EMResult,
  mk: MatchkeyConfig,
): { minWeight: number; maxWeight: number } {
  let minWeight = 0;
  let maxWeight = 0;
  for (const f of mk.fields) {
    const w = em.matchWeights[f.field];
    if (!w || w.length === 0) continue;
    maxWeight += w.reduce((m, v) => (v > m ? v : m), -Infinity);
    minWeight += w.reduce((m, v) => (v < m ? v : m), Infinity);
  }
  for (const ne of mk.negativeEvidence ?? []) {
    if (ne.penaltyBits !== undefined) {
      minWeight += -Math.abs(ne.penaltyBits);
      continue;
    }
    const entry = em.matchWeights[`__ne__${ne.field}`];
    if (!entry || entry.length === 0) continue;
    maxWeight += entry.reduce((m, v) => (v > m ? v : m), -Infinity);
    minWeight += entry.reduce((m, v) => (v < m ? v : m), Infinity);
  }
  return { minWeight, maxWeight };
}

// ---------------------------------------------------------------------------
// Public: scoreProbabilistic
// ---------------------------------------------------------------------------

export interface ProbScoreOptions {
  readonly excludePairs?: ReadonlySet<string>;
  readonly threshold?: number;
}

/**
 * Score all pairs in a block using F-S match weights.
 * Returns normalized scores in [0,1] (weight sum mapped to 0-1 via min/max).
 * Pairs below threshold are filtered out.
 */
export function scoreProbabilistic(
  rows: readonly Row[],
  mk: MatchkeyConfig,
  em: EMResult,
  options?: ProbScoreOptions,
): ScoredPair[] {
  const fields = mk.fields;
  if (fields.length === 0) return [];

  const excludePairs = options?.excludePairs ?? new Set<string>();
  const linkThreshold =
    mk.type === "probabilistic" ? mk.linkThreshold : undefined;
  const threshold = options?.threshold ?? linkThreshold ?? 0.5;

  // Min/max possible weight totals for normalization (NE-aware envelope).
  const { minWeight, maxWeight } = fsWeightRange(em, mk);
  const weightRange = maxWeight - minWeight;

  // NE fields + their `__ne__` lookup keys, hoisted outside the pair loop.
  const neFields =
    mk.type === "probabilistic" ? (mk.negativeEvidence ?? []) : [];
  const neKeys = neFields.map((ne) => `__ne__${ne.field}`);

  const rowIds: number[] = [];
  const rowLookup: Row[] = [];
  for (const r of rows) {
    const id = r["__row_id__"];
    if (typeof id === "number") {
      rowIds.push(id);
      rowLookup.push(r);
    }
  }

  const results: ScoredPair[] = [];
  for (let i = 0; i < rowIds.length; i++) {
    for (let j = i + 1; j < rowIds.length; j++) {
      const a = Math.min(rowIds[i]!, rowIds[j]!);
      const b = Math.max(rowIds[i]!, rowIds[j]!);
      const key = `${a}:${b}`;
      if (excludePairs.has(key)) continue;

      const vec = buildComparisonVector(rowLookup[i]!, rowLookup[j]!, fields);

      let total = 0;
      for (let k = 0; k < fields.length; k++) {
        const f = fields[k]!;
        const level = vec[k]!;
        const w = em.matchWeights[f.field];
        if (!w) continue;
        total += w[level] ?? 0;
      }
      for (let k = 0; k < neFields.length; k++) {
        total += neContribution(
          rowLookup[i]!,
          rowLookup[j]!,
          neFields[k]!,
          em,
          neKeys[k]!,
        );
      }

      const normalized =
        weightRange > 0 ? (total - minWeight) / weightRange : 0.5;

      if (normalized >= threshold) {
        results.push(
          makeScoredPair(a, b, Math.round(normalized * 10000) / 10000),
        );
      }
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// Public: scoreProbabilisticPair (single-pair variant for match_one use)
// ---------------------------------------------------------------------------

export function scoreProbabilisticPair(
  rowA: Row,
  rowB: Row,
  mk: MatchkeyConfig,
  em: EMResult,
): number {
  const fields = mk.fields;
  if (fields.length === 0) return 0.5;

  const { minWeight, maxWeight } = fsWeightRange(em, mk);
  const weightRange = maxWeight - minWeight;
  if (weightRange <= 0) return 0.5;

  const vec = buildComparisonVector(rowA, rowB, fields);
  let total = 0;
  for (let k = 0; k < fields.length; k++) {
    const f = fields[k]!;
    const level = vec[k]!;
    const w = em.matchWeights[f.field];
    if (!w) continue;
    total += w[level] ?? 0;
  }
  const neFields =
    mk.type === "probabilistic" ? (mk.negativeEvidence ?? []) : [];
  for (const ne of neFields) {
    total += neContribution(rowA, rowB, ne, em);
  }
  // NOTE: raw float on purpose -- the round-4 output convention applies to
  // scoreProbabilistic only, on both surfaces (Python parity).
  return (total - minWeight) / weightRange;
}

// ---------------------------------------------------------------------------
// Fallback result for tiny datasets
// ---------------------------------------------------------------------------

/**
 * Conservative EMResult defaults when EM can't run (too few pairs / no
 * fields). Mirrors Python `_fallback_result`, including its NE posture:
 * each penaltyBits-free negative-evidence field gets a fixed
 * `matchWeights["__ne__<field>"] = [-3.0, 0.0]` (m=0.0625, u=0.5 ->
 * log2(0.0625/0.5) == -3.0 exactly -- consistent with a symmetric
 * u=[0.5, 0.5] non-match prior). penaltyBits NE fields need no EM entry
 * (fixed override). Exported for tests; in-module callers (`trainEM`) reach
 * it directly.
 */
export function fallbackResult(mk: MatchkeyConfig): EMResult {
  const m: Record<string, number[]> = {};
  const u: Record<string, number[]> = {};
  const w: Record<string, number[]> = {};
  for (const f of mk.fields) {
    const n = fieldLevels(f);
    if (n === 2) {
      m[f.field] = [0.1, 0.9];
      u[f.field] = [0.9, 0.1];
      w[f.field] = [Math.log2(0.1 / 0.9), Math.log2(0.9 / 0.1)];
    } else if (n === 3) {
      m[f.field] = [0.05, 0.15, 0.8];
      u[f.field] = [0.8, 0.15, 0.05];
      w[f.field] = [
        Math.log2(0.05 / 0.8),
        Math.log2(0.15 / 0.15),
        Math.log2(0.8 / 0.05),
      ];
    } else {
      // Uniform fallback.
      const mv = new Array<number>(n).fill(1 / n);
      const uv = new Array<number>(n).fill(1 / n);
      m[f.field] = mv;
      u[f.field] = uv;
      w[f.field] = new Array<number>(n).fill(0);
    }
  }
  for (const ne of mk.negativeEvidence ?? []) {
    if (ne.penaltyBits !== undefined) continue; // fixed override -- no EM entry
    const key = `__ne__${ne.field}`;
    m[key] = [0.0625, 0.9375];
    u[key] = [0.5, 0.5];
    w[key] = [-3.0, 0.0];
  }
  return {
    m,
    u,
    matchWeights: w,
    proportionMatched: 0.05,
    iterations: 0,
    converged: false,
  };
}
