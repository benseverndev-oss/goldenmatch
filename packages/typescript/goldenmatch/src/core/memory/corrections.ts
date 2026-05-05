/**
 * memory/corrections.ts -- apply pair-level corrections to scored pairs.
 *
 * Edge-safe: no `node:*` imports. Uses the Phase 1.2 Web Crypto-backed hash
 * module. Mirrors `packages/python/goldenmatch/goldenmatch/core/memory/
 * corrections.py:80-200` line-by-line; the function is async because the
 * hash module is async.
 *
 * Algorithm (collision-safe vectorized re-anchor):
 *
 *   1. Empty df / missing `__row_id__` -> return input unchanged with warning.
 *   2. `store.getCorrections({ dataset })`. Empty -> return input unchanged.
 *   3. Build `recordHash -> [rowIds]` map via `computeRecordHashes(df, cols)`
 *      (vectorized) and invert.
 *   4. For each correction:
 *        - Direct row-id match (both ids present in current df) wins.
 *        - Else if `reanchor !== false`, look up the correction's stored
 *          per-side record_hashes against the inverted map. Both sides
 *          resolve uniquely -> re-anchor. Multi-resolve either side ->
 *          `staleAmbiguous`. Empty either side -> `staleUnanchorable`.
 *        - Else (`reanchor === false`, ids gone) -> `staleUnanchorable`.
 *   5. Apply with dual-hash safety: recompute fieldHash + recordHash for the
 *      (possibly re-anchored) row ids. Empty stored hashes short-circuit
 *      (always apply). Match -> clamp 1.0 / 0.0. Mismatch -> keep original
 *      score and bump `stale`.
 */

import type { Row } from "../types.js";
import {
  computeFieldHash,
  computeRecordHash,
  computeRecordHashes,
} from "./hash.js";
import type {
  Correction,
  CorrectionStats,
  MemoryStore,
} from "./types.js";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * Tuple form `[idA, idB, score]` matches the Python source-of-truth
 * `list[tuple[int, int, float]]`. Distinct from the object-shaped
 * `ScoredPair` in `core/types.ts`.
 */
export type ScoredPair = readonly [a: number, b: number, score: number];

/** Optional knobs for `applyCorrections`. */
export interface ApplyOptions {
  readonly dataset?: string | null;
  /** Default true. Set false to disable record_hash re-anchor on missing ids. */
  readonly reanchor?: boolean;
}

const ROW_ID_COL = "__row_id__";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function canonPair(a: number, b: number): readonly [number, number] {
  return a <= b ? [a, b] : [b, a];
}

function pairKey(a: number, b: number): string {
  const [lo, hi] = canonPair(a, b);
  return `${lo}|${hi}`;
}

function emptyStats(totalPairs: number): CorrectionStats {
  return {
    applied: 0,
    stale: 0,
    staleAmbiguous: 0,
    staleUnanchorable: 0,
    stalePairs: [],
    totalPairs,
  };
}

/** Mutable accumulator -- frozen via spread into the readonly result. */
interface MutStats {
  applied: number;
  stale: number;
  staleAmbiguous: number;
  staleUnanchorable: number;
  stalePairs: Array<readonly [number, number]>;
  totalPairs: number;
}

function makeMutStats(totalPairs: number): MutStats {
  return {
    applied: 0,
    stale: 0,
    staleAmbiguous: 0,
    staleUnanchorable: 0,
    stalePairs: [],
    totalPairs,
  };
}

function freezeStats(s: MutStats): CorrectionStats {
  return {
    applied: s.applied,
    stale: s.stale,
    staleAmbiguous: s.staleAmbiguous,
    staleUnanchorable: s.staleUnanchorable,
    stalePairs: s.stalePairs,
    totalPairs: s.totalPairs,
  };
}

// ---------------------------------------------------------------------------
// applyCorrections
// ---------------------------------------------------------------------------

/**
 * Apply stored corrections to scored pairs.
 *
 * Returns `[adjustedPairs, stats]`. The adjusted list preserves order. Stats
 * obey the invariant `applied + stale + staleAmbiguous + staleUnanchorable
 * <= totalPairs` for non-empty inputs.
 */
