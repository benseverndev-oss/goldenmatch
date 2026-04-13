/**
 * Post-scan confidence processing.
 * Port of goldencheck/engine/confidence.py.
 */

import type { Finding } from "../types.js";
import { Severity, replaceFinding } from "../types.js";

/**
 * Boost confidence for columns flagged by multiple profilers.
 * - 2 distinct WARNING/ERROR checks on same column: +0.1
 * - 3+ distinct checks: +0.2 (exclusive tiers)
 * - Capped at 1.0
 * - Returns new array — originals never mutated.
 */
export function applyCorroborationBoost(findings: readonly Finding[]): Finding[] {
  // Count distinct WARNING/ERROR checks per column
  const checksPerCol = new Map<string, Set<string>>();
  for (const f of findings) {
    if (f.severity === Severity.ERROR || f.severity === Severity.WARNING) {
      let checks = checksPerCol.get(f.column);
      if (!checks) {
        checks = new Set();
        checksPerCol.set(f.column, checks);
      }
      checks.add(f.check);
    }
  }

  return findings.map((f) => {
    const colCount = checksPerCol.get(f.column)?.size ?? 0;
    let boost: number;
    if (colCount >= 3) boost = 0.2;
    else if (colCount >= 2) boost = 0.1;
    else boost = 0.0;

    if (boost > 0 && (f.severity === Severity.ERROR || f.severity === Severity.WARNING)) {
      return replaceFinding(f, { confidence: Math.min(f.confidence + boost, 1.0) });
    }
    return f;
  });
}

/**
 * Downgrade low-confidence findings to INFO when LLM boost is not enabled.
 * If llmBoost=true, returns findings unchanged.
 * If llmBoost=false, WARNING/ERROR with confidence < 0.5 → INFO.
 */
export function applyConfidenceDowngrade(findings: readonly Finding[], llmBoost: boolean): Finding[] {
  if (llmBoost) return [...findings];

  return findings.map((f) => {
    if (f.confidence < 0.5 && (f.severity === Severity.ERROR || f.severity === Severity.WARNING)) {
      return replaceFinding(f, {
        severity: Severity.INFO,
        message: `${f.message} (low confidence — use --llm-boost to verify)`,
      });
    }
    return f;
  });
}
