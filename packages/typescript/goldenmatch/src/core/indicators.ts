/**
 * indicators.ts — Auto-config complexity indicators (v1.10).
 *
 * TS port of ``packages/python/goldenmatch/goldenmatch/core/indicators.py``.
 * Pure functions: each takes a row array (and optional config args) and
 * returns a typed result. No controller state, no I/O. Each function has
 * a wall-clock budget; on exhaustion, returns null or a sentinel.
 *
 * The Python implementation operates on polars DataFrames; we mirror the
 * algorithms on plain row-of-object arrays so the module stays edge-safe
 * (no ``node:`` imports, no polars).
 */

import type { Row, GoldenMatchConfig } from "./types.js";
import { getMatchkeys } from "./types.js";
import type {
  ColumnPrior,
  SparsityVerdict,
  CollisionSignal,
} from "./complexityProfile.js";

// ---------------------------------------------------------------------------
// Wall-clock budgets (seconds) — match Python constants.
// ---------------------------------------------------------------------------

export const BUDGET_COLUMN_PRIORS = 5.0;
export const BUDGET_SPARSE_MATCH = 2.0;
export const BUDGET_FULL_POP_HITS = 15.0;
export const BUDGET_CROSS_BLOCKING = 20.0;
export const BUDGET_CORRUPTION = 3.0;
export const BUDGET_COLLISION = 8.0;

// Identity-column name → identity_score floor heuristics. Mirrors
// ``_IDENTITY_NAME_PATTERNS`` in Python.
const IDENTITY_NAME_PATTERNS: ReadonlyArray<readonly [RegExp, number]> = [
  [/^(email|e[-_]?mail|email_addr)$/i, 0.95],
  [/^(ssn|social|tax_id)$/i, 0.95],
  [/^(phone|mobile|tel|telephone)$/i, 0.85],
  [/^(id|uuid|guid|user_id|account_id)$/i, 0.9],
];

function elapsedSec(startMs: number): number {
  return (Date.now() - startMs) / 1000;
}

function userColumns(rows: readonly Row[]): string[] {
  if (rows.length === 0) return [];
  return Object.keys(rows[0] as object);
}

// Lightweight column dtype probe: returns "boolean" / "date" / "other".
// Python's `_NON_IDENTITY_DTYPES` returns identity_score 0 for booleans/dates.
function columnKind(rows: readonly Row[], col: string): "boolean" | "date" | "other" {
  for (const r of rows) {
    const v = (r as Record<string, unknown>)[col];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "boolean") return "boolean";
    if (v instanceof Date) return "date";
    return "other";
  }
  return "other";
}

// ---------------------------------------------------------------------------
// Indicator 1: compute_column_priors
// ---------------------------------------------------------------------------

/** Per-column identity + corruption priors. Returns an empty record for empty data. */
export function computeColumnPriors(
  rows: readonly Row[],
): Record<string, ColumnPrior> {
  const start = Date.now();
  if (rows.length === 0) return {};
  const cols = userColumns(rows);
  const sample = rows.length > 1000 ? rows.slice(0, 1000) : rows;
  const priors: Record<string, ColumnPrior> = {};

  for (const col of cols) {
    if (elapsedSec(start) > BUDGET_COLUMN_PRIORS) {
      for (const remaining of cols) {
        if (!(remaining in priors)) {
          priors[remaining] = { identityScore: 0.0, corruptionScore: 0.0 };
        }
      }
      break;
    }
    const identityScore = computeIdentityScore(rows, col);
    const corruptionScore = computeCorruptionScoreInline(sample, col);
    priors[col] = { identityScore, corruptionScore };
  }
  return priors;
}

function computeIdentityScore(rows: readonly Row[], col: string): number {
  const kind = columnKind(rows, col);
  if (kind === "boolean" || kind === "date") return 0.0;
  for (const [pat, score] of IDENTITY_NAME_PATTERNS) {
    if (pat.test(col)) return score;
  }
  // High-cardinality column = id-like. Python uses polars' ``n_unique``
  // which counts nulls as one distinct value too — mirror that here so the
  // ratio matches for all-null / mostly-null columns.
  const distinct = new Set<unknown>();
  for (const r of rows) {
    const v = (r as Record<string, unknown>)[col];
    // Treat null/undefined/empty-string as a single "null" sentinel.
    distinct.add(v === null || v === undefined || v === "" ? "__NULL__" : v);
  }
  const total = rows.length;
  const cardinalityRatio = distinct.size / Math.max(1, total);
  if (cardinalityRatio > 0.5) return 0.7;
  if (cardinalityRatio > 0.1) return 0.3;
  return 0.0;
}

