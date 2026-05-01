/**
 * Pattern consistency profiler — detects inconsistent string patterns.
 * Port of goldencheck/profilers/pattern_consistency.py.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const MINORITY_THRESHOLD = 0.30;
const WARNING_THRESHOLD = 0.05;
const MAX_PATTERNS = 5;

/** Replace digits with D and letters with L, keeping punctuation as-is. */
export function generalize(value: string): string {
  let result = "";
  for (const ch of value) {
    if (ch >= "0" && ch <= "9") result += "D";
    else if ((ch >= "a" && ch <= "z") || (ch >= "A" && ch <= "Z")) result += "L";
    else result += ch;
  }
  return result;
}

export class PatternConsistencyProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];

    if (!data.isString(column)) return findings;

    const nonNull = data.stringValues(column);
    const total = nonNull.length;
    if (total === 0) return findings;

    // Build pattern counts
    const patternCounts = new Map<string, number>();
    const patternValues = new Map<string, string[]>();

    for (const v of nonNull) {
      const p = generalize(v);
      patternCounts.set(p, (patternCounts.get(p) ?? 0) + 1);
      const samples = patternValues.get(p);
      if (!samples) {
        patternValues.set(p, [v]);
      } else if (samples.length < 5) {
        samples.push(v);
      }
    }

    if (patternCounts.size <= 1) return findings;

    // Sort by count descending
    const sorted = [...patternCounts.entries()].sort((a, b) => b[1] - a[1]);
    const dominantPattern = sorted[0]![0];
    const dominantCount = sorted[0]![1];

    // Collect minority patterns
    const minorities: Array<{ pattern: string; count: number; pct: number }> = [];
    for (let i = 1; i < sorted.length; i++) {
      const [pattern, count] = sorted[i]!;
      const pct = count / total;
      if (pct < MINORITY_THRESHOLD) {
        minorities.push({ pattern, count, pct });
      }
    }

    if (minorities.length === 0) return findings;

    // Sort rarest first
    minorities.sort((a, b) => a.count - b.count);

    const emitted = minorities.slice(0, MAX_PATTERNS);
    for (const { pattern, count, pct } of emitted) {
      const severity = pct < WARNING_THRESHOLD ? Severity.WARNING : Severity.INFO;
      const confidence = pct < WARNING_THRESHOLD ? 0.8 : 0.5;
      const sampleVals = patternValues.get(pattern)?.slice(0, 5) ?? [];

      // Detect structural pattern shift (letter-first vs digit-first)
      const domStartsAlpha = dominantPattern.length > 0 && dominantPattern[0] === "L";
      const minStartsAlpha = pattern.length > 0 && pattern[0] === "L";
      const msgExtra =
        domStartsAlpha !== minStartsAlpha && pct < WARNING_THRESHOLD
          ? " — possible invalid format or mixed coding standard"
          : "";

      findings.push(
        makeFinding({
          severity,
          column,
          check: "pattern_consistency",
          message:
            `Inconsistent pattern detected: '${pattern}' appears in ` +
            `${count} row(s) (${(pct * 100).toFixed(1)}%) vs dominant pattern ` +
            `'${dominantPattern}' (${dominantCount} row(s))` +
            msgExtra,
          affectedRows: count,
          sampleValues: sampleVals,
          suggestion: "Standardize values to a single format/pattern",
          confidence,
          metadata: { dominant_pattern: dominantPattern, minority_pattern: pattern },
        }),
      );
    }

    // Summary if more patterns exist
    if (minorities.length > MAX_PATTERNS) {
      const extra = minorities.length - MAX_PATTERNS;
      findings.push(
        makeFinding({
          severity: Severity.INFO,
          column,
          check: "pattern_consistency",
          message: `${extra} additional inconsistent pattern(s) detected (showing top ${MAX_PATTERNS})`,
          suggestion: "Standardize values to a single format/pattern",
          confidence: 0.5,
        }),
      );
    }

    return findings;
  }
}
