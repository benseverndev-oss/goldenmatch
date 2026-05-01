/**
 * Numeric cross-column validation — detects value > max violations.
 * Port of goldencheck/relations/numeric_cross.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const MAX_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["amount", "max"],
  ["amount", "limit"],
  ["charge", "max"],
  ["charge", "limit"],
  ["cost", "budget"],
  ["balance", "limit"],
  ["payment", "max"],
  ["total", "max"],
  ["total", "limit"],
  ["score", "max_score"],
  ["usage", "quota"],
];

function findMaxPairs(columns: readonly string[]): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  const lowerToOrig = new Map<string, string>();
  for (const c of columns) lowerToOrig.set(c.toLowerCase(), c);
  const lowerCols = [...lowerToOrig.keys()];

  for (const [valueKw, maxKw] of MAX_PAIRS) {
    const valueCandidates = lowerCols.filter((lc) => lc.includes(valueKw) && !lc.includes(maxKw));
    const maxCandidates = lowerCols.filter((lc) => lc.includes(maxKw));
    for (const vc of valueCandidates) {
      for (const mc of maxCandidates) {
        if (vc !== mc) {
          pairs.push([lowerToOrig.get(vc)!, lowerToOrig.get(mc)!]);
        }
      }
    }
  }

  return pairs;
}

export class NumericCrossColumnProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const findings: Finding[] = [];
    const pairs = findMaxPairs(data.columns);

    for (const [valueCol, maxCol] of pairs) {
      const f = this.checkExceeds(data, valueCol, maxCol);
      if (f) findings.push(f);
    }

    return findings;
  }

  private checkExceeds(
    data: TabularData,
    valueCol: string,
    maxCol: string,
  ): Finding | null {
    const valVals = data.column(valueCol);
    const maxVals = data.column(maxCol);
    let violations = 0;
    const samples: string[] = [];

    for (let i = 0; i < valVals.length; i++) {
      const v = valVals[i];
      const m = maxVals[i];
      if (isNullish(v) || isNullish(m)) continue;
      const vn = Number(v);
      const mn = Number(m);
      if (!Number.isFinite(vn) || !Number.isFinite(mn)) continue;
      if (vn > mn) {
        violations++;
        if (samples.length < 3) {
          samples.push(`${vn} exceeds ${mn}`);
        }
      }
    }

    if (violations > 0) {
      return makeFinding({
        severity: Severity.ERROR,
        column: valueCol, // Only the value column (not comma-joined) per Python behavior
        check: "cross_column_validation",
        message: `${violations} row(s) where '${valueCol}' exceeds '${maxCol}' — values violate expected maximum constraint`,
        affectedRows: violations,
        sampleValues: samples,
        suggestion: `Ensure '${valueCol}' <= '${maxCol}' for all rows`,
        confidence: 0.85,
      });
    }

    return null;
  }
}