function computeCorruptionScoreInline(
  sample: readonly Row[],
  col: string,
): number {
  if (BUDGET_CORRUPTION <= 0.0) return 0.0;
  const vals: string[] = [];
  for (const r of sample) {
    const v = (r as Record<string, unknown>)[col];
    if (v === null || v === undefined) {
      vals.push("");
      continue;
    }
    vals.push(String(v));
  }
  if (vals.length === 0) return 0.0;
  const raw = new Set<string>();
  const normalized = new Set<string>();
  for (const v of vals) {
    if (!v) continue;
    raw.add(v);
    normalized.add(v.trim().toLowerCase());
  }
  if (raw.size === 0) return 0.0;
  const ratioClean = normalized.size / raw.size;
  return Math.max(0.0, Math.min(1.0, 1.0 - ratioClean));
}

/** Public per-column corruption score (mirrors ``compute_corruption_score``). */
export function computeCorruptionScore(rows: readonly Row[], col: string): number {
  if (rows.length === 0) return 0.0;
  const cols = userColumns(rows);
  if (!cols.includes(col)) return 0.0;
  const sample = rows.length > 1000 ? rows.slice(0, 1000) : rows;
  return computeCorruptionScoreInline(sample, col);
}

// ---------------------------------------------------------------------------
// Indicator 2: estimate_sparse_match_signal
// ---------------------------------------------------------------------------

export interface SparseMatchOptions {
  readonly exactColumns?: readonly string[];
  readonly sampleSize?: number;
  readonly sparseThreshold?: number;
}

