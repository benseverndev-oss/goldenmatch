/**
 * Intelligence layer — strategy selection, finding explanation, domain comparison.
 * Port of goldencheck/agent/intelligence.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { TabularData } from "../data.js";
import type { Finding, DatasetProfile, ColumnProfile } from "../types.js";
import { Severity, severityLabel, healthScore } from "../types.js";
import { classifyColumns, loadTypeDefs } from "../semantic/classifier.js";
import { listAvailableDomains } from "../semantic/domains/index.js";

export interface StrategyDecision {
  domain: string | null;
  domainConfidence: number;
  sampleStrategy: string;
  profilerStrategy: string;
  llmBoost: boolean;
  why: Record<string, unknown>;
}

const PREVIEW_ROWS = 10_000;

// ---------------------------------------------------------------------------
// Domain detection
// ---------------------------------------------------------------------------

function detectDomain(
  data: TabularData,
): { domain: string | null; confidence: number; scores: Record<string, number> } {
  const available = listAvailableDomains();
  if (available.length === 0) {
    return { domain: null, confidence: 0, scores: {} };
  }

  const scores: Record<string, number> = {};
  for (const domainName of available) {
    const typeDefs = loadTypeDefs(domainName);
    const colTypes = classifyColumns(data, typeDefs);
    const matched = Object.values(colTypes).filter((c) => c.typeName !== null).length;
    const total = Math.max(Object.keys(colTypes).length, 1);
    scores[domainName] = matched / total;
  }

  let bestName: string | null = null;
  let bestScore = 0;
  for (const [name, score] of Object.entries(scores)) {
    if (score > bestScore) {
      bestScore = score;
      bestName = name;
    }
  }

  // Only pick a domain if it meaningfully matches (>20% columns)
  if (bestName !== null && bestScore > 0.2) {
    return { domain: bestName, confidence: bestScore, scores };
  }
  return { domain: null, confidence: 0, scores };
}

// ---------------------------------------------------------------------------
// LLM availability check
// ---------------------------------------------------------------------------

function checkLlmAvailable(): boolean {
  try {
    // Edge-safe env check — globalThis.process may not exist in all runtimes
    const env = (typeof process !== "undefined" && process.env) || {};
    return !!(env.ANTHROPIC_API_KEY || env.OPENAI_API_KEY);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Strategy selection
// ---------------------------------------------------------------------------

/**
 * Analyse data and decide how to scan it.
 *
 * Returns a StrategyDecision with domain, sampling, profiler, and LLM
 * choices plus an explanation dict.
 */
export function selectStrategy(data: TabularData): StrategyDecision {
  const rowCount = data.rowCount;
  const colCount = data.columns.length;

  // Preview sample for domain detection (capped at PREVIEW_ROWS)
  const preview = rowCount > PREVIEW_ROWS ? data.sample(PREVIEW_ROWS) : data;

  // Domain detection
  const { domain, confidence: domainConfidence, scores: domainScores } = detectDomain(preview);

  // Sample strategy (based on total rows)
  let sampleStrategy: string;
  if (rowCount <= 50_000) {
    sampleStrategy = "full";
  } else if (rowCount <= 500_000) {
    sampleStrategy = "sample_100k";
  } else {
    sampleStrategy = "sample_100k_stratified";
  }

  // Profiler strategy (based on column count)
  let profilerStrategy: string;
  if (colCount <= 20) {
    profilerStrategy = "standard";
  } else if (colCount <= 80) {
    profilerStrategy = "parallel_batched";
  } else {
    profilerStrategy = "wide_table";
  }

  // LLM availability
  const llmAvailable = checkLlmAvailable();

  const why: Record<string, unknown> = {
    row_count: rowCount,
    col_count: colCount,
    preview_rows: preview.rowCount,
    domain_scores: domainScores,
    llm_available: llmAvailable,
    sample_strategy_reason: `${rowCount} rows -> ${sampleStrategy}`,
    profiler_strategy_reason: `${colCount} columns -> ${profilerStrategy}`,
  };

  return {
    domain,
    domainConfidence,
    sampleStrategy,
    profilerStrategy,
    llmBoost: llmAvailable,
    why,
  };
}

// ---------------------------------------------------------------------------
// Alternatives builder
// ---------------------------------------------------------------------------

/**
 * Build a ranked list of alternative strategies the user could try.
 */
export function buildAlternatives(
  decision: StrategyDecision,
  domainScores: Record<string, number>,
): object[] {
  const alts: object[] = [];

  // Suggest runner-up domains
  const sorted = Object.entries(domainScores).sort((a, b) => b[1] - a[1]);
  for (const [name, score] of sorted) {
    if (name === decision.domain) continue;
    if (score > 0.1) {
      alts.push({
        type: "domain",
        value: name,
        score: Math.round(score * 1000) / 1000,
        reason: `Domain '${name}' matched ${Math.round(score * 100)}% of columns`,
      });
    }
  }

  // Suggest LLM boost if not already enabled
  if (!decision.llmBoost) {
    alts.push({
      type: "llm_boost",
      value: true,
      reason: "Install LLM extras for deeper analysis",
    });
  }

  // Suggest no-domain scan if a domain was picked
  if (decision.domain !== null) {
    alts.push({
      type: "domain",
      value: null,
      score: 0,
      reason: "Run without a domain pack for generic analysis",
    });
  }

  return alts;
}

// ---------------------------------------------------------------------------
// Finding / column explanation
// ---------------------------------------------------------------------------

