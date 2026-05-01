/**
 * Pipeline handoff module — generates a structured handoff dict for downstream consumers.
 * Port of goldencheck/agent/handoff.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { Finding, DatasetProfile, ColumnProfile } from "../types.js";
import { Severity, severityLabel, healthScore } from "../types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Convert a list of findings to a findings-by-column dict for healthScore().
 */
function findingsToFbc(
  findings: readonly Finding[],
): Record<string, { errors: number; warnings: number }> {
  const fbc: Record<string, { errors: number; warnings: number }> = {};
  for (const f of findings) {
    if (!fbc[f.column]) {
      fbc[f.column] = { errors: 0, warnings: 0 };
    }
    if (f.severity === Severity.ERROR) {
      fbc[f.column]!.errors += 1;
    } else if (f.severity === Severity.WARNING) {
      fbc[f.column]!.warnings += 1;
    }
  }
  return fbc;
}

/**
 * Derive the attestation label from findings and review state.
 */
function attestation(
  findings: readonly Finding[],
  reviewPending: number,
): string {
  const hasErrors = findings.some((f) => f.severity === Severity.ERROR);
  const hasWarnings = findings.some((f) => f.severity === Severity.WARNING);

  if (hasErrors) return "FAIL";
  if (reviewPending > 0) return "REVIEW_REQUIRED";
  if (hasWarnings) return "PASS_WITH_WARNINGS";
  return "PASS";
}

/**
 * Build the per-column summary dict.
 */
function buildColumns(
  profile: DatasetProfile,
  findings: readonly Finding[],
  columnTypes?: Readonly<Record<string, string>> | null,
): Record<string, Record<string, unknown>> {
  // Group findings by column
  const issuesByCol: Record<string, object[]> = {};
  for (const f of findings) {
    if (!issuesByCol[f.column]) {
      issuesByCol[f.column] = [];
    }
    issuesByCol[f.column]!.push({
      check: f.check,
      severity: severityLabel(f.severity),
      message: f.message,
      confidence: f.confidence,
      affected_rows: f.affectedRows,
    });
  }

  const columns: Record<string, Record<string, unknown>> = {};
  for (const cp of profile.columns) {
    const colType = columnTypes?.[cp.name] ?? cp.inferredType;
    columns[cp.name] = {
      type: colType,
      null_pct: Math.round(cp.nullPct * 10000) / 10000,
      unique_pct: Math.round(cp.uniquePct * 10000) / 10000,
      issues: issuesByCol[cp.name] ?? [],
    };
  }
  return columns;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface GenerateHandoffOptions {
  filePath: string;
  findings: readonly Finding[];
  profile: DatasetProfile;
  pinnedRules: readonly object[];
  reviewPending: number;
  dismissed: number;
  jobName: string;
  columnTypes?: Readonly<Record<string, string>> | null;
}

/**
 * Generate a structured handoff dict summarising a GoldenCheck scan.
 *
 * The returned object is JSON-serialisable and follows schema_version: 1.
 *
 * Attestation logic:
 * - FAIL: any ERROR finding
 * - REVIEW_REQUIRED: reviewPending > 0
 * - PASS_WITH_WARNINGS: any WARNING
 * - PASS: clean
 */
export function generateHandoff(opts: GenerateHandoffOptions): object {
  const {
    filePath,
    findings,
    profile,
    pinnedRules,
    reviewPending,
    dismissed,
    jobName,
    columnTypes,
  } = opts;

  // Counts
  const errorCount = findings.filter((f) => f.severity === Severity.ERROR).length;
  const warningCount = findings.filter((f) => f.severity === Severity.WARNING).length;

  // Health score
  const fbc = findingsToFbc(findings);
  const health = healthScore(fbc);

  // Unresolved: medium-confidence findings (not yet pinned or dismissed)
  const unresolvedFindings = findings
    .filter((f) => f.confidence >= 0.5 && f.confidence < 0.8)
    .map((f) => ({
      severity: severityLabel(f.severity),
      column: f.column,
      check: f.check,
      message: f.message,
      confidence: f.confidence,
      affectedRows: f.affectedRows,
      sampleValues: [...f.sampleValues],
      suggestion: f.suggestion,
      pinned: f.pinned,
      source: f.source,
      metadata: { ...f.metadata },
    }));

  return {
    schema_version: 1,
    source_tool: "goldencheck",
    timestamp: new Date().toISOString(),
    job_name: jobName,
    file_path: filePath,
    row_count: profile.rowCount,
    column_count: profile.columnCount,
    health: { grade: health.grade, score: health.points },
    summary: {
      errors: errorCount,
      warnings: warningCount,
      pinned_rules: pinnedRules.length,
      review_pending: reviewPending,
      dismissed,
    },
    columns: buildColumns(profile, findings, columnTypes),
    pinned_rules: pinnedRules,
    unresolved_findings: unresolvedFindings,
    attestation: attestation(findings, reviewPending),
  };
}
