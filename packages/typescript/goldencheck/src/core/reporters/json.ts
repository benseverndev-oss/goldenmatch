/**
 * JSON reporter — machine-readable output matching spec schema.
 * Port of goldencheck/reporters/json_reporter.py.
 */

import type { Finding, DatasetProfile } from "../types.js";
import { Severity, severityLabel, healthScore } from "../types.js";

/**
 * Serialize findings and profile to a JSON string.
 *
 * Output format:
 * ```json
 * {
 *   "file": "...",
 *   "rows": N,
 *   "columns": N,
 *   "health_grade": "A",
 *   "health_score": 95,
 *   "summary": { "errors": 0, "warnings": 1, "info": 3 },
 *   "findings": [...]
 * }
 * ```
 */
export function reportJson(
  findings: readonly Finding[],
  profile: DatasetProfile,
): string {
  const errors = findings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = findings.filter((f) => f.severity === Severity.WARNING).length;
  const infos = findings.length - errors - warnings;

  const score = healthScore(undefined, errors, warnings);

  const data = {
    file: profile.filePath,
    rows: profile.rowCount,
    columns: profile.columnCount,
    health_grade: score.grade,
    health_score: score.points,
    summary: { errors, warnings, info: infos },
    findings: findings.map((f) => {
      const entry: Record<string, unknown> = {
        severity: severityLabel(f.severity).toLowerCase(),
        column: f.column,
        check: f.check,
        message: f.message,
        affected_rows: f.affectedRows,
        sample_values: f.sampleValues,
        confidence: f.confidence,
      };
      if (f.source !== null) {
        entry.source = f.source;
      }
      return entry;
    }),
  };

  return JSON.stringify(data, null, 2) + "\n";
}
