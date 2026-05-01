/**
 * Suppression engine — downgrades findings based on semantic type.
 * Port of goldencheck/semantic/suppression.py.
 */

import type { Finding, TypeDef, ColumnClassification } from "../types.js";
import { Severity, replaceFinding } from "../types.js";

/**
 * Apply type-based suppression to findings.
 * Matching checks are downgraded to INFO (never suppresses LLM or high-confidence).
 */
export function applySuppression(
  findings: readonly Finding[],
  columnTypes: Readonly<Record<string, ColumnClassification>>,
  typeDefs: Readonly<Record<string, TypeDef>>,
): Finding[] {
  return findings.map((f) => {
    // Only suppress WARNING/ERROR
    if (f.severity === Severity.INFO) return f;

    // Never suppress LLM findings
    if (f.source === "llm") return f;

    // Never suppress high-confidence findings
    if (f.confidence >= 0.9) return f;

    const classification = columnTypes[f.column];
    if (!classification || !classification.typeName) return f;

    const typeDef = typeDefs[classification.typeName];
    if (!typeDef) return f;

    if (!typeDef.suppress.includes(f.check)) return f;

    // Special handling for pattern_consistency on geo/identifier types
    if (f.check === "pattern_consistency" && (classification.typeName === "geo" || classification.typeName === "identifier")) {
      const dominantPattern = f.metadata["dominant_pattern"] as string | undefined;
      const minorityPattern = f.metadata["minority_pattern"] as string | undefined;

      if (dominantPattern && minorityPattern) {
        const lengthDiff = Math.abs(dominantPattern.length - minorityPattern.length);
        const domDigitRatio = countChar(dominantPattern, "D") / Math.max(dominantPattern.length, 1);
        const minDigitRatio = countChar(minorityPattern, "D") / Math.max(minorityPattern.length, 1);

        // Preserve real format issues (e.g., 5-digit vs 9-digit zip)
        if (lengthDiff > 1 && domDigitRatio > 0.5 && minDigitRatio > 0.5) {
          return f; // Skip suppression
        }
      }
    }

    // Suppress: downgrade to INFO
    return replaceFinding(f, {
      severity: Severity.INFO,
      message: `${f.message} (suppressed: ${classification.typeName} column)`,
    });
  });
}

function countChar(s: string, ch: string): number {
  let count = 0;
  for (const c of s) {
    if (c === ch) count++;
  }
  return count;
}
