/**
 * memory/learner.ts -- MemoryLearner: threshold tuning from corrections.
 * Edge-safe: no `node:` imports.
 *
 * Ports `goldenmatch/core/memory/learner.py:1-118` line-by-line. Threshold
 * tuning at `thresholdMinCorrections` (default 10) via grid search weighted
 * by correction trust. Field weights remain null in v0.4.0 (stubbed; matches
 * Python's v1.6.0 behavior, which returns None pending per-field subscores).
 *
 * Constructor takes a `LearningConfig` object (TS-idiomatic) rather than the
 * positional numeric args used by Python.
 */

import type {
  Correction,
  LearnedAdjustment,
  LearningConfig,
  MemoryStore,
} from "./types.js";

const DEFAULT_LEARNING: LearningConfig = {
  thresholdMinCorrections: 10,
  weightsMinCorrections: 50,
};

export class MemoryLearner {
  private readonly store: MemoryStore;
  private readonly config: LearningConfig;

  constructor(store: MemoryStore, config: LearningConfig = DEFAULT_LEARNING) {
    this.store = store;
    this.config = config;
  }

  /** True if corrections exist since the last learning pass. */
  async hasNewCorrections(): Promise<boolean> {
    const last = await this.store.lastLearnTime();
    if (last === null) {
      return (await this.store.countCorrections()) > 0;
    }
    return (await this.store.correctionsSince(last)).length > 0;
  }

  /** Run a learning pass. Returns list of learned adjustments. */
  async learn(matchkeyName?: string): Promise<LearnedAdjustment[]> {
    const allCorrections = await this.store.getCorrections();
    if (allCorrections.length === 0) return [];

    // Group corrections by matchkey_name (fall back to dataset, then "_default")
    const byMatchkey = new Map<string, Correction[]>();
    for (const c of allCorrections) {
      const key = c.matchkeyName ?? c.dataset ?? "_default";
      if (matchkeyName !== undefined && key !== matchkeyName) continue;
      let bucket = byMatchkey.get(key);
      if (bucket === undefined) {
        bucket = [];
        byMatchkey.set(key, bucket);
      }
      bucket.push(c);
    }

    const results: LearnedAdjustment[] = [];
    for (const [mkName, corrections] of byMatchkey) {
      if (corrections.length < this.config.thresholdMinCorrections) continue;

      const approved: number[] = [];
      const rejected: number[] = [];
      for (const c of corrections) {
        if (c.decision === "approve") approved.push(c.originalScore);
        else if (c.decision === "reject") rejected.push(c.originalScore);
      }
      if (approved.length === 0 || rejected.length === 0) continue;

      const threshold = this.computeThreshold(approved, rejected, corrections);

      // Field weights stub per Python v1.6.0 -- returns null until per-field
      // subscores are stored on Correction.
      const adj: LearnedAdjustment = {
        matchkeyName: mkName,
        threshold,
        fieldWeights: null,
        sampleSize: corrections.length,
        learnedAt: new Date(),
      };
      await this.store.saveAdjustment(adj);
      results.push(adj);
    }
    return results;
  }

  /**
   * Find threshold separating approves from rejects. Clean-separation case:
   * midpoint of (max rejected, min approved). Overlapping case: grid search
   * over candidate thresholds (midpoints of consecutive sorted unique scores)
   * minimizing trust-weighted misclassification cost. Mirrors Python
   * `_compute_threshold` exactly.
   */
  private computeThreshold(
    approved: number[],
    rejected: number[],
    all: readonly Correction[],
  ): number {
    // Math.max/min via spread is fine here: corrections per matchkey is
    // typically small (<100). For PR 2 row-count paths, the spec bans this
    // pattern; the learner only sees correction scores.
    const maxRejected = Math.max(...rejected);
    const minApproved = Math.min(...approved);
    if (maxRejected < minApproved) {
      return (maxRejected + minApproved) / 2;
    }

    // Overlapping: grid search over candidate thresholds, weighted by trust.
    const allScores = Array.from(new Set([...approved, ...rejected])).sort(
      (a, b) => a - b,
    );
    let bestThreshold = (maxRejected + minApproved) / 2;
    let bestCost = Infinity;

    for (let i = 0; i < allScores.length - 1; i++) {
      const candidate = (allScores[i]! + allScores[i + 1]!) / 2;
      let cost = 0;
      for (const c of all) {
        if (c.decision === "approve" && c.originalScore < candidate) {
          cost += c.trust;
        } else if (c.decision === "reject" && c.originalScore >= candidate) {
          cost += c.trust;
        }
      }
      if (cost < bestCost) {
        bestCost = cost;
        bestThreshold = candidate;
      }
    }
    return bestThreshold;
  }
}
