/**
 * Profile similarity — score how similar two column profiles are.
 */

import type { ColumnProfile } from "../types.js";

export function profileSimilarity(source: ColumnProfile, target: ColumnProfile): number {
  let score = 0;
  let weights = 0;

  // Type match
  if (source.inferredType === target.inferredType) score += 0.4;
  weights += 0.4;

  // Null percentage similarity
  const nullDiff = Math.abs(source.nullPct - target.nullPct);
  score += 0.2 * Math.max(0, 1 - nullDiff);
  weights += 0.2;

  // Uniqueness similarity
  const uniqueDiff = Math.abs(source.uniquePct - target.uniquePct);
  score += 0.2 * Math.max(0, 1 - uniqueDiff);
  weights += 0.2;

  // Cardinality ratio
  if (source.uniqueCount > 0 && target.uniqueCount > 0) {
    const ratio =
      Math.min(source.uniqueCount, target.uniqueCount) /
      Math.max(source.uniqueCount, target.uniqueCount);
    score += 0.2 * ratio;
  }
  weights += 0.2;

  return weights > 0 ? score / weights : 0;
}
