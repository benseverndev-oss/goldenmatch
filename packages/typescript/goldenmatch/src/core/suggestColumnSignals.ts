/**
 * suggestColumnSignals.ts — caller-built `column_signals` for the healer kernel.
 *
 * The Rust `suggest-core` kernel does NOT build the per-column signal batch
 * itself; the caller does (Python's `adapter.py::_build_column_signals_batch`).
 * This is the TS port: one `ColumnSignal` per non-internal data column, with
 * fields the kernel's Rule 2 (scorer swap) / Rule 3 (negative evidence) consume.
 *
 * Edge-safe: no `node:` imports. Built over the existing TS primitives —
 * `computeColumnPriors` (indicators.ts) for identity/corruption, `profileRows`
 * (profiler.ts) for col_type, and direct reductions for cardinality / null /
 * collision.
 *
 * Output keys are snake_case to match the Rust `ColumnSignal` serde shape (so
 * `JSON.stringify(buildColumnSignals(...))` is a valid `column_signals` string).
 */

import type { GoldenMatchConfig, MatchkeyConfig, Row } from "./types.js";
import { getMatchkeys } from "./types.js";
import { computeColumnPriors } from "./indicators.js";
import { profileRows } from "./profiler.js";

/**
 * One per-column signal row. Snake_case keys mirror the Rust `ColumnSignal`
 * struct (`packages/rust/extensions/suggest-core/src/diagnostics.rs`).
 */
export interface ColumnSignal {
  field: string;
  col_type: string;
  scorer: string;
  in_blocking: boolean;
  in_negative_evidence: boolean;
  identity_score: number;
  corruption_score: number;
  collision_rate: number;
  cardinality_ratio: number;
  null_rate: number;
  variant_rate: number;
}

/**
 * Minimal cluster shape the builder needs (a structural subset of
 * `ClusterInfo`, so `[...result.clusters.values()]` is assignable). `members`
 * are row identifiers — `__row_id__` values when the rows carry that column,
 * otherwise positional indices into `rows`.
 */
export interface ColumnSignalCluster {
  readonly members: readonly number[];
  readonly size: number;
  readonly oversized: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Non-internal data columns, in first-seen order (mirrors df column order). */
function dataColumns(rows: readonly Row[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (k.startsWith("__")) continue;
      if (!seen.has(k)) {
        seen.add(k);
        out.push(k);
      }
    }
  }
  return out;
}

/** Project each row onto the given columns (mirrors `df.select(data_cols)`). */
function projectRows(rows: readonly Row[], cols: readonly string[]): Row[] {
  return rows.map((r) => {
    const o: Record<string, unknown> = {};
    for (const c of cols) o[c] = (r as Record<string, unknown>)[c];
    return o;
  });
}

/** Blocking columns from keys + passes + subBlockKeys (mirrors
 *  `collect_blocking_fields`). */
function collectBlockingFields(config: GoldenMatchConfig): Set<string> {
  const out = new Set<string>();
  const bk = config.blocking;
  if (!bk) return out;
  for (const k of bk.keys ?? []) for (const f of k.fields ?? []) out.add(f);
  for (const k of bk.passes ?? []) for (const f of k.fields ?? []) out.add(f);
  for (const k of bk.subBlockKeys ?? []) for (const f of k.fields ?? []) out.add(f);
  return out;
}

/** A polars-`pl.String`-column analog: every non-null value is a JS string. */
function isStringColumn(rows: readonly Row[], col: string): boolean {
  let sawValue = false;
  for (const r of rows) {
    const v = (r as Record<string, unknown>)[col];
    if (v === null || v === undefined) continue;
    sawValue = true;
    if (typeof v !== "string") return false;
  }
  return sawValue;
}

/** polars null == None only — empty string "" is a non-null value. */
function isNull(v: unknown): boolean {
  return v === null || v === undefined;
}

/**
 * Per-column collision rate over multi-member, non-oversized clusters
 * (port of `_collision_rates`): the fraction of such clusters in which the
 * column has ≥2 distinct non-null values among the cluster's member rows. Only
 * string columns are considered (what blockers/scorers actually use); all other
 * columns get 0.0.
 */
