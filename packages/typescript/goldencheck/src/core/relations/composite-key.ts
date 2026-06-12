/**
 * Composite-key discovery (cross-column relation).
 * Port of goldencheck/relations/composite_key.py (the pure-Polars fallback;
 * the native kernel is Python-only and parity-validated identical).
 *
 * Finds minimal column subsets (size 2..MAX_KEY_SIZE) whose tuples are all
 * distinct, but only when NO single-column key exists. Reported INFO.
 */
import type { TabularData, Dtype } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MAX_KEY_SIZE = 3;
const MAX_CANDIDATE_COLS = 12;
const MAX_REPORTED_KEYS = 3;
const SUPPORTED: ReadonlySet<Dtype> = new Set<Dtype>([
  "string", "integer", "float", "boolean",
]);

function hasSingleColumnKey(data: TabularData, nRows: number): boolean {
  for (const col of data.columns) {
    if (data.nullCount(col) === 0 && data.nUnique(col) === nRows) return true;
  }
  return false;
}

function selectCandidates(data: TabularData): string[] {
  const scored: Array<[number, string]> = [];
  for (const col of data.columns) {
    if (!SUPPORTED.has(data.dtype(col))) continue;
    const nu = data.nUnique(col);
    if (nu <= 1) continue;
    scored.push([nu, col]);
  }
  // Highest cardinality first — most likely to complete a key; stable on ties.
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, MAX_CANDIDATE_COLS).map(([, c]) => c);
}

/** BFS mirror of goldencheck_core::composite_key_search. */
function search(
  data: TabularData,
  candidates: string[],
  nRows: number,
  maxSize: number,
): number[][] {
  const idxs = candidates.map((_, i) => i);
  const found: number[][] = [];
  const cap = Math.min(maxSize, idxs.length);
  let frontier: number[][] = idxs.map((i) => [i]);
  for (let size = 2; size <= cap; size++) {
    const next: number[][] = [];
    for (const base of frontier) {
      const last = base[base.length - 1]!;
      for (const c of idxs) {
        if (c <= last) continue;
        const subset = [...base, c];
        // Prune supersets of an already-found key.
        if (found.some((k) => k.every((x) => subset.includes(x)))) continue;
        const cols = subset.map((j) => candidates[j]!);
        if (data.nUniqueTuple(cols) === nRows) found.push(subset);
        else next.push(subset);
      }
    }
    if (next.length === 0) break;
    frontier = next;
  }
  return found;
}

export class CompositeKeyProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < 2 || data.columns.length < 2) return [];
    if (hasSingleColumnKey(data, nRows)) return [];

    const candidates = selectCandidates(data);
    if (candidates.length < 2) return [];

    const keysIdx = search(data, candidates, nRows, MAX_KEY_SIZE);
    if (keysIdx.length === 0) return [];

    // Smallest keys first, then deterministic (lexicographic by column names).
    const keys = keysIdx.map((idxs) => idxs.map((i) => candidates[i]!));
    keys.sort((a, b) => {
      if (a.length !== b.length) return a.length - b.length;
      const len = Math.min(a.length, b.length);
      for (let k = 0; k < len; k++) {
        if (a[k] !== b[k]) return a[k]! < b[k]! ? -1 : 1;
      }
      return 0;
    });

    const findings: Finding[] = [];
    for (const key of keys.slice(0, MAX_REPORTED_KEYS)) {
      const colsStr = key.join(", ");
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column: key[0]!, // anchor on first key column
          check: "composite_key",
          message:
            `Columns (${colsStr}) form a composite key — together they ` +
            `uniquely identify every row, and no single column does.`,
          affectedRows: nRows,
          suggestion:
            "Use this column set as the natural join/dedup key, or add a " +
            "stable single-column surrogate key (UUID / autoincrement).",
          confidence: 0.6,
          metadata: { technique: "composite_key", key_columns: key },
        }),
      );
    }
    return findings;
  }
}
