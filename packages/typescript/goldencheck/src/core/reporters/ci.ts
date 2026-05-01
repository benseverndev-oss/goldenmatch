/**
 * CI reporter — determines exit code based on findings and fail_on threshold.
 * Port of goldencheck/reporters/ci_reporter.py.
 */

import type { Finding, Severity } from "../types.js";
import { Severity as S } from "../types.js";

const SEVERITY_MAP: Readonly<Record<string, Severity>> = {
  error: S.ERROR,
  warning: S.WARNING,
  info: S.INFO,
};

/**
 * Return exit code 0 (pass) or 1 (fail) based on whether any finding
 * meets or exceeds the fail_on severity threshold.
 *
 * @param findings - The list of findings to evaluate.
 * @param failOn - Severity threshold: "error", "warning", or "info".
 * @returns 0 if no finding meets the threshold, 1 otherwise.
 */
export function ciCheck(
  findings: readonly Finding[],
  failOn: string,
): number {
  const threshold = SEVERITY_MAP[failOn.toLowerCase()] ?? S.ERROR;
  for (const f of findings) {
    if (f.severity >= threshold) {
      return 1;
    }
  }
  return 0;
}
