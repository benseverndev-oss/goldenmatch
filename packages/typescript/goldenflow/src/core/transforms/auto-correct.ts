/**
 * Auto-correct transform — ported from goldenflow/transforms/auto_correct.py
 * Side-effect module: registers the category_auto_correct transform on import.
 *
 * Uses Levenshtein-based fuzzy matching to correct low-frequency categorical
 * values to their most likely canonical form.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Levenshtein distance + fuzzy ratio
// ---------------------------------------------------------------------------

function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;

  if (m === 0) return n;
  if (n === 0) return m;

  // Use single-row DP for space efficiency
  const prev = new Array<number>(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;

  for (let i = 1; i <= m; i++) {
    let prevDiag = prev[0]!;
    prev[0] = i;
    for (let j = 1; j <= n; j++) {
      const temp = prev[j]!;
      if (a[i - 1] === b[j - 1]) {
        prev[j] = prevDiag;
      } else {
        prev[j] = 1 + Math.min(prevDiag, prev[j]!, prev[j - 1]!);
      }
      prevDiag = temp;
    }
  }

  return prev[n]!;
}

/**
 * Fuzzy similarity ratio between two strings (0-100).
 * 100 means identical, 0 means completely different.
 */
function fuzzyRatio(a: string, b: string): number {
  if (a.length === 0 && b.length === 0) return 100;
  const maxLen = Math.max(a.length, b.length);
  const dist = levenshtein(a, b);
  return 100 * (1 - dist / maxLen);
}

// ---------------------------------------------------------------------------
// category_auto_correct (series, string, 35, auto_apply)
// ---------------------------------------------------------------------------

function categoryAutoCorrect(
  values: readonly ColumnValue[],
  frequencyThreshold: unknown = 0.05,
  matchThreshold: unknown = 85,
): ColumnValue[] {
  const freqThresh =
    typeof frequencyThreshold === "number"
      ? frequencyThreshold
      : Number(frequencyThreshold) || 0.05;
  const matchThresh =
    typeof matchThreshold === "number"
      ? matchThreshold
      : Number(matchThreshold) || 85;

  // 1. Count case-insensitive frequencies
  const freqMap = new Map<string, number>(); // lowercase -> count
  const casingMap = new Map<string, Map<string, number>>(); // lowercase -> (original -> count)
  let totalNonNull = 0;

  for (const v of values) {
    if (v === null || typeof v !== "string") continue;
    const lower = v.toLowerCase();
    totalNonNull++;
    freqMap.set(lower, (freqMap.get(lower) ?? 0) + 1);

    let casings = casingMap.get(lower);
    if (!casings) {
      casings = new Map<string, number>();
      casingMap.set(lower, casings);
    }
    casings.set(v, (casings.get(v) ?? 0) + 1);
  }

  if (totalNonNull === 0) return values.slice();

  // 2. Determine canonical candidates: values above frequency threshold
  //    Use the most common casing for each canonical
  const canonicals = new Map<string, string>(); // lowercase -> best casing

  for (const [lower, count] of freqMap) {
    if (count / totalNonNull >= freqThresh) {
      // Pick the most common casing
      const casings = casingMap.get(lower)!;
      let bestCasing = lower;
      let bestCount = 0;
      for (const [original, c] of casings) {
        if (c > bestCount) {
          bestCount = c;
          bestCasing = original;
        }
      }
      canonicals.set(lower, bestCasing);
    }
  }

  if (canonicals.size === 0) return values.slice();

  // 3. Build correction map for low-frequency values
  const corrections = new Map<string, string>(); // lowercase -> canonical

  for (const [lower] of freqMap) {
    if (canonicals.has(lower)) continue; // Already canonical

    let bestCanonical: string | null = null;
    let bestScore = 0;

    for (const [canonLower, canonOriginal] of canonicals) {
      const score = fuzzyRatio(lower, canonLower);
      if (score >= matchThresh && score > bestScore) {
        bestScore = score;
        bestCanonical = canonOriginal;
      }
    }

    if (bestCanonical !== null) {
      corrections.set(lower, bestCanonical);
    }
  }

  // 4. Apply corrections
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const lower = v.toLowerCase();
    const correction = corrections.get(lower);
    if (correction !== undefined) return correction;
    // If it's a canonical, normalize to best casing
    const canonical = canonicals.get(lower);
    if (canonical !== undefined) return canonical;
    return v;
  });
}

registerTransform(
  { name: "category_auto_correct", inputTypes: ["string"], autoApply: true, priority: 35, mode: "series" },
  categoryAutoCorrect,
);