/**
 * Return a natural-language explanation dict for a single finding.
 */
export function explainFinding(finding: Finding, profile: DatasetProfile): object {
  const colProfile: ColumnProfile | undefined = profile.columns.find(
    (c) => c.name === finding.column,
  );

  const sevLabel = severityLabel(finding.severity).toLowerCase();
  const confLabel =
    finding.confidence >= 0.8 ? "high" : finding.confidence >= 0.5 ? "medium" : "low";

  const what =
    `The '${finding.check}' check found an issue in column '${finding.column}': ` +
    finding.message;

  const impactParts = [`Severity is ${sevLabel} (confidence: ${confLabel}).`];
  if (finding.affectedRows) {
    if (colProfile && colProfile.rowCount > 0) {
      const pct = finding.affectedRows / colProfile.rowCount;
      impactParts.push(
        `Affects ${finding.affectedRows.toLocaleString()} row(s) ` +
          `(${(pct * 100).toFixed(1)}% of ${colProfile.rowCount.toLocaleString()} total).`,
      );
    } else {
      impactParts.push(`Affects ${finding.affectedRows.toLocaleString()} row(s).`);
    }
  }

  const suggestion = finding.suggestion || "Review the flagged values and correct or confirm them.";

  const result: Record<string, unknown> = {
    what,
    severity: sevLabel,
    confidence: Math.round(finding.confidence * 1000) / 1000,
    impact: impactParts.join(" "),
    suggestion,
    affected_rows: finding.affectedRows,
  };

  if (finding.sampleValues.length > 0) {
    result.sample_values = finding.sampleValues.slice(0, 5);
  }
  if (colProfile) {
    result.column_type = colProfile.inferredType;
    result.column_null_pct = Math.round(colProfile.nullPct * 10000) / 10000;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Convert a findings list to a findings-by-column dict for healthScore().
 */
export function findingsToFbc(
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
 * Deep-dive explanation for a single column's health.
 *
 * Unlike the Python version, this takes pre-computed findings and profile
 * instead of reading a file (edge-safe, no filesystem access).
 */
export function explainColumn(
  data: TabularData,
  column: string,
  findings?: readonly Finding[],
  profile?: DatasetProfile,
): object {
  // Filter findings for this column
  const colFindings = findings ? findings.filter((f) => f.column === column) : [];

  const colProfile: ColumnProfile | undefined = profile
    ? profile.columns.find((c) => c.name === column)
    : undefined;

  const errors = colFindings.filter((f) => f.severity === Severity.ERROR).length;
  const warnings = colFindings.filter((f) => f.severity === Severity.WARNING).length;
  const infos = colFindings.filter((f) => f.severity === Severity.INFO).length;

  let health: string;
  if (errors > 0) {
    health = "unhealthy";
  } else if (warnings > 0) {
    health = "needs attention";
  } else {
    health = "healthy";
  }

  let narrative = `Column '${column}' is ${health}.`;
  if (colProfile) {
    narrative +=
      ` Type: ${colProfile.inferredType},` +
      ` ${(colProfile.nullPct * 100).toFixed(1)}% null,` +
      ` ${(colProfile.uniquePct * 100).toFixed(1)}% unique.`;
  }
  if (errors) narrative += ` ${errors} error(s) detected.`;
  if (warnings) narrative += ` ${warnings} warning(s) detected.`;

  const explained = profile
    ? colFindings.map((f) => explainFinding(f, profile))
    : [];

  return {
    column,
    health,
    narrative,
    errors,
    warnings,
    infos,
    findings: explained,
    profile: {
      type: colProfile?.inferredType ?? null,
      null_pct: colProfile ? Math.round(colProfile.nullPct * 10000) / 10000 : null,
      unique_pct: colProfile ? Math.round(colProfile.uniquePct * 10000) / 10000 : null,
      row_count: colProfile?.rowCount ?? null,
    },
  };
}

// ---------------------------------------------------------------------------
// Domain comparison
// ---------------------------------------------------------------------------

/**
 * Compare how each available domain scores the given data.
 *
 * Unlike the Python version, this does not re-scan (edge-safe).
 * It classifies columns under each domain and reports match rates.
 */
export function compareDomains(data: TabularData): object {
  const available = listAvailableDomains();
  const candidates = [null, ...available]; // null = base (no domain)

  const results: Record<string, Record<string, unknown>> = {};
  for (const domain of candidates) {
    const label = domain ?? "base";
    const typeDefs = loadTypeDefs(domain);
    const colTypes = classifyColumns(data, typeDefs);
    const matched = Object.values(colTypes).filter((c) => c.typeName !== null).length;
    const total = Math.max(Object.keys(colTypes).length, 1);
    const matchRate = matched / total;

    results[label] = {
      match_rate: Math.round(matchRate * 1000) / 1000,
      matched_columns: matched,
      total_columns: total,
    };
  }

  // Determine recommendation (highest match rate)
  let bestLabel = "base";
  let bestRate = 0;
  for (const [label, info] of Object.entries(results)) {
    const rate = info.match_rate as number;
    if (rate > bestRate) {
      bestRate = rate;
      bestLabel = label;
    }
  }

  return {
    domains_tested: candidates.map((d) => d ?? "base"),
    results,
    recommendation: bestLabel,
    reason:
      `'${bestLabel}' achieves the highest column match rate ` +
      `(${Math.round(bestRate * 100)}%) for semantic type detection.`,
  };
}
