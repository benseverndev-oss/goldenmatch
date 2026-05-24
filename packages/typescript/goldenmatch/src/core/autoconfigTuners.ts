/**
 * autoconfigTuners.ts — memory-backed adaptive tuners (gap 4).
 * Edge-safe: no `node:` imports (consumes the abstract MemoryStore).
 *
 * Ports three Python tuners:
 *   - tuneNeField        <- core/autoconfig_ne_tuner.py
 *   - tuneFieldStrategy  <- core/autoconfig_golden_strategy_tuner.py
 *   - tuneDecisionThreshold <- core/autoconfig_cluster_threshold_tuner.py
 *
 * All three learn from MemoryStore corrections, gated on a minimum count,
 * with a held-out overfit guard. They are async because MemoryStore is async
 * in the TS port (Python's is sync).
 *
 * Correction-shape note: Python's ne_tuner keys on decision == "match"; the TS
 * Correction decision union uses "approve" as the match-truth equivalent, so
 * the predicates below treat "approve" as Python's "match". The
 * cluster-decision tuner reads the optional clusterScore / clusterOutcome
 * fields added to the TS Correction interface for this port.
 */

import type { Correction, MemoryStore } from "./memory/types.js";

// ===========================================================================
// NE tuner — adaptive penalty / threshold for negative-evidence fields
// ===========================================================================

export const NE_PENALTY_GRID: readonly number[] = [0.1, 0.2, 0.3, 0.4, 0.5];
export const NE_THRESHOLD_GRID: readonly number[] = [0.2, 0.3, 0.4, 0.5];
export const NE_MIN_CORRECTIONS = 50;
export const NE_HELDOUT_FRACTION = 0.1;
export const NE_OVERFIT_GUARD_PP = 5.0;
export const NE_DEFAULT_PENALTY = 0.3;
export const NE_DEFAULT_THRESHOLD = 0.4;

export interface NETuning {
  readonly penalty: number;
  readonly threshold: number;
  readonly nCorrections: number;
  readonly trainF1: number | null;
  readonly heldoutF1: number | null;
  readonly reason: "tuned" | "below_minimum" | "overfit_guard" | "no_memory";
}

/** True iff a correction labels a "match" (Python "match" == TS "approve"). */
function neIsMatch(c: Correction): boolean {
  return c.decision === "approve";
}

/** Whether the candidate (penalty, threshold) predicts "match" for a labeled
 *  correction, using `trust` as the scorer proxy (Python ne_tuner heuristic). */
function neScoreCorrection(c: Correction, penalty: number, threshold: number): boolean {
  const rawScore = c.trust;
  if (rawScore === null || rawScore === undefined) return false;
  if (neIsMatch(c)) return rawScore >= threshold;
  return rawScore - penalty >= threshold;
}

function neF1(corrections: readonly Correction[], penalty: number, threshold: number): number {
  let tp = 0;
  let fp = 0;
  let fn = 0;
  for (const c of corrections) {
    const predicted = neScoreCorrection(c, penalty, threshold);
    const actual = neIsMatch(c);
    if (predicted && actual) tp++;
    else if (predicted && !actual) fp++;
    else if (!predicted && actual) fn++;
  }
  if (tp === 0) return 0.0;
  const precision = tp + fp > 0 ? tp / (tp + fp) : 0.0;
  const recall = tp + fn > 0 ? tp / (tp + fn) : 0.0;
  if (precision + recall === 0) return 0.0;
  return (2 * precision * recall) / (precision + recall);
}

/** Tune (penalty, threshold) for one NE field via grid search over labeled
 *  corrections. Mirrors Python ``tune_ne_field``. */
