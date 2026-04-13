/**
 * Uniqueness profiler — detects primary key candidates and duplicates.
 * Port of goldencheck/profilers/uniqueness.py.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const IDENTIFIER_KEYWORDS = ["id", "key", "code", "sku"];

export class UniquenessProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];
    const total = data.rowCount;
    const nonNull = data.dropNulls(column);
    const uniqueCount = data.nUnique(column);
    const uniquePct = nonNull.length > 0 ? uniqueCount / nonNull.length : 0;

    if (uniquePct === 1.0 && total >= 10) {
      const confidence = total >= 100 ? 0.95 : 0.7;
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column,
          check: "uniqueness",
          message: `100% unique across ${total} rows — likely primary key`,
          confidence,
        }),
      );
    } else if (uniquePct < 1.0) {
      const dupCount = nonNull.length - uniqueCount;
      const colLower = column.toLowerCase();
      const isIdentifier = IDENTIFIER_KEYWORDS.some((kw) => colLower.includes(kw));

      if (dupCount > 0 && uniquePct > 0.95 && isIdentifier) {
        findings.push(
          makeFinding({
            severity: Severity.WARNING,
            column,
            check: "uniqueness",
            message: `Near-unique column (${(uniquePct * 100).toFixed(1)}% unique) with ${dupCount} duplicates`,
            affectedRows: dupCount,
            confidence: 0.6,
          }),
        );
      }
    }

    return findings;
  }
}
