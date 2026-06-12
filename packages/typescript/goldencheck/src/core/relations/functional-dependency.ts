/**
 * Strict functional-dependency discovery (cross-column relation).
 * Port of goldencheck/relations/functional_dependency.py (the pure-Polars
 * fallback; the native kernel is Python-only and integer-exact-identical).
 *
 * det -> dep holds iff n_distinct(det, dep) === n_distinct(det). Skips unique
 * determinants and constant dependents. Merged by determinant; reported INFO.
 */
import type { TabularData, Dtype } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MIN_ROWS = 50;
const MAX_CANDIDATES = 12;
const MAX_FINDINGS = 10;
const SUPPORTED: ReadonlySet<Dtype> = new Set<Dtype>(["string", "integer", "boolean"]);

function selectCandidates(data: TabularData): string[] {
  const scored: Array<[number, string]> = [];
  for (const col of data.columns) {
    if (!SUPPORTED.has(data.dtype(col))) continue;
    const nu = data.nUnique(col);
    if (nu <= 1) continue;
    scored.push([nu, col]);
  }
  // Lowest-cardinality first (interesting determinants); stable on ties.
  scored.sort((a, b) => a[0] - b[0]);
  return scored.slice(0, MAX_CANDIDATES).map(([, c]) => c);
}

export class FunctionalDependencyProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const nRows = data.rowCount;
    if (nRows < MIN_ROWS || data.columns.length < 2) return [];

    const cols = selectCandidates(data);
    if (cols.length < 2) return [];

    const distinct = new Map<string, number>();
    for (const c of cols) distinct.set(c, data.nUnique(c));

    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < cols.length; i++) {
      const det = cols[i]!;
      if (distinct.get(det) === nRows) continue; // unique determinant → trivial
      for (let j = 0; j < cols.length; j++) {
        if (i === j) continue;
        const dep = cols[j]!;
        if (distinct.get(dep)! <= 1) continue;
        if (data.nUniqueTuple([det, dep]) === distinct.get(det)) {
          pairs.push([i, j]);
        }
      }
    }
    if (pairs.length === 0) return [];

    // Merge by determinant (A->B and A->C become one finding).
    const detToDeps = new Map<string, string[]>();
    for (const [i, j] of pairs) {
      const det = cols[i]!;
      let deps = detToDeps.get(det);
      if (!deps) {
        deps = [];
        detToDeps.set(det, deps);
      }
      deps.push(cols[j]!);
    }

    // Sort determinants by (deps count, name) descending — mirror Python's
    // sorted(..., key=lambda d: (len(deps), d), reverse=True).
    const dets = [...detToDeps.keys()].sort((a, b) => {
      const la = detToDeps.get(a)!.length;
      const lb = detToDeps.get(b)!.length;
      if (la !== lb) return lb - la;
      return a < b ? 1 : a > b ? -1 : 0;
    });

    const findings: Finding[] = [];
    for (const det of dets) {
      const deps = [...detToDeps.get(det)!].sort();
      const depsStr = deps.join(", ");
      const many = deps.length > 1;
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column: det,
          check: "functional_dependency",
          message:
            `Column '${det}' determines (${depsStr}) — each '${det}' value maps ` +
            `to a single value of ${many ? "these columns" : "this column"}, ` +
            `so ${many ? "they are" : "it is"} redundant given '${det}'.`,
          affectedRows: nRows,
          suggestion:
            "If this is a lookup relationship, consider normalizing " +
            `(${depsStr} into a table keyed by '${det}') to remove redundancy.`,
          confidence: 0.55,
          metadata: { technique: "functional_dependency", determinant: det, dependents: deps },
        }),
      );
      if (findings.length >= MAX_FINDINGS) break;
    }
    return findings;
  }
}
