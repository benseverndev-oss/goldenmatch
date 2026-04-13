/**
 * Type inference profiler — detects mixed types and type mismatches.
 * Port of goldencheck/profilers/type_inference.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const SHOULD_BE_STRING = ["zip", "postal", "phone", "fax", "ssn", "npi", "id", "code", "sku"];

export class TypeInferenceProfiler implements Profiler {
  profile(
    data: TabularData,
    column: string,
    context?: Record<string, unknown>,
  ): Finding[] {
    const findings: Finding[] = [];
    const values = data.column(column);
    const nonNull = values.filter((v) => !isNullish(v));
    const total = nonNull.length;
    if (total === 0) return findings;

    // Detect string column that is mostly numeric
    const dt = data.dtype(column);
    if (dt === "string") {
      let numericCount = 0;
      let intCount = 0;
      for (const v of nonNull) {
        const n = Number(v);
        if (Number.isFinite(n)) {
          numericCount++;
          if (Number.isInteger(n)) intCount++;
        }
      }
      const numericPct = numericCount / total;

      if (numericPct >= 0.8) {
        const intPct = intCount / total;
        const typeName = intPct > 0.9 ? "integer" : "numeric";
        const nonNumeric = total - numericCount;

        if (context) {
          if (!context[column]) context[column] = {};
          (context[column] as Record<string, unknown>)["mostly_numeric"] = true;
        }

        findings.push(
          makeFinding({
            severity: Severity.WARNING,
            column,
            check: "type_inference",
            message: `Column is string but ${Math.round(numericPct * 100)}% of values are ${typeName} (${nonNumeric} non-${typeName} values)`,
            affectedRows: nonNumeric,
            suggestion: `Consider casting to ${typeName}`,
            confidence: 0.9,
          }),
        );
      } else if (numericPct > 0 && numericPct < 0.05) {
        findings.push(
          makeFinding({
            severity: Severity.INFO,
            column,
            check: "type_inference",
            message: `Column is string but ${(numericPct * 100).toFixed(1)}% of values appear numeric (${numericCount} values) — possible data entry error`,
            affectedRows: numericCount,
            suggestion: "Investigate numeric values in this text column",
            confidence: 0.3,
          }),
        );
      }
    }

    // Check: numeric column that should be string based on name
    if (dt === "integer" || dt === "float") {
      const colLower = column.toLowerCase();
      for (const hint of SHOULD_BE_STRING) {
        if (colLower.includes(hint)) {
          findings.push(
            makeFinding({
              severity: Severity.WARNING,
              column,
              check: "type_inference",
              message: `Column '${column}' is numeric but name suggests it should be string (may lose leading zeros)`,
              confidence: 0.6,
              suggestion: "Consider storing as string to preserve formatting",
            }),
          );
          break;
        }
      }
    }

    return findings;
  }
}
