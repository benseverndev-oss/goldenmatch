/**
 * Confidence prior builder — build and apply Bayesian priors from findings.
 * TypeScript port of goldencheck/baseline/priors.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { Finding } from "../types.js";
import type { ConfidencePrior } from "./models.js";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Build confidence priors from a list of findings.
 *
 * Groups findings by (check, column), averages the confidence per group,
 * and returns a nested dict `{check: {column: ConfidencePrior}}`.
 *
 * @param findings - List of Finding instances from a validation run.
 * @param rowCount - Number of rows in the dataset (used as evidenceCount for all priors).
 * @returns Nested dict of priors, or an empty object if findings is empty.
 */
export function buildPriors(
  findings: readonly Finding[],
  rowCount: number,
): Record<string, Record<string, ConfidencePrior>> {
  if (findings.length === 0) return {};

  // Accumulate (sum, count) per (check, column)
  const sums = new Map<string, number>();
  const counts = new Map<string, number>();

  for (const f of findings) {
    const key = `${f.check}\0${f.column}`;
    sums.set(key, (sums.get(key) ?? 0) + f.confidence);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const result: Record<string, Record<string, ConfidencePrior>> = {};

  for (const [key, total] of sums) {
    const [check, column] = key.split("\0") as [string, string];
    const avgConfidence = total / counts.get(key)!;

    if (!(check in result)) {
      result[check] = {};
    }
    result[check]![column] = {
      confidence: avgConfidence,
      evidenceCount: rowCount,
    };
  }

  return result;
}

/**
 * Adjust a raw confidence value toward a prior using Bayesian blending.
 *
 * Formula:
 *   prior_weight = min(prior.evidenceCount / 100, 1.0)
 *   adjusted = (raw * 1.0 + prior.confidence * prior_weight) / (1.0 + prior_weight)
 *
 * The result is clamped to [0, 1] and rounded to 4 decimal places.
 *
 * @param rawConfidence - Raw confidence from the current check.
 * @param prior - The ConfidencePrior to blend toward.
 * @returns Adjusted confidence, clamped to [0, 1] and rounded to 4 d.p.
 */
export function applyPrior(rawConfidence: number, prior: ConfidencePrior): number {
  const evidenceWeight = 1.0;
  const priorWeight = Math.min(prior.evidenceCount / 100, 1.0);

  const adjusted =
    (rawConfidence * evidenceWeight + prior.confidence * priorWeight) /
    (evidenceWeight + priorWeight);

  const clamped = Math.max(0.0, Math.min(1.0, adjusted));
  return Math.round(clamped * 10000) / 10000;
}