export async function tuneNeField(
  store: MemoryStore | null,
  dataset: string,
  _field: string,
  minCorrections: number = NE_MIN_CORRECTIONS,
): Promise<NETuning> {
  if (store === null) {
    return {
      penalty: NE_DEFAULT_PENALTY,
      threshold: NE_DEFAULT_THRESHOLD,
      nCorrections: 0,
      trainF1: null,
      heldoutF1: null,
      reason: "no_memory",
    };
  }

  const corrections = await store.getCorrections({ dataset });
  const n = corrections.length;

  if (n < minCorrections) {
    return {
      penalty: NE_DEFAULT_PENALTY,
      threshold: NE_DEFAULT_THRESHOLD,
      nCorrections: n,
      trainF1: null,
      heldoutF1: null,
      reason: "below_minimum",
    };
  }

  // 90/10 deterministic split by correction id.
  const sorted = [...corrections].sort((a, b) => (a.id ?? "").localeCompare(b.id ?? ""));
  const nHeldout = Math.max(Math.trunc(n * NE_HELDOUT_FRACTION), 1);
  const train = sorted.slice(0, n - nHeldout);
  const heldout = sorted.slice(n - nHeldout);

  let bestF1 = -1.0;
  let bestPenalty = NE_DEFAULT_PENALTY;
  let bestThreshold = NE_DEFAULT_THRESHOLD;
  for (const penalty of NE_PENALTY_GRID) {
    for (const threshold of NE_THRESHOLD_GRID) {
      const f1 = neF1(train, penalty, threshold);
      if (f1 > bestF1) {
        bestF1 = f1;
        bestPenalty = penalty;
        bestThreshold = threshold;
      }
    }
  }

  const heldoutF1 = neF1(heldout, bestPenalty, bestThreshold);
  if (bestF1 * 100 - heldoutF1 * 100 > NE_OVERFIT_GUARD_PP) {
    return {
      penalty: NE_DEFAULT_PENALTY,
      threshold: NE_DEFAULT_THRESHOLD,
      nCorrections: n,
      trainF1: bestF1,
      heldoutF1,
      reason: "overfit_guard",
    };
  }

  return {
    penalty: bestPenalty,
    threshold: bestThreshold,
    nCorrections: n,
    trainF1: bestF1,
    heldoutF1,
    reason: "tuned",
  };
}

// ===========================================================================
// Golden-strategy tuner — learn the best survivorship strategy per field
// ===========================================================================

export const GOLDEN_DEFAULT_CANDIDATES: readonly string[] = [
  "most_complete",
  "majority_vote",
  "first_non_null",
  "longest_value",
  "confidence_majority",
];
export const GOLDEN_MIN_CORRECTIONS = 50;
export const GOLDEN_HELDOUT_FRACTION = 0.1;
export const GOLDEN_OVERFIT_GUARD_PP = 5.0;

export interface StrategyTuning {
  readonly field: string;
  readonly strategy: string;
  readonly nCorrections: number;
  readonly trainHitRate: number | null;
  readonly heldoutHitRate: number | null;
  readonly reason: "learned" | "below_minimum" | "no_memory" | "overfit_guard";
}

/** Whether `strategy` would have produced the reviewer's chosen value.
 *  Faithful port of Python ``_strategy_would_match`` (two regimes). */
function strategyWouldMatch(c: Correction, strategy: string, field: string | null): boolean {
  const fname = c.fieldName ?? null;
  if (fname !== null) {
    // Regime 1: field-level correction.
    if (field !== null && fname !== field) return false;
    const orig = c.originalValue ?? null;
    const corrected = c.correctedValue ?? null;
    if (corrected === null) return false;
    if (orig === corrected) {
      return ["most_complete", "longest_value", "majority_vote", "first_non_null"].includes(
        strategy,
      );
    }
    if (orig !== null) {
      if (strategy === "longest_value") return corrected.length > orig.length;
      if (strategy === "unanimous_or_null") return corrected === "" || corrected === null;
      if (strategy === "confidence_majority") return true;
      if (strategy === "most_recent" || strategy === "source_priority") return true;
      if (["most_complete", "majority_vote", "first_non_null"].includes(strategy)) return false;
    }
    return strategy === "first_non_null" || strategy === "most_complete";
  }

  // Regime 2: pair-level correction (older heuristic).
  const rawTrust = c.trust;
  if (rawTrust === null || rawTrust === undefined) return false;
  if (rawTrust < 0.5) return false;
  const preserves = ["most_complete", "longest_value", "majority_vote", "first_non_null"];
  const drops = ["unanimous_or_null", "confidence_majority"];
  // TS decision union: "approve" == Python "approve"; otherwise treat as reject.
  if (c.decision === "approve") return preserves.includes(strategy);
  return drops.includes(strategy);
}

