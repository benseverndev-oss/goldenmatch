/**
 * Null correlation profiler — detects columns whose null patterns correlate.
 * Port of goldencheck/relations/null_correlation.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const DEFAULT_THRESHOLD = 0.90;
const HIGH_THRESHOLD = 0.95;
const MAX_GROUPS = 3;

class UnionFind {
  parent: Map<string, string>;

  constructor(elements: readonly string[]) {
    this.parent = new Map();
    for (const e of elements) this.parent.set(e, e);
  }

  find(x: string): string {
    let root = x;
    while (this.parent.get(root) !== root) {
      const p = this.parent.get(root)!;
      this.parent.set(root, this.parent.get(p)!);
      root = p;
    }
    return root;
  }

  union(x: string, y: string): void {
    const rx = this.find(x);
    const ry = this.find(y);
    if (rx !== ry) this.parent.set(ry, rx);
  }

  groups(): string[][] {
    const buckets = new Map<string, string[]>();
    for (const e of this.parent.keys()) {
      const root = this.find(e);
      let group = buckets.get(root);
      if (!group) {
        group = [];
        buckets.set(root, group);
      }
      group.push(e);
    }
    return [...buckets.values()];
  }
}

export class NullCorrelationProfiler implements RelationProfiler {
  private threshold: number;

  constructor(threshold: number = DEFAULT_THRESHOLD) {
    this.threshold = threshold;
  }

  profile(data: TabularData): Finding[] {
    const findings: Finding[] = [];
    const columns = data.columns;
    const nRows = data.rowCount;

    if (nRows === 0 || columns.length < 2) return findings;

    // Pre-compute null masks
    const nullMasks = new Map<string, boolean[]>();
    const nullCounts = new Map<string, number>();
    for (const col of columns) {
      const vals = data.column(col);
      const mask = vals.map((v) => isNullish(v));
      nullMasks.set(col, mask);
      nullCounts.set(col, mask.filter(Boolean).length);
    }

    // Find correlated pairs
    const highPairs: Array<[string, string]> = [];
    const lowPairs: Array<[string, string]> = [];

    for (let i = 0; i < columns.length; i++) {
      for (let j = i + 1; j < columns.length; j++) {
        const colA = columns[i]!;
        const colB = columns[j]!;
        const ncA = nullCounts.get(colA)!;
        const ncB = nullCounts.get(colB)!;

        if (ncA === 0 && ncB === 0) continue;
        if (ncA / nRows <= 0.05 && ncB / nRows <= 0.05) continue;

        const maskA = nullMasks.get(colA)!;
        const maskB = nullMasks.get(colB)!;

        let agreement = 0;
        for (let r = 0; r < nRows; r++) {
          if (maskA[r] === maskB[r]) agreement++;
        }
        const correlation = agreement / nRows;

        if (correlation >= HIGH_THRESHOLD) {
          highPairs.push([colA, colB]);
        } else if (correlation >= this.threshold) {
          lowPairs.push([colA, colB]);
        }
      }
    }

    const allGroupFindings: Finding[] = [];

    // Group high-threshold pairs
    if (highPairs.length > 0) {
      const uf = new UnionFind(columns);
      for (const [a, b] of highPairs) uf.union(a, b);

      for (const group of uf.groups()) {
        if (group.length < 2) continue;
        const sorted = [...group].sort();
        const groupStr = sorted.map((c) => `'${c}'`).join(", ");
        const totalNulls = sorted.reduce((m, c) => Math.max(m, nullCounts.get(c) ?? 0), 0);
        const confidence = group.length >= 3 ? 0.8 : 0.5;

        allGroupFindings.push(
          makeFinding({
            severity: Severity.INFO,
            column: sorted.join(","),
            check: "null_correlation",
            message: `Columns ${groupStr} have strongly correlated null patterns (>= ${Math.round(HIGH_THRESHOLD * 100)}% agreement). They may represent a logical group.`,
            affectedRows: totalNulls,
            suggestion: "Consider treating these columns as a unit — validate that they are all populated or all absent together.",
            confidence,
          }),
        );
      }
    }

    // Low-threshold pairs
    const highPairSet = new Set(highPairs.map(([a, b]) => [a, b].sort().join("|")));
    for (const [colA, colB] of lowPairs) {
      const key = [colA, colB].sort().join("|");
      if (highPairSet.has(key)) continue;
      const sorted = [colA, colB].sort();
      const pairStr = sorted.map((c) => `'${c}'`).join(", ");
      const totalNulls = Math.max(nullCounts.get(colA) ?? 0, nullCounts.get(colB) ?? 0);

      allGroupFindings.push(
        makeFinding({
          severity: Severity.INFO,
          column: sorted.join(","),
          check: "null_correlation",
          message: `Columns ${pairStr} have moderately correlated null patterns (90-95% agreement). They may represent a logical group.`,
          affectedRows: totalNulls,
          suggestion: "Consider treating these columns as a unit — validate that they are all populated or all absent together.",
          confidence: 0.4,
        }),
      );
    }

    // Emit at most MAX_GROUPS, prefer higher confidence
    allGroupFindings.sort((a, b) => b.confidence - a.confidence);
    return allGroupFindings.slice(0, MAX_GROUPS);
  }
}
