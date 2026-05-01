/**
 * Nullability profiler — detects required vs. optional columns.
 * Port of goldencheck/profilers/nullability.py.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

export class NullabilityProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];
    const total = data.rowCount;
    const nullCount = data.nullCount(column);
    const nullPct = total > 0 ? nullCount / total : 0;

    if (nullCount === total) {
      findings.push(
        makeFinding({
          severity: Severity.ERROR,
          column,
          check: "nullability",
          message: `Column is entirely null (${total} rows)`,
          affectedRows: total,
          confidence: 0.99,
        }),
      );
    } else if (nullCount === 0 && total >= 10) {
      let confidence: number;
      if (total >= 1000) confidence = 0.95;
      else if (total < 50) confidence = 0.5;
      else confidence = 0.7;

      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column,
          check: "nullability",
          message: `0 nulls across ${total} rows — likely required`,
          confidence,
        }),
      );
    } else if (nullPct > 0 && nullPct < 1) {
      const nonNullPct = 1.0 - nullPct;

      if (nonNullPct > 0.95 && total >= 100) {
        findings.push(
          makeFinding({
            severity: Severity.WARNING,
            column,
            check: "nullability",
            message: `${nullCount} nulls (${(nullPct * 100).toFixed(1)}%) in a ${(nonNullPct * 100).toFixed(1)}% non-null column — possible data quality issue`,
            affectedRows: nullCount,
            suggestion: "Verify whether these nulls are expected or indicate missing data",
            confidence: 0.75,
          }),
        );
      } else {
        const notable =
          nullPct > 0.8 || (total >= 100 && nullPct > 0.05);

        if (notable) {
          findings.push(
            makeFinding({
              severity: Severity.INFO,
              column,
              check: "nullability",
              message: `${nullCount} nulls (${(nullPct * 100).toFixed(1)}%) — column is optional`,
              affectedRows: nullCount,
              confidence: 0.7,
            }),
          );
        }
      }
    }

    return findings;
  }
}