function hitRate(
  corrections: readonly Correction[],
  strategy: string,
  field: string | null,
): number {
  if (corrections.length === 0) return 0.0;
  let hits = 0;
  for (const c of corrections) if (strategyWouldMatch(c, strategy, field)) hits++;
  return hits / corrections.length;
}

/** Learn the best golden-strategy for `field` from MemoryStore corrections.
 *  Mirrors Python ``tune_field_strategy``. */
export async function tuneFieldStrategy(
  store: MemoryStore | null,
  dataset: string,
  field: string,
  candidates: readonly string[] = GOLDEN_DEFAULT_CANDIDATES,
  minCorrections: number = GOLDEN_MIN_CORRECTIONS,
): Promise<StrategyTuning> {
  if (store === null) {
    return {
      field,
      strategy: "",
      nCorrections: 0,
      trainHitRate: null,
      heldoutHitRate: null,
      reason: "no_memory",
    };
  }

  const all = await store.getCorrections({ dataset });
  const fieldLevel = all.filter((c) => (c.fieldName ?? null) === field);
  const pairLevel = all.filter((c) => (c.fieldName ?? null) === null);
  const corrections =
    fieldLevel.length >= minCorrections ? fieldLevel : [...fieldLevel, ...pairLevel];
  const n = corrections.length;
  if (n < minCorrections) {
    return {
      field,
      strategy: "",
      nCorrections: n,
      trainHitRate: null,
      heldoutHitRate: null,
      reason: "below_minimum",
    };
  }

  const sorted = [...corrections].sort((a, b) => (a.id ?? "").localeCompare(b.id ?? ""));
  const nHeldout = Math.max(Math.trunc(n * GOLDEN_HELDOUT_FRACTION), 1);
  const train = sorted.slice(0, n - nHeldout);
  const heldout = sorted.slice(n - nHeldout);

  let bestStrategy = "most_complete";
  let bestTrainRate = -1.0;
  for (const strat of candidates) {
    const rate = hitRate(train, strat, field);
    if (rate > bestTrainRate) {
      bestTrainRate = rate;
      bestStrategy = strat;
    }
  }

  const heldoutRate = hitRate(heldout, bestStrategy, field);
  if (bestTrainRate * 100 - heldoutRate * 100 > GOLDEN_OVERFIT_GUARD_PP) {
    return {
      field,
      strategy: "",
      nCorrections: n,
      trainHitRate: bestTrainRate,
      heldoutHitRate: heldoutRate,
      reason: "overfit_guard",
    };
  }

  return {
    field,
    strategy: bestStrategy,
    nCorrections: n,
    trainHitRate: bestTrainRate,
    heldoutHitRate: heldoutRate,
    reason: "learned",
  };
}

// ===========================================================================
// Cluster-decision threshold tuner
// ===========================================================================

export interface ThresholdSuggestion {
  readonly threshold: number | null;
  readonly nTotal: number;
  readonly nTrain: number;
  readonly nHeldout: number;
  readonly trainApproveRate: number | null;
  readonly heldoutApproveRate: number | null;
  readonly reason: "ok" | "below_minimum" | "no_qualifying_band" | "overfit";
}

export interface ThresholdTunerOptions {
  readonly targetApproveRate?: number;
  readonly minBandN?: number;
  readonly holdoutFrac?: number;
  readonly maxOverfitDropPp?: number;
  readonly seed?: number;
}

/** Deterministic Mulberry32 RNG seeded like Python's first-8-bytes-of-sha256
 *  fallback would be — but the seed itself must be supplied by the caller for
 *  cross-language determinism (the TS port doesn't hash the dataset string;
 *  callers pass an explicit `seed` when they need a reproducible split). */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** In-place Fisher-Yates shuffle using the supplied RNG. */
function shuffle<T>(arr: T[], rand: () => number): void {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const tmp = arr[i]!;
    arr[i] = arr[j]!;
    arr[j] = tmp;
  }
}

