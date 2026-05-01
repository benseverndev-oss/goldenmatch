/**
 * Cardinality profiler — detects low-cardinality columns (enum candidates).
 * Port of goldencheck/profilers/cardinality.py.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const ENUM_UNIQUE_THRESHOLD = 20;
const ENUM_MIN_ROWS = 50;

export class CardinalityProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];
    const total = data.rowCount;
    const uniqueCount = data.nUnique(column);

    if (uniqueCount < ENUM_UNIQUE_THRESHOLD && total >= ENUM_MIN_ROWS) {
      const nonNull = data.dropNulls(column);
      const uniqueVals = [...new Set(nonNull.map(String))].sort();
      const sample = uniqueVals.slice(0, 10);

      let confidence: number;
      if (uniqueCount < 10 && total >= 1000) {
        confidence = 0.9;
      } else if (uniqueCount >= 10 && uniqueCount < 20 && total >= 50 && total <= 100) {
        confidence = 0.5;
      } else {
        confidence = 0.7;
      }

      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column,
          check: "cardinality",
          message: `Low cardinality: ${uniqueCount} unique value(s) across ${total} rows — consider using an enum type`,
          affectedRows: total,
          sampleValues: sample,
          suggestion: "Define an enum or categorical constraint for this column",
          confidence,
        }),
      );
    }

    return findings;
  }
}