function collisionRates(
  rows: readonly Row[],
  clusters: readonly ColumnSignalCluster[],
  stringCols: readonly string[],
): Record<string, number> {
  const multi = clusters.filter((c) => c.size > 1 && !c.oversized);
  if (multi.length === 0 || stringCols.length === 0) return {};

  const hasRowId = rows.length > 0 && "__row_id__" in (rows[0] as object);
  const byRowId = new Map<number, Row>();
  if (hasRowId) {
    for (const r of rows) {
      const id = (r as Record<string, unknown>)["__row_id__"];
      if (typeof id === "number") byRowId.set(id, r);
    }
  }

  const collisionCount: Record<string, number> = {};
  for (const c of stringCols) collisionCount[c] = 0;
  const nMulti = multi.length;

  for (const cluster of multi) {
    const memberIds = cluster.members;
    if (memberIds.length === 0) continue;

    const memberRows: Row[] = [];
    if (hasRowId) {
      for (const m of memberIds) {
        const r = byRowId.get(m);
        if (r !== undefined) memberRows.push(r);
      }
    } else {
      for (const m of memberIds) {
        if (m >= 0 && m < rows.length) memberRows.push(rows[m] as Row);
      }
    }
    if (memberRows.length === 0) continue;

    for (const col of stringCols) {
      const distinct = new Set<string>();
      for (const r of memberRows) {
        const v = (r as Record<string, unknown>)[col];
        if (isNull(v)) continue;
        distinct.add(String(v));
      }
      if (distinct.size >= 2) collisionCount[col] = (collisionCount[col] ?? 0) + 1;
    }
  }

  const out: Record<string, number> = {};
  for (const col of stringCols) out[col] = (collisionCount[col] ?? 0) / nMulti;
  return out;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Build the per-column signals the healer kernel consumes. One `ColumnSignal`
 * per non-internal data column.
 *
 * @param rows     The deduped rows (may carry `__row_id__`; internal `__*`
 *                 columns are excluded from the signal set).
 * @param clusters Cluster summaries from the run (only `members`/`size`/
 *                 `oversized` are read). Pass `[...result.clusters.values()]`.
 * @param config   The resolved config (matchkey scorers + blocking + negative
 *                 evidence drive `scorer`/`in_blocking`/`in_negative_evidence`).
 */
export function buildColumnSignals(
  rows: readonly Row[],
  clusters: readonly ColumnSignalCluster[],
  config: GoldenMatchConfig,
): ColumnSignal[] {
  const dataCols = dataColumns(rows);
  if (dataCols.length === 0) return [];

  const dataRows = projectRows(rows, dataCols);

  // identity_score + corruption_score (parity-validated indicators port).
  const priors = computeColumnPriors(dataRows);

  // col_type via the same classifier autoconfig uses (wasm path follows Python
  // when the autoconfig wasm backend is enabled; pure-TS heuristic otherwise).
  const colTypes: Record<string, string> = {};
  for (const p of profileRows(dataRows).columns) colTypes[p.name] = p.inferredType;

  // Blocking fields, matchkey field→scorer map, negative-evidence set.
  const blockingFields = collectBlockingFields(config);
  const fieldScorer: Record<string, string> = {};
  const neFields = new Set<string>();
  const matchkeys: readonly MatchkeyConfig[] = getMatchkeys(config);
  for (const mk of matchkeys) {
    for (const f of mk.fields) {
      if (f.field && f.scorer) fieldScorer[f.field] = f.scorer;
    }
    for (const ne of mk.negativeEvidence ?? []) neFields.add(ne.field);
  }

  // cardinality_ratio + null_rate (full data, polars semantics: null == None).
  const nRows = Math.max(rows.length, 1);
  const cardinalityRatios: Record<string, number> = {};
  const nullRates: Record<string, number> = {};
  for (const col of dataCols) {
    let nNonNull = 0;
    const distinct = new Set<string>();
    for (const r of rows) {
      const v = (r as Record<string, unknown>)[col];
      if (isNull(v)) continue;
      nNonNull += 1;
      distinct.add(String(v));
    }
    nullRates[col] = 1.0 - nNonNull / nRows;
    cardinalityRatios[col] = nNonNull > 0 ? distinct.size / nNonNull : 0.0;
  }

  // collision_rate over string columns only.
  const stringCols = dataCols.filter((c) => isStringColumn(rows, c));
  const collisions = collisionRates(rows, clusters, stringCols);

  return dataCols.map((col) => {
    const prior = priors[col];
    return {
      field: col,
      col_type: colTypes[col] ?? "string",
      scorer: fieldScorer[col] ?? "",
      in_blocking: blockingFields.has(col),
      in_negative_evidence: neFields.has(col),
      identity_score: prior ? prior.identityScore : 0.0,
      corruption_score: prior ? prior.corruptionScore : 0.0,
      collision_rate: collisions[col] ?? 0.0,
      cardinality_ratio: cardinalityRatios[col] ?? 0.0,
      null_rate: nullRates[col] ?? 0.0,
      variant_rate: 0.0,
    };
  });
}
