/**
 * Correlation analyzer — Pearson (numeric-numeric) and Cramer's V (categorical-categorical).
 * TypeScript port of goldencheck/baseline/correlation.py.
 * Edge-safe: uses only stats.ts utilities, no external dependencies.
 */

import type { TabularData } from "../data.js";
import type { CorrelationEntry } from "./models.js";
import { pearson, cramersV } from "../stats.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Threshold for "strong" correlation. */
const STRONG_THRESHOLD = 0.7;

/** Threshold for "moderate" correlation. */
const MODERATE_THRESHOLD = 0.4;

/** Maximum number of column pairs to evaluate. */
const MAX_PAIRS = 500;

/** Minimum non-null rows required per pair. */
const MIN_ROWS = 30;

/** Maximum unique values for a string column to be treated as categorical. */
const MAX_CAT_UNIQUE = 100;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function strength(value: number): "strong" | "moderate" | "weak" {
  const abs = Math.abs(value);
  if (abs >= STRONG_THRESHOLD) return "strong";
  if (abs >= MODERATE_THRESHOLD) return "moderate";
  return "weak";
}

function pearsonEntry(
  data: TabularData,
  colA: string,
  colB: string,
): CorrelationEntry | null {
  // Get paired non-null numeric values
  const aValues: number[] = [];
  const bValues: number[] = [];

  for (const row of data.rows) {
    const aRaw = row[colA];
    const bRaw = row[colB];
    if (aRaw === null || aRaw === undefined || bRaw === null || bRaw === undefined) continue;
    const a = Number(aRaw);
    const b = Number(bRaw);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    aValues.push(a);
    bValues.push(b);
  }

  if (aValues.length < MIN_ROWS) return null;

  const corr = pearson(aValues, bValues);
  if (corr === null) return null;

  const s = strength(corr);
  if (s === "weak") return null;

  return {
    col1: colA < colB ? colA : colB,
    col2: colA < colB ? colB : colA,
    method: "pearson",
    value: Math.round(corr * 1e6) / 1e6,
    strength: s,
  };
}

function cramersVEntry(
  data: TabularData,
  colA: string,
  colB: string,
): CorrelationEntry | null {
  // Build contingency table
  const contingency = new Map<string, Map<string, number>>();
  let validCount = 0;

  for (const row of data.rows) {
    const aRaw = row[colA];
    const bRaw = row[colB];
    if (aRaw === null || aRaw === undefined || bRaw === null || bRaw === undefined) continue;
    const a = String(aRaw);
    const b = String(bRaw);

    let bMap = contingency.get(a);
    if (!bMap) {
      bMap = new Map<string, number>();
      contingency.set(a, bMap);
    }
    bMap.set(b, (bMap.get(b) ?? 0) + 1);
    validCount++;
  }

  if (validCount < MIN_ROWS) return null;

  const v = cramersV(contingency);
  if (v === null) return null;

  const s = strength(v);
  if (s === "weak") return null;

  return {
    col1: colA < colB ? colA : colB,
    col2: colA < colB ? colB : colA,
    method: "cramers_v",
    value: Math.round(v * 1e6) / 1e6,
    strength: s,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Analyze pairwise correlations in the data and return reportable entries.
 *
 * - Numeric pairs: Pearson correlation.
 * - Categorical pairs: Cramer's V.
 * - Only moderate (>= 0.4) or strong (>= 0.7) correlations are reported.
 * - At most MAX_PAIRS (500) column pairs are evaluated.
 * - String columns must have nUnique < MAX_CAT_UNIQUE (100) to qualify as categorical.
 * - Each pair must have at least MIN_ROWS (30) non-null rows.
 */
export function analyzeCorrelations(data: TabularData): CorrelationEntry[] {
  // Partition columns by type
  const numericCols: string[] = [];
  const categoricalCols: string[] = [];

  for (const col of data.columns) {
    const dt = data.dtype(col);
    if (dt === "integer" || dt === "float") {
      numericCols.push(col);
    } else if (dt === "string") {
      if (data.nUnique(col) < MAX_CAT_UNIQUE) {
        categoricalCols.push(col);
      }
    }
  }

  const results: CorrelationEntry[] = [];
  let pairsEvaluated = 0;

  // --- Numeric-numeric pairs ---
  for (let i = 0; i < numericCols.length && pairsEvaluated < MAX_PAIRS; i++) {
    for (let j = i + 1; j < numericCols.length && pairsEvaluated < MAX_PAIRS; j++) {
      pairsEvaluated++;
      const entry = pearsonEntry(data, numericCols[i]!, numericCols[j]!);
      if (entry !== null) {
        results.push(entry);
      }
    }
  }

  // --- Categorical-categorical pairs ---
  for (let i = 0; i < categoricalCols.length && pairsEvaluated < MAX_PAIRS; i++) {
    for (let j = i + 1; j < categoricalCols.length && pairsEvaluated < MAX_PAIRS; j++) {
      pairsEvaluated++;
      const entry = cramersVEntry(data, categoricalCols[i]!, categoricalCols[j]!);
      if (entry !== null) {
        results.push(entry);
      }
    }
  }

  return results;
}
