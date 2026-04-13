/**
 * Temporal order profiler — checks start dates precede end dates.
 * Port of goldencheck/relations/temporal.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const PAIR_HEURISTICS: ReadonlyArray<readonly [string, string]> = [
  ["start", "end"],
  ["created", "updated"],
  ["begin", "finish"],
  ["signup", "login"],
  ["signup", "last_login"],
  ["open", "close"],
  ["opened", "closed"],
  ["hire", "termination"],
  ["birth", "death"],
  ["order", "delivery"],
  ["order", "ship"],
  ["admission", "discharge"],
  ["admit", "discharge"],
  ["service", "submit"],
  ["submit", "approval"],
  ["effective", "expir"],
  ["issue", "expir"],
  ["received", "processed"],
  ["received", "complet"],
  ["placed", "fulfill"],
  ["placed", "shipped"],
  ["request", "approved"],
  ["booked", "checkin"],
  ["checkin", "checkout"],
  ["enroll", "graduat"],
  ["invoice", "payment"],
  ["prescribed", "dispensed"],
];

function findDatePairs(columns: readonly string[]): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  const lowerToOrig = new Map<string, string>();
  for (const c of columns) lowerToOrig.set(c.toLowerCase(), c);
  const lowerCols = [...lowerToOrig.keys()];

  for (const [startKw, endKw] of PAIR_HEURISTICS) {
    const startCandidates = lowerCols.filter((lc) => lc.includes(startKw));
    const endCandidates = lowerCols.filter(
      (lc) => lc.includes(endKw) && !startCandidates.includes(lc),
    );
    for (const sc of startCandidates) {
      for (const ec of endCandidates) {
        pairs.push([lowerToOrig.get(sc)!, lowerToOrig.get(ec)!]);
      }
    }
  }

  // Deduplicate
  const seen = new Set<string>();
  return pairs.filter((p) => {
    const key = `${p[0]}|${p[1]}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function tryParseDate(v: unknown): Date | null {
  if (isNullish(v)) return null;
  const s = String(v);
  const d = new Date(s);
  if (isNaN(d.getTime())) return null;
  return d;
}

function isDateColumn(data: TabularData, col: string): boolean {
  const nonNull = data.stringValues(col);
  if (nonNull.length === 0) return false;
  let dateCount = 0;
  for (const v of nonNull.slice(0, 50)) {
    if (tryParseDate(v) !== null) dateCount++;
  }
  return dateCount / Math.min(nonNull.length, 50) > 0.7;
}

export class TemporalOrderProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const findings: Finding[] = [];

    // Keyword-matched pairs
    const kwPairs = findDatePairs(data.columns);
    const kwPairSet = new Set(kwPairs.map((p) => `${p[0]}|${p[1]}`));
    const checked = new Set<string>();

    for (const [startCol, endCol] of kwPairs) {
      checked.add(`${startCol}|${endCol}`);
      const f = this.checkPair(data, startCol, endCol, 0.9);
      if (f) findings.push(f);
    }

    // Fallback: all date column pairs (if <= 6 date columns)
    const dateCols = data.columns.filter((c) => isDateColumn(data, c));
    if (dateCols.length <= 6) {
      for (let i = 0; i < dateCols.length; i++) {
        for (let j = i + 1; j < dateCols.length; j++) {
          const a = dateCols[i]!;
          const b = dateCols[j]!;
          const fwd = `${a}|${b}`;
          const rev = `${b}|${a}`;
          if (!kwPairSet.has(fwd) && !kwPairSet.has(rev) && !checked.has(fwd)) {
            checked.add(fwd);
            const f = this.checkPair(data, a, b, 0.4);
            if (f) findings.push(f);
          }
        }
      }
    }

    return findings;
  }

  private checkPair(
    data: TabularData,
    startCol: string,
    endCol: string,
    confidence: number,
  ): Finding | null {
    const startVals = data.column(startCol);
    const endVals = data.column(endCol);
    let violations = 0;
    const samples: string[] = [];

    for (let i = 0; i < startVals.length; i++) {
      const s = tryParseDate(startVals[i]);
      const e = tryParseDate(endVals[i]);
      if (s && e && s > e) {
        violations++;
        if (samples.length < 3) {
          samples.push(`${String(startVals[i])} > ${String(endVals[i])}`);
        }
      }
    }

    if (violations > 0) {
      return makeFinding({
        severity: Severity.ERROR,
        column: `${startCol},${endCol}`,
        check: "temporal_order",
        message: `${violations} row(s) where '${startCol}' is later than '${endCol}', violating expected temporal order`,
        affectedRows: violations,
        sampleValues: samples,
        suggestion: `Ensure '${startCol}' <= '${endCol}' for all rows.`,
        confidence,
      });
    }

    return null;
  }
}
