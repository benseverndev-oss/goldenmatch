/**
 * incremental.ts — Match new records against an existing base dataset.
 * Edge-safe: no Node.js imports, pure TypeScript only.
 *
 * Ports goldenmatch/core/incremental.py::run_incremental.
 *
 * The base and new populations get non-overlapping `__row_id__` ranges: base
 * rows are numbered 0..baseHeight-1, new rows are offset above the base max so
 * the two never collide. Only cross-source pairs (one new, one base) are
 * returned; new-vs-new pairs are dropped.
 *
 * CRITICAL — exact vs fuzzy split (mirrors Python): `matchOne` returns `[]`
 * for EXACT matchkeys (they carry no fuzzy threshold), so exact matchkeys are
 * resolved SEPARATELY via `findExactMatches` (a hash equijoin over the combined
 * frame) and only fuzzy matchkeys go through `matchOne`. A naive "call matchOne
 * for every matchkey" would silently drop every exact match.
 */

import type { Row, GoldenMatchConfig, MatchkeyConfig } from "./types.js";
import { getMatchkeys } from "./types.js";
import { addRowIds, addSourceColumn, computeMatchkeys } from "./matchkey.js";
import { autoFixRows } from "./autofix.js";
import { applyStandardization } from "./standardize.js";
import { findExactMatches } from "./scorer.js";
import { matchOne } from "./match-one.js";

// ---------------------------------------------------------------------------
// Types (snake_case wire shape — Python parity)
// ---------------------------------------------------------------------------

export interface IncrementalMatch {
  readonly new_row_id: number;
  readonly base_row_id: number;
  readonly score: number;
}

export interface IncrementalResult {
  readonly base_records: number;
  readonly new_records: number;
  readonly matched_to_base: number;
  readonly new_entities: number;
  readonly total_pairs: number;
  readonly matches: readonly IncrementalMatch[];
}

/** Mirror Python round(x, 4). */
function round4(x: number): number {
  return Math.round(x * 1e4) / 1e4;
}

/**
 * Apply a threshold override to every matchkey that carries a threshold.
 *
 * Mirrors Python: `for mk in matchkeys: if mk.threshold is not None:
 * mk.threshold = threshold`. Exact matchkeys with no threshold are untouched.
 */
function overrideThreshold(
  matchkeys: readonly MatchkeyConfig[],
  threshold: number | undefined,
): MatchkeyConfig[] {
  if (threshold === undefined) return [...matchkeys];
  return matchkeys.map((mk) =>
    (mk as { threshold?: number }).threshold !== undefined
      ? ({ ...mk, threshold } as MatchkeyConfig)
      : mk,
  );
}

// ---------------------------------------------------------------------------
// runIncremental
// ---------------------------------------------------------------------------

/**
 * Match records in `newRows` against the existing `baseRows`.
 *
 * Returns the matched (new_row_id, base_row_id, score) pairs plus summary
 * counts. Only cross-source pairs are kept; new-vs-new pairs are dropped.
 */
export function runIncremental(
  baseRows: readonly Row[],
  newRows: readonly Row[],
  config: GoldenMatchConfig,
  threshold?: number,
): IncrementalResult {
  const matchkeys = overrideThreshold(getMatchkeys(config), threshold);

  // Stamp base rows: __row_id__ 0..baseHeight-1, __source__="base".
  const base = autoFixRows(
    addSourceColumn(addRowIds(baseRows, 0), "base"),
  ).rows;
  const baseMaxId = baseRows.length;

  // Stamp new rows: __row_id__ offset above the base max, __source__="new".
  const newStamped = autoFixRows(
    addSourceColumn(addRowIds(newRows, baseMaxId), "new"),
  ).rows;

  // Standardize + compute matchkeys on the combined frame.
  let combined: Row[] = [...base, ...newStamped];
  if (config.standardization) {
    combined = applyStandardization(combined, config.standardization.rules);
  }
  combined = computeMatchkeys(combined, matchkeys);

  const newIds = new Set<number>();
  for (let i = 0; i < newRows.length; i++) newIds.add(baseMaxId + i);

  const exactMks = matchkeys.filter((mk) => mk.type === "exact");
  const fuzzyMks = matchkeys.filter((mk) => mk.type !== "exact");

  const allMatches: Array<[number, number, number]> = [];

  // Exact matchkeys via the hash equijoin (matchOne can't do exact).
  for (const mk of exactMks) {
    for (const pair of findExactMatches(combined, mk)) {
      const aNew = newIds.has(pair.idA);
      const bNew = newIds.has(pair.idB);
      // Keep only cross-source pairs (one new, one base).
      if (aNew !== bNew) {
        const newId = aNew ? pair.idA : pair.idB;
        const baseId = aNew ? pair.idB : pair.idA;
        allMatches.push([newId, baseId, pair.score]);
      }
    }
  }

  // Fuzzy matchkeys via matchOne, per new record.
  if (fuzzyMks.length > 0) {
    const rowIndex = new Map<number, Row>();
    for (const row of combined) rowIndex.set(row["__row_id__"] as number, row);
    const sortedNewIds = [...newIds].sort((a, b) => a - b);
    for (const newId of sortedNewIds) {
      const row = rowIndex.get(newId);
      if (row === undefined) continue;
      for (const mk of fuzzyMks) {
        for (const hit of matchOne(row, combined, mk)) {
          // Drop self + new-vs-new (only base rows are outside newIds).
          if (!newIds.has(hit.rowId)) {
            allMatches.push([newId, hit.rowId, hit.score]);
          }
        }
      }
    }
  }

  // Deduplicate: keep the best score per (new_id, base_id) pair.
  const best = new Map<string, number>();
  for (const [newId, baseId, score] of allMatches) {
    const key = `${newId}:${baseId}`;
    const prev = best.get(key);
    if (prev === undefined || score > prev) best.set(key, score);
  }

  const matches: IncrementalMatch[] = [];
  const matchedNewIds = new Set<number>();
  for (const [key, score] of best) {
    const sep = key.indexOf(":");
    const newId = Number(key.slice(0, sep));
    const baseId = Number(key.slice(sep + 1));
    matches.push({
      new_row_id: newId,
      base_row_id: baseId,
      score: round4(score),
    });
    matchedNewIds.add(newId);
  }

  return {
    base_records: baseRows.length,
    new_records: newRows.length,
    matched_to_base: matchedNewIds.size,
    new_entities: newIds.size - matchedNewIds.size,
    total_pairs: matches.length,
    matches,
  };
}
