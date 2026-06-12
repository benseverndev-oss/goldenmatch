/**
 * Approximate functional-dependency VIOLATION detection (cross-column relation).
 * Port of goldencheck/relations/approx_fd.py (the pure-Python fallback; the
 * native kernel is Python-only and produces identical violation sets).
 *
 * Surfaces near-strict FDs and the ROWS that break them: zip -> city holds
 * 99.7%, and the 0.3% are likely data-entry errors. WARNING.
 */
import type { TabularData, Dtype, ColumnValue } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MIN_ROWS = 100;
const MIN_CONFIDENCE = 0.95;
const MIN_AVG_GROUP = 3;
const MAX_CANDIDATES = 12;
const MAX_FINDINGS = 8;
const SUPPORTED: ReadonlySet<Dtype> = new Set<Dtype>(["string", "integer", "boolean"]);

function selectCandidates(data: TabularData): string[] {
  const scored: Array<[number, string]> = [];
  for (const col of data.columns) {
    if (!SUPPORTED.has(data.dtype(col))) continue;
    const nu = data.nUnique(col);
    if (nu <= 1) continue;
    scored.push([nu, col]);
  }
  scored.sort((a, b) => a[0] - b[0]); // low-cardinality first (likely determinants)
  return scored.slice(0, MAX_CANDIDATES).map(([, c]) => c);
}

/** First-seen interning matching the native shim: null -> 0, values -> 1,2,… */
function intern(values: readonly ColumnValue[]): number[] {
  const ids: number[] = new Array(values.length);
  const seen = new Map<ColumnValue, number>();
  let nxt = 1;
  for (let r = 0; r < values.length; r++) {
    const v = values[r]!;
    if (isNullish(v)) {
      ids[r] = 0;
      continue;
    }
    let id = seen.get(v);
    if (id === undefined) {
      id = nxt++;
      seen.set(v, id);
    }
    ids[r] = id;
  }
  return ids;
}

/** Per determinant-group mode dependent, smallest-id tie-break. */
function groupModes(det: number[], dep: number[]): Map<number, number> {
  const counts = new Map<number, Map<number, number>>();
  for (let r = 0; r < det.length; r++) {
    const d = det[r]!;
    const p = dep[r]!;
    let inner = counts.get(d);
    if (!inner) {
      inner = new Map();
      counts.set(d, inner);
    }
    inner.set(p, (inner.get(p) ?? 0) + 1);
  }
  const modes = new Map<number, number>();
  for (const [d, depCounts] of counts) {
    let bestId = -1;
    let bestCnt = -1;
    for (const [pid, c] of depCounts) {
      if (c > bestCnt || (c === bestCnt && (bestId === -1 || pid < bestId))) {
        bestCnt = c;
        bestId = pid;
      }
    }
    modes.set(d, bestId);
  }
  return modes;
}

function violationRows(det: number[], dep: number[]): number[] {
  const modes = groupModes(det, dep);
  const out: number[] = [];
  for (let r = 0; r < det.length; r++) {
    if (modes.get(det[r]!) !== dep[r]!) out.push(r);
  }
  return out;
}

export class ApproximateFDProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < MIN_ROWS || data.columns.length < 2) return [];
    const cols = selectCandidates(data);
    if (cols.length < 2) return [];

    const colsIds = cols.map((c) => intern(data.column(c)));
    const distinct = colsIds.map((c) => new Set(c).size);

    // Discover (det, dep, violationCount) triples above the confidence floor.
    const triples: Array<[number, number, number]> = [];
    for (let i = 0; i < colsIds.length; i++) {
      if (distinct[i] === 0 || distinct[i]! * MIN_AVG_GROUP > nRows) continue;
      for (let j = 0; j < colsIds.length; j++) {
        if (i === j || distinct[j]! <= 1) continue;
        const viol = violationRows(colsIds[i]!, colsIds[j]!).length;
        if (viol === 0) continue;
        if (1.0 - viol / nRows >= MIN_CONFIDENCE) triples.push([i, j, viol]);
      }
    }
    if (triples.length === 0) return [];

    triples.sort((a, b) => a[2] - b[2]); // fewest violations (highest conf) first

    const findings: Finding[] = [];
    for (const [i, j, viol] of triples.slice(0, MAX_FINDINGS)) {
      const det = cols[i]!;
      const dep = cols[j]!;
      const confidence = 1.0 - viol / nRows;
      const rows = violationRows(colsIds[i]!, colsIds[j]!).slice(0, 5);
      const detVals = data.column(det);
      const depVals = data.column(dep);
      const samples = rows.map(
        (r) => `${det}=${JSON.stringify(detVals[r])} has ${dep}=${JSON.stringify(depVals[r])}`,
      );
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: dep,
          check: "fd_violation",
          message:
            `'${dep}' is almost always determined by '${det}' ` +
            `(${(confidence * 100).toFixed(1)}% of rows); ${viol} row(s) break the pattern — ` +
            `likely data-entry errors.`,
          affectedRows: viol,
          sampleValues: samples,
          suggestion:
            `Review the ${viol} row(s) where '${dep}' disagrees with the value ` +
            `'${det}' usually maps to; correct or confirm them.`,
          confidence: 0.7,
          metadata: {
            technique: "fd_violation",
            determinant: det,
            dependent: dep,
            fd_confidence: Math.round(confidence * 1e6) / 1e6,
            violation_count: viol,
          },
        }),
      );
    }
    return findings;
  }
}