export function estimateSparseMatchSignal(
  rows: readonly Row[],
  options: SparseMatchOptions = {},
): SparsityVerdict {
  const exactColumns = options.exactColumns ?? [];
  const sampleSize = options.sampleSize ?? 1000;
  const sparseThreshold = options.sparseThreshold ?? 50;
  if (exactColumns.length === 0 || rows.length === 0) {
    return { isSparse: true, estimatedNTruePairs: 0 };
  }
  const sample = rows.length > sampleSize ? rows.slice(0, sampleSize) : rows;
  const cols = userColumns(sample);
  const NULL_KEY = "__NULL__";
  let nPairs = 0;
  for (const col of exactColumns) {
    if (!cols.includes(col)) continue;
    const counts = new Map<unknown, number>();
    for (const r of sample) {
      const v = (r as Record<string, unknown>)[col];
      const key = v === null || v === undefined || v === "" ? NULL_KEY : v;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    for (const n of counts.values()) {
      if (n > 1) nPairs += (n * (n - 1)) / 2;
    }
  }
  return { isSparse: nPairs < sparseThreshold, estimatedNTruePairs: nPairs };
}

// ---------------------------------------------------------------------------
// Indicator 3: estimate_full_pop_hits
// ---------------------------------------------------------------------------

export function estimateFullPopHits(
  rows: readonly Row[],
  blockingCol: string,
): number | null {
  if (BUDGET_FULL_POP_HITS <= 0.0) return null;
  const start = Date.now();
  if (rows.length === 0) return 0;
  const cols = userColumns(rows);
  if (!cols.includes(blockingCol)) return 0;
  if (elapsedSec(start) > BUDGET_FULL_POP_HITS) return null;
  try {
    // Python parity: polars ``group_by`` keeps nulls as a single group, so
    // multiple null rows in the blocking column count as a collision group.
    const counts = new Map<unknown, number>();
    const NULL_KEY = "__NULL__";
    for (const r of rows) {
      const raw = (r as Record<string, unknown>)[blockingCol];
      const key = raw === null || raw === undefined || raw === "" ? NULL_KEY : raw;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    if (elapsedSec(start) > BUDGET_FULL_POP_HITS) return null;
    let nPairs = 0;
    for (const n of counts.values()) {
      if (n > 1) nPairs += (n * (n - 1)) / 2;
    }
    return nPairs;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Indicator 4: compute_cross_blocking_overlap
// ---------------------------------------------------------------------------

export function computeCrossBlockingOverlap(
  rows: readonly Row[],
  keyA: string,
  keyB: string,
): number | null {
  if (keyA === keyB) return 1.0;
  if (BUDGET_CROSS_BLOCKING <= 0.0) return null;
  const start = Date.now();
  const cols = userColumns(rows);
  if (!cols.includes(keyA) || !cols.includes(keyB) || rows.length === 0) {
    return null;
  }
  if (elapsedSec(start) > BUDGET_CROSS_BLOCKING) return null;
  try {
    // Python parity: polars ``group_by`` includes nulls as a single group.
    const NULL_KEY = "__NULL__";
    const groupA = new Map<unknown, number[]>();
    const groupB = new Map<unknown, number[]>();
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i] as Record<string, unknown>;
      const va = r[keyA];
      const vb = r[keyB];
      const ka = va === null || va === undefined || va === "" ? NULL_KEY : va;
      const kb = vb === null || vb === undefined || vb === "" ? NULL_KEY : vb;
      {
        const arr = groupA.get(ka);
        if (arr) arr.push(i);
        else groupA.set(ka, [i]);
      }
      {
        const arr = groupB.get(kb);
        if (arr) arr.push(i);
        else groupB.set(kb, [i]);
      }
    }
    if (elapsedSec(start) > BUDGET_CROSS_BLOCKING) return null;
    const pairsSet = (grouped: Map<unknown, number[]>): Set<string> | null => {
      const pairs = new Set<string>();
      for (const arr of grouped.values()) {
        if (arr.length < 2) continue;
        const sorted = arr.slice().sort((a, b) => a - b);
        for (let i = 0; i < sorted.length; i++) {
          for (let j = i + 1; j < sorted.length; j++) {
            pairs.add(`${sorted[i]}|${sorted[j]}`);
            if (elapsedSec(start) > BUDGET_CROSS_BLOCKING) return null;
          }
        }
      }
      return pairs;
    };
    const setA = pairsSet(groupA);
    if (setA === null) return null;
    const setB = pairsSet(groupB);
    if (setB === null) return null;
    const union = new Set<string>(setA);
    for (const p of setB) union.add(p);
    if (union.size === 0) return 1.0;
    let inter = 0;
    for (const p of setA) if (setB.has(p)) inter += 1;
    return inter / union.size;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Indicator 5: compute_identity_collision_signal
// ---------------------------------------------------------------------------

/**
 * Detect whether an identity column is shared across distinct entities.
 *
 * For each multi-record group, compute the max pairwise divergence
 * (1 - similarity) on witnesses, using a token-sort ratio approximation.
 * Returns the fraction of multi-record groups with max-divergence > 0.5.
 */
export function computeIdentityCollisionSignal(
  rows: readonly Row[],
  identityCol: string,
  witnessCols: readonly string[],
): CollisionSignal {
  const start = Date.now();
  if (BUDGET_COLLISION <= 0.0) return { rate: 0.0, witnessUsed: "" };
  if (witnessCols.length === 0 || rows.length === 0) {
    return { rate: 0.0, witnessUsed: "" };
  }
  const cols = userColumns(rows);
  if (!cols.includes(identityCol)) return { rate: 0.0, witnessUsed: "" };
  const valid = witnessCols.filter((c) => cols.includes(c));
  if (valid.length === 0) return { rate: 0.0, witnessUsed: "" };

  try {
    const groups = new Map<unknown, number[]>();
    for (let i = 0; i < rows.length; i++) {
      const v = (rows[i] as Record<string, unknown>)[identityCol];
      if (v === null || v === undefined || v === "") continue;
      const arr = groups.get(v);
      if (arr) arr.push(i);
      else groups.set(v, [i]);
    }
    const multi: number[][] = [];
    for (const arr of groups.values()) if (arr.length > 1) multi.push(arr);
    if (elapsedSec(start) > BUDGET_COLLISION || multi.length === 0) {
      return { rate: 0.0, witnessUsed: "" };
    }

    let nGroups = multi.length;
    let nHighDivergence = 0;
    let winningWitness = "";
    let maxObservedDiv = 0.0;
    for (const groupRows of multi) {
      if (elapsedSec(start) > BUDGET_COLLISION) {
        return { rate: 0.0, witnessUsed: "" };
      }
      let maxDivInGroup = 0.0;
      for (const witness of valid) {
        const vals = groupRows.map((i) => {
          const v = (rows[i] as Record<string, unknown>)[witness];
          return v === null || v === undefined ? "" : String(v);
        });
        for (let i = 0; i < vals.length; i++) {
          for (let j = i + 1; j < vals.length; j++) {
            const sim = tokenSortRatio(vals[i]!, vals[j]!);
            const div = 1.0 - sim;
            if (div > maxDivInGroup) {
              maxDivInGroup = div;
              if (div > maxObservedDiv) {
                maxObservedDiv = div;
                winningWitness = witness;
              }
            }
          }
        }
      }
      if (maxDivInGroup > 0.5) nHighDivergence += 1;
    }
    const rate = nGroups > 0 ? nHighDivergence / nGroups : 0.0;
    return { rate, witnessUsed: winningWitness };
  } catch {
    return { rate: 0.0, witnessUsed: "" };
  }
}

/** Token-sort ratio approximation: sort whitespace-separated tokens, then
 *  compute a Levenshtein-derived similarity (0..1). */
function tokenSortRatio(a: string, b: string): number {
  const ta = a.toLowerCase().trim().split(/\s+/).filter(Boolean).sort().join(" ");
  const tb = b.toLowerCase().trim().split(/\s+/).filter(Boolean).sort().join(" ");
  if (ta === tb) return 1.0;
  if (ta.length === 0 && tb.length === 0) return 1.0;
  const dist = levenshtein(ta, tb);
  const maxLen = Math.max(ta.length, tb.length);
  return maxLen === 0 ? 1.0 : 1.0 - dist / maxLen;
}

function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  const prev = new Array<number>(b.length + 1);
  const curr = new Array<number>(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(curr[j - 1]! + 1, prev[j]! + 1, prev[j - 1]! + cost);
    }
    for (let j = 0; j <= b.length; j++) prev[j] = curr[j]!;
  }
  return prev[b.length]!;
}

// ---------------------------------------------------------------------------
// IndicatorContext — memoization layer (matches Python's class shape)
// ---------------------------------------------------------------------------

/**
 * Context object passed into indicator-aware refit rules. Memoizes each
 * indicator's result so repeated rule lookups in one iteration share work.
 *
 * Mirrors Python ``goldenmatch.core.autoconfig_controller.IndicatorContext``.
 */
export class IndicatorContext {
  readonly rows: readonly Row[];
  readonly config: GoldenMatchConfig;
  private _columnPriors: Record<string, ColumnPrior> | null = null;
  private _sparsity: SparsityVerdict | null = null;
  private _fullPop: Map<string, number | null> = new Map();
  private _crossOverlap: Map<string, number | null> = new Map();
  private _collision: Map<string, CollisionSignal> = new Map();
  private _firedFlags: Set<string> = new Set();

  constructor(rows: readonly Row[], config: GoldenMatchConfig) {
    this.rows = rows;
    this.config = config;
  }

  get columnPriors(): Record<string, ColumnPrior> {
    if (this._columnPriors === null) {
      this._columnPriors = computeColumnPriors(this.rows);
    }
    return this._columnPriors;
  }

  get sparsityVerdict(): SparsityVerdict {
    if (this._sparsity === null) {
      const exactCols: string[] = [];
      for (const mk of getMatchkeys(this.config)) {
        if (mk.type === "exact") {
          for (const f of mk.fields) exactCols.push(f.field);
        }
      }
      this._sparsity = estimateSparseMatchSignal(this.rows, {
        exactColumns: exactCols,
      });
    }
    return this._sparsity;
  }

  fullPopHits(blockingCol: string): number | null {
    if (!this._fullPop.has(blockingCol)) {
      this._fullPop.set(blockingCol, estimateFullPopHits(this.rows, blockingCol));
    }
    return this._fullPop.get(blockingCol) ?? null;
  }

  crossBlockingOverlap(keyA: string, keyB: string): number | null {
    const cacheKey = keyA <= keyB ? `${keyA}|${keyB}` : `${keyB}|${keyA}`;
    if (!this._crossOverlap.has(cacheKey)) {
      this._crossOverlap.set(
        cacheKey,
        computeCrossBlockingOverlap(this.rows, keyA, keyB),
      );
    }
    return this._crossOverlap.get(cacheKey) ?? null;
  }

  collisionSignal(identityCol: string, witnessCols: readonly string[]): CollisionSignal {
    const cacheKey = `${identityCol}|${witnessCols.slice().sort().join(",")}`;
    if (!this._collision.has(cacheKey)) {
      this._collision.set(
        cacheKey,
        computeIdentityCollisionSignal(this.rows, identityCol, witnessCols),
      );
    }
    return this._collision.get(cacheKey) ?? { rate: 0.0, witnessUsed: "" };
  }

  /** Side-channel flag used by ``rule_sparse_match_expand`` to one-shot. */
  hasFired(name: string): boolean {
    return this._firedFlags.has(name);
  }
  markFired(name: string): void {
    this._firedFlags.add(name);
  }

  /** Columns of the backing data (mirrors Python's ``ctx._df.columns``). */
  get columns(): readonly string[] {
    return userColumns(this.rows);
  }
}
