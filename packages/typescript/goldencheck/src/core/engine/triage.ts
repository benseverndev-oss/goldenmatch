/**
 * Auto-triage engine — classify findings into pin/dismiss/review buckets.
 * Port of goldencheck/engine/triage.py.
 * Edge-safe: no Node.js dependencies.
 */

import { type Finding, Severity } from "../types.js";

export interface TriageResult {
  readonly pin: readonly Finding[];
  readonly dismiss: readonly Finding[];
  readonly review: readonly Finding[];
}

/**
 * Classify findings into pin/dismiss/review buckets.
 *
 * Operates on POST-downgrade findings (after applyConfidenceDowngrade).
 *
 * - **Pin**: severity >= WARNING AND confidence >= 0.8
 * - **Dismiss**: severity == INFO OR confidence < 0.5
 * - **Review**: everything else (0.5 <= confidence < 0.8 + WARNING/ERROR)
 */
export function autoTriage(findings: readonly Finding[]): TriageResult {
  const pin: Finding[] = [];
  const dismiss: Finding[] = [];
  const review: Finding[] = [];

  for (const f of findings) {
    if (f.severity >= Severity.WARNING && f.confidence >= 0.8) {
      pin.push(f);
    } else if (f.severity === Severity.INFO || f.confidence < 0.5) {
      dismiss.push(f);
    } else {
      review.push(f);
    }
  }

  return { pin, dismiss, review };
}
