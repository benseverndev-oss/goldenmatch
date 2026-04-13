/**
 * Format detection profiler — detects email, phone, and URL patterns.
 * Port of goldencheck/profilers/format_detection.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const EMAIL_RE = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
const PHONE_RE = /^\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$/;
const URL_RE = /^https?:\/\//;

const FORMATS: Array<[string, RegExp]> = [
  ["email", EMAIL_RE],
  ["phone", PHONE_RE],
  ["url", URL_RE],
];

const CROSS_FORMAT_CHECKS: Record<string, Array<[string, RegExp]>> = {
  url: [["email", EMAIL_RE]],
  email: [["url", URL_RE]],
  phone: [["email", EMAIL_RE]],
};

export class FormatDetectionProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];

    if (!data.isString(column)) return findings;

    const nonNull = data.stringValues(column);
    const total = nonNull.length;
    if (total === 0) return findings;

    for (const [fmtName, pattern] of FORMATS) {
      const matches: boolean[] = nonNull.map((v) => pattern.test(v));
      const matchCount = matches.filter(Boolean).length;
      const matchPct = matchCount / total;

      if (matchPct > 0.7) {
        const detectConfidence = matchPct > 0.95 ? 0.9 : 0.6;

        findings.push(
          makeFinding({
            severity: Severity.INFO,
            column,
            check: "format_detection",
            message: `Column appears to contain ${fmtName} values (${(matchPct * 100).toFixed(1)}% match)`,
            affectedRows: matchCount,
            confidence: detectConfidence,
          }),
        );

        const nonMatchCount = total - matchCount;
        if (nonMatchCount > 0) {
          const sample = nonNull
            .filter((_, i) => !matches[i])
            .slice(0, 5);

          findings.push(
            makeFinding({
              severity: Severity.WARNING,
              column,
              check: "format_detection",
              message: `${nonMatchCount} value(s) do not match expected ${fmtName} format`,
              affectedRows: nonMatchCount,
              sampleValues: sample,
              suggestion: `Review non-${fmtName} values for data quality issues`,
              confidence: detectConfidence,
            }),
          );
        }

        // Cross-format detection
        const crossChecks = CROSS_FORMAT_CHECKS[fmtName];
        if (crossChecks && nonMatchCount > 0) {
          const nonMatching = nonNull.filter((_, i) => !matches[i]);
          for (const [otherFmt, otherPattern] of crossChecks) {
            const wrongFmtCount = nonMatching.filter((v) => otherPattern.test(v)).length;
            if (wrongFmtCount > 0) {
              const wrongPct = wrongFmtCount / total;
              findings.push(
                makeFinding({
                  severity: Severity.ERROR,
                  column,
                  check: "format_detection",
                  message: `Column is detected as ${fmtName} but ${wrongFmtCount} value(s) (${(wrongPct * 100).toFixed(1)}%) appear to be ${otherFmt} — wrong type values present`,
                  affectedRows: wrongFmtCount,
                  suggestion: `Remove or correct ${otherFmt} values from this ${fmtName} column`,
                  confidence: detectConfidence,
                }),
              );
            }
          }
        }
      }
    }

    return findings;
  }
}