export async function applyCorrections(
  scoredPairs: ReadonlyArray<ScoredPair>,
  store: MemoryStore,
  df: ReadonlyArray<Row>,
  matchkeyFields: ReadonlyArray<string>,
  opts: ApplyOptions = {},
): Promise<readonly [ScoredPair[], CorrectionStats]> {
  const reanchor = opts.reanchor !== false; // default true
  const dataset = opts.dataset ?? null;

  const stats = makeMutStats(scoredPairs.length);

  // Mutable copy mirrors Python's `adjusted = []` then per-pair push.
  const passthrough = (): [ScoredPair[], CorrectionStats] => [
    scoredPairs.map((p) => [p[0], p[1], p[2]] as ScoredPair),
    freezeStats(stats),
  ];

  // (1) Need at least one row with a __row_id__ column to do anything.
  const cols = df.length === 0 ? [] : Object.keys(df[0]!);
  if (df.length === 0 || !cols.includes(ROW_ID_COL)) {
    if (df.length > 0) {
      console.warn(
        "DataFrame missing __row_id__ column, corrections cannot be applied",
      );
    }
    return passthrough();
  }

  // (2) Single fetch.
  const datasetOpt: { dataset?: string | null } =
    opts.dataset === undefined ? {} : { dataset };
  const allCorrections: Correction[] = await store.getCorrections(datasetOpt);
  if (allCorrections.length === 0) {
    return passthrough();
  }

  // (3) Build record_hash -> [row_ids] map (vectorized) and the set of
  // currently-present row ids.
  const contentCols = cols.filter((c) => c !== ROW_ID_COL);
  const ridToRecordHash = await computeRecordHashes(
    df as ReadonlyArray<Record<string, unknown>>,
    cols,
  );
  const hashToRids = new Map<string, number[]>();
  for (const [rid, h] of ridToRecordHash) {
    const list = hashToRids.get(h);
    if (list === undefined) hashToRids.set(h, [rid]);
    else list.push(rid);
  }
  const currentRids = new Set<number>(ridToRecordHash.keys());

  // (4) Resolve corrections to active (canonical-pair -> Correction) map. The
  //     correction may be re-anchored to a different (idA', idB').
  interface Active {
    readonly correction: Correction;
    readonly resolvedA: number;
    readonly resolvedB: number;
  }
  const active = new Map<string, Active>();

  for (const c of allCorrections) {
    if (currentRids.has(c.idA) && currentRids.has(c.idB)) {
      const [lo, hi] = canonPair(c.idA, c.idB);
      active.set(pairKey(lo, hi), {
        correction: c,
        resolvedA: lo,
        resolvedB: hi,
      });
      continue;
    }
    if (!reanchor) {
      stats.staleUnanchorable += 1;
      stats.stalePairs.push([c.idA, c.idB]);
      continue;
    }
    const rh = c.recordHash || "";
    const colonIdx = rh.indexOf(":");
    if (colonIdx === -1) {
      stats.staleUnanchorable += 1;
      stats.stalePairs.push([c.idA, c.idB]);
      continue;
    }
    const ha = rh.slice(0, colonIdx);
    const hb = rh.slice(colonIdx + 1);
    const candsA = ha ? hashToRids.get(ha) ?? [] : [];
    const candsB = hb ? hashToRids.get(hb) ?? [] : [];
    if (candsA.length === 1 && candsB.length === 1) {
      const [lo, hi] = canonPair(candsA[0]!, candsB[0]!);
      active.set(pairKey(lo, hi), {
        correction: c,
        resolvedA: lo,
        resolvedB: hi,
      });
    } else if (candsA.length > 0 && candsB.length > 0) {
      stats.staleAmbiguous += 1;
      stats.stalePairs.push([c.idA, c.idB]);
    } else {
      stats.staleUnanchorable += 1;
      stats.stalePairs.push([c.idA, c.idB]);
    }
  }

  if (active.size === 0) {
    return passthrough();
  }

  // (5) Apply with dual-hash safety.
  // Pre-build rowId -> Row index for field lookup, plus per-rid record-hash
  // cache (already computed above).
  const rowById = new Map<number, Row>();
  for (const r of df) {
    const raw = r[ROW_ID_COL];
    if (typeof raw === "number") rowById.set(raw, r);
  }

  // Pre-compute record_hash for each resolved row id (re-uses the existing
  // map -- we already have every row's record_hash).
  const recordHashByRid = ridToRecordHash;

  // Pre-compute field tuples for each resolved row id.
  const availableFields = matchkeyFields.filter((f) => cols.includes(f));
  const fieldVals = new Map<number, unknown[]>();
  for (const a of active.values()) {
    for (const rid of [a.resolvedA, a.resolvedB]) {
      if (!fieldVals.has(rid)) {
        const row = rowById.get(rid);
        if (row !== undefined) {
          fieldVals.set(
            rid,
            availableFields.map((f) => row[f]),
          );
        }
      }
    }
  }

  const adjusted: ScoredPair[] = [];
  for (const pair of scoredPairs) {
    const [idA, idB, score] = pair;
    const key = pairKey(idA, idB);
    const hit = active.get(key);
    if (hit === undefined) {
      adjusted.push([idA, idB, score]);
      continue;
    }
    const c = hit.correction;
    const valsA = fieldVals.get(hit.resolvedA);
    const valsB = fieldVals.get(hit.resolvedB);
    if (valsA === undefined || valsB === undefined) {
      console.warn(
        `Row ID(s) not in lookup for correction (${idA}, ${idB}), marking stale`,
      );
      adjusted.push([idA, idB, score]);
      stats.stale += 1;
      stats.stalePairs.push([idA, idB]);
      continue;
    }
    const currFh = await computeFieldHash(valsA, valsB);
    const [loRid, hiRid] = canonPair(hit.resolvedA, hit.resolvedB);
    const currRh = `${recordHashByRid.get(loRid) ?? ""}:${recordHashByRid.get(hiRid) ?? ""}`;

    const hashesEmpty = !c.fieldHash && !c.recordHash;
    const hashesMatch = currFh === c.fieldHash && currRh === c.recordHash;

    if (hashesEmpty || hashesMatch) {
      const newScore = c.decision === "approve" ? 1.0 : 0.0;
      adjusted.push([idA, idB, newScore]);
      stats.applied += 1;
    } else {
      adjusted.push([idA, idB, score]);
      stats.stale += 1;
      stats.stalePairs.push([idA, idB]);
    }
  }

  // Suppress unused-binding warning when contentCols isn't read directly --
  // it's implicit in computeRecordHashes(cols).
  void contentCols;

  return [adjusted, freezeStats(stats)];
}
