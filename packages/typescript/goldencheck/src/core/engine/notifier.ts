/**
 * Webhook notifier — POST scan results to external URLs.
 * Port of goldencheck/engine/notifier.py.
 * Edge-safe types; sendWebhook uses fetch() (available in Node 18+ and all modern runtimes).
 */

import type { Finding } from "../types.js";
import { Severity, severityLabel } from "../types.js";
import type { ScanRecord } from "./history.js";

// Grade ordering: lower index = better grade
const GRADE_ORDER = "ABCDF";

/**
 * Determine whether to fire a webhook notification.
 *
 * Trigger semantics:
 * - "grade-drop": health grade decreased since last scan
 * - "any-error": at least one ERROR finding exists
 * - "any-warning": at least one WARNING or ERROR finding exists
 */
export function shouldNotify(
  currentGrade: string,
  currentFindings: readonly Finding[],
  previousScan: ScanRecord | null,
  notifyOn: string,
): boolean {
  const errors = currentFindings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = currentFindings.filter((f) => f.severity === Severity.WARNING).length;

  if (notifyOn === "any-error") {
    return errors > 0;
  }

  if (notifyOn === "any-warning") {
    return warnings > 0 || errors > 0;
  }

  if (notifyOn === "grade-drop") {
    if (previousScan === null) {
      return false;
    }
    const oldIdx = GRADE_ORDER.includes(previousScan.grade)
      ? GRADE_ORDER.indexOf(previousScan.grade)
      : 0;
    const newIdx = GRADE_ORDER.includes(currentGrade)
      ? GRADE_ORDER.indexOf(currentGrade)
      : 0;
    // Higher index = worse grade
    return newIdx > oldIdx;
  }

  return false;
}

/**
 * POST scan results to a webhook URL. Fire-and-forget.
 * Logs a warning on failure but never throws.
 */
export async function sendWebhook(
  url: string,
  file: string,
  grade: string,
  score: number,
  findings: readonly Finding[],
  trigger: string,
  previousGrade?: string,
): Promise<void> {
  const errors = findings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = findings.filter((f) => f.severity === Severity.WARNING).length;

  // Collect top findings (first 10 that are WARNING or above)
  const topFindings: Array<{
    severity: string;
    column: string;
    check: string;
    message: string;
  }> = [];

  for (const f of findings) {
    if (topFindings.length >= 10) break;
    if (f.severity >= Severity.WARNING) {
      topFindings.push({
        severity: severityLabel(f.severity).toLowerCase(),
        column: f.column,
        check: f.check,
        message: f.message.slice(0, 200),
      });
    }
  }

  const payload = {
    tool: "goldencheck-js",
    version: "0.1.0",
    trigger,
    file,
    health_grade: grade,
    health_score: score,
    previous_grade: previousGrade ?? null,
    errors,
    warnings,
    top_findings: topFindings,
  };

  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(5000),
    });
  } catch (e) {
    console.warn(`Webhook failed (${url}):`, e);
  }
}