/**
 * Sweep cluster_decision corrections for a threshold that hits
 * `targetApproveRate` on training while staying valid on held-out. Faithful
 * port of Python ``tune_decision_threshold``.
 *
 * Determinism caveat vs Python: Python defaults its shuffle seed to the first
 * 8 bytes of sha256(dataset); the TS port takes the seed as an explicit option
 * (default 0). With a supplied seed the split is reproducible within TS, but
 * the train/heldout partition is NOT byte-identical to Python's sha256-seeded
 * split. The sweep/overfit-guard LOGIC matches exactly.
 */
export async function tuneDecisionThreshold(
  store: MemoryStore,
  dataset: string,
  options: ThresholdTunerOptions = {},
): Promise<ThresholdSuggestion> {
  const targetApproveRate = options.targetApproveRate ?? 0.99;
  const minBandN = options.minBandN ?? 50;
  const holdoutFrac = options.holdoutFrac ?? 0.1;
  const maxOverfitDropPp = options.maxOverfitDropPp ?? 1.0;
  const seed = options.seed ?? 0;

  const all = await store.getCorrections({ dataset });
  const corrections = all.filter(
    (c) =>
      c.decision === "cluster_decision" &&
      c.clusterScore !== null &&
      c.clusterScore !== undefined &&
      (c.clusterOutcome === "approve" || c.clusterOutcome === "reject"),
  );
  const nTotal = corrections.length;

  if (nTotal < minBandN * 2) {
    return {
      threshold: null,
      nTotal,
      nTrain: 0,
      nHeldout: 0,
      trainApproveRate: null,
      heldoutApproveRate: null,
      reason: "below_minimum",
    };
  }

  const rand = mulberry32(seed);
  const shuffled = [...corrections];
  shuffle(shuffled, rand);

  const nHeldout = Math.max(1, Math.round(nTotal * holdoutFrac));
  const nTrain = nTotal - nHeldout;
  const heldout = shuffled.slice(0, nHeldout);
  const train = shuffled.slice(nHeldout);

  const trainSorted = [...train].sort(
    (a, b) => (b.clusterScore as number) - (a.clusterScore as number),
  );

  let bestThreshold: number | null = null;
  let bestTrainRate: number | null = null;
  let approves = 0;
  for (let i = 0; i < trainSorted.length; i++) {
    const c = trainSorted[i]!;
    if (c.clusterOutcome === "approve") approves++;
    const bandN = i + 1;
    if (bandN < minBandN) continue;
    const rate = approves / bandN;
    if (rate >= targetApproveRate) {
      bestThreshold = c.clusterScore as number;
      bestTrainRate = rate;
    } else {
      break;
    }
  }

  if (bestThreshold === null || bestTrainRate === null) {
    return {
      threshold: null,
      nTotal,
      nTrain,
      nHeldout,
      trainApproveRate: null,
      heldoutApproveRate: null,
      reason: "no_qualifying_band",
    };
  }

  const heldoutAt = heldout.filter((c) => (c.clusterScore as number) >= bestThreshold!);
  if (heldoutAt.length === 0) {
    return {
      threshold: null,
      nTotal,
      nTrain,
      nHeldout,
      trainApproveRate: bestTrainRate,
      heldoutApproveRate: null,
      reason: "overfit",
    };
  }
  const heldoutApproves = heldoutAt.filter((c) => c.clusterOutcome === "approve").length;
  const heldoutRate = heldoutApproves / heldoutAt.length;

  if (
    heldoutRate < targetApproveRate ||
    bestTrainRate * 100 - heldoutRate * 100 > maxOverfitDropPp
  ) {
    return {
      threshold: null,
      nTotal,
      nTrain,
      nHeldout,
      trainApproveRate: bestTrainRate,
      heldoutApproveRate: heldoutRate,
      reason: "overfit",
    };
  }

  return {
    threshold: bestThreshold,
    nTotal,
    nTrain,
    nHeldout,
    trainApproveRate: bestTrainRate,
    heldoutApproveRate: heldoutRate,
    reason: "ok",
  };
}
