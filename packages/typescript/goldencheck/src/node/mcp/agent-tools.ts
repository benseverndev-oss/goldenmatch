/**
 * mcp/agent-tools.ts -- 10 agent-level MCP tools for GoldenCheck.
 *
 * Parity with goldencheck/mcp/agent_tools.py: strategy analysis, auto-config,
 * finding/column explanation, review queue, domain comparison, fix preview, and
 * pipeline handoff. Wires the existing TS agent + engine primitives.
 *
 * Node-only: reads files via ../reader.js. Handlers are synchronous and return
 * plain objects (matching the GoldenCheck MCP server convention); errors are
 * trapped and returned as `{ error }` rather than thrown.
 */

import { existsSync } from "node:fs";

import { readFile } from "../reader.js";
import { scanData } from "../../core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../core/engine/confidence.js";
import { autoTriage } from "../../core/engine/triage.js";
import { applyFixes } from "../../core/engine/fixer.js";
import {
  selectStrategy,
  buildAlternatives,
  explainFinding,
  explainColumn,
  compareDomains,
  generateHandoff,
  ReviewQueue,
} from "../../core/agent/index.js";
import { Severity, severityLabel, type Finding } from "../../core/types.js";

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Tool definitions (mirror Python agent_tools.py)
// ---------------------------------------------------------------------------

export const AGENT_TOOLS: readonly Tool[] = [
  {
    name: "analyze_data",
    description:
      "Analyze a data file to detect its domain, profile columns, and recommend a " +
      "scanning strategy. Returns domain detection, column/row counts, strategy " +
      "decisions, and alternative approaches.",
    inputSchema: {
      type: "object",
      properties: { file_path: { type: "string", description: "Path to the data file (CSV, Parquet)" } },
      required: ["file_path"],
    },
  },
  {
    name: "auto_configure",
    description:
      "Scan a data file, triage findings by confidence, and generate goldencheck.yml " +
      "content from the pinned findings. Optionally accepts constraints to filter the config.",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Path to the data file" },
        constraints: {
          type: "object",
          description: "Optional: { min_confidence, severity_filter, include_columns, exclude_columns }",
        },
      },
      required: ["file_path"],
    },
  },
  {
    name: "explain_finding",
    description:
      "Explain a single finding in natural language. Requires the finding as a JSON " +
      "dict and the file_path to load a profile for context.",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Path to the data file (for profile context)" },
        finding: {
          type: "object",
          description: "Finding dict: severity, column, check, message, affected_rows, confidence, sample_values",
        },
      },
      required: ["file_path", "finding"],
    },
  },
  {
    name: "explain_column",
    description:
      "Get a natural-language health narrative for a specific column. Scans the file, " +
      "profiles the column, and explains all findings.",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Path to the data file" },
        column: { type: "string", description: "Column name to explain" },
      },
      required: ["file_path", "column"],
    },
  },
  {
    name: "review_queue",
    description: "List all pending review items for a given job (medium-confidence findings needing a decision).",
    inputSchema: {
      type: "object",
      properties: { job_name: { type: "string", description: "Job name to filter review items" } },
      required: ["job_name"],
    },
  },
  {
    name: "approve_reject",
    description: "Approve (pin) or reject (dismiss) a review queue item. Decision must be 'pin' or 'dismiss'.",
    inputSchema: {
      type: "object",
      properties: {
        item_id: { type: "string", description: "Review item ID to update" },
        decision: { type: "string", description: "'pin' (approve) or 'dismiss' (reject)", enum: ["pin", "dismiss"] },
        reason: { type: "string", description: "Optional reason for the decision" },
      },
      required: ["item_id", "decision"],
    },
  },
  {
    name: "compare_domains",
    description:
      "Scan a file with every available domain pack (plus base/no-domain) and compare " +
      "health scores. Recommends the best-fitting domain.",
    inputSchema: {
      type: "object",
      properties: { file_path: { type: "string", description: "Path to the data file" } },
      required: ["file_path"],
    },
  },
  {
    name: "suggest_fix",
    description:
      "Preview fixes for a data file without applying them. Shows what would change " +
      "(columns, fix types, rows affected, before/after samples).",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Path to the data file" },
        mode: { type: "string", description: "'safe' (default) or 'aggressive'", default: "safe", enum: ["safe", "aggressive"] },
      },
      required: ["file_path"],
    },
  },
  {
    name: "pipeline_handoff",
    description:
      "Generate a structured quality attestation for a data file: health score, findings " +
      "summary, pinned rules, and attestation status (PASS / PASS_WITH_WARNINGS / REVIEW_REQUIRED / FAIL).",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Path to the data file" },
        job_name: { type: "string", description: "Job name for the handoff record" },
      },
      required: ["file_path", "job_name"],
    },
  },
  {
    name: "review_stats",
    description: "Get review queue statistics for a job — counts of pending, pinned, and dismissed items.",
    inputSchema: {
      type: "object",
      properties: { job_name: { type: "string", description: "Job name to get stats for" } },
      required: ["job_name"],
    },
  },
];

export const AGENT_TOOL_NAMES: ReadonlySet<string> = new Set(AGENT_TOOLS.map((t) => t.name));

// ---------------------------------------------------------------------------
// Shared review queue (created on first use, mirrors Python's module singleton)
// ---------------------------------------------------------------------------

let _reviewQueue: ReviewQueue | null = null;
function getReviewQueue(): ReviewQueue {
  if (_reviewQueue === null) _reviewQueue = new ReviewQueue();
  return _reviewQueue;
}

/** Test seam: reset the shared review queue between tests. */
export function __resetReviewQueueForTests(): void {
  _reviewQueue = null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function severityFromName(name: unknown): Severity {
  switch (String(name).toUpperCase()) {
    case "ERROR":
      return Severity.ERROR;
    case "WARNING":
      return Severity.WARNING;
    default:
      return Severity.INFO;
  }
}

function findingFromDict(d: Record<string, unknown>): Finding {
  return {
    severity: severityFromName(d["severity"]),
    column: typeof d["column"] === "string" ? (d["column"] as string) : "",
    check: typeof d["check"] === "string" ? (d["check"] as string) : "",
    message: typeof d["message"] === "string" ? (d["message"] as string) : "",
    affectedRows: typeof d["affected_rows"] === "number" ? (d["affected_rows"] as number) : 0,
    sampleValues: Array.isArray(d["sample_values"]) ? (d["sample_values"] as string[]).map(String) : [],
    suggestion: typeof d["suggestion"] === "string" ? (d["suggestion"] as string) : null,
    pinned: d["pinned"] === true,
    source: typeof d["source"] === "string" ? (d["source"] as string) : null,
    confidence: typeof d["confidence"] === "number" ? (d["confidence"] as number) : 1.0,
    metadata: (d["metadata"] as Record<string, unknown>) ?? {},
  };
}

/** Minimal YAML emitter for the fixed goldencheck.yml config shape. */
function rulesToYaml(
  rules: ReadonlyArray<Record<string, unknown>>,
  rowCount: number,
  columnCount: number,
): string {
  const q = (v: unknown): string => JSON.stringify(v ?? null);
  const lines: string[] = [
    "version: 1",
    "dataset:",
    `  row_count: ${rowCount}`,
    `  column_count: ${columnCount}`,
    "rules:",
  ];
  for (const r of rules) {
    lines.push(`  - column: ${q(r["column"])}`);
    lines.push(`    check: ${q(r["check"])}`);
    lines.push(`    severity: ${String(r["severity"])}`);
    lines.push(`    message: ${q(r["message"])}`);
    if (r["suggestion"]) lines.push(`    suggestion: ${q(r["suggestion"])}`);
  }
  return lines.join("\n") + "\n";
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

export function handleAgentTool(name: string, args: Record<string, unknown>): object {
  try {
    return dispatch(name, args ?? {});
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

function dispatch(name: string, args: Record<string, unknown>): object {
  // Tools that read a file share the existence guard.
  const filePath = typeof args["file_path"] === "string" ? (args["file_path"] as string) : "";
  const needsFile = name !== "review_queue" && name !== "approve_reject" && name !== "review_stats";
  if (needsFile) {
    if (!filePath) return { error: "Missing required parameter: file_path" };
    if (!existsSync(filePath)) return { error: `File not found: ${filePath}` };
  }

  if (name === "analyze_data") {
    const data = readFile(filePath);
    const decision = selectStrategy(data);
    const scores = (decision.why["domain_scores"] as Record<string, number>) ?? {};
    return {
      file: filePath,
      rows: data.rowCount,
      columns: data.columns.length,
      column_names: data.columns,
      strategy: {
        domain: decision.domain,
        domain_confidence: Math.round(decision.domainConfidence * 1000) / 1000,
        sample_strategy: decision.sampleStrategy,
        profiler_strategy: decision.profilerStrategy,
        llm_boost: decision.llmBoost,
      },
      reasoning: decision.why,
      alternatives: buildAlternatives(decision, scores),
    };
  }

  if (name === "auto_configure") {
    const constraints = (args["constraints"] as Record<string, unknown>) ?? {};
    const data = readFile(filePath);
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    const triage = autoTriage(findings);

    let pinned: Finding[] = [...triage.pin];
    const minConf = typeof constraints["min_confidence"] === "number" ? (constraints["min_confidence"] as number) : 0;
    if (minConf > 0) pinned = pinned.filter((f) => f.confidence >= minConf);
    const sevFilter = constraints["severity_filter"];
    if (typeof sevFilter === "string") {
      const target = severityFromName(sevFilter);
      pinned = pinned.filter((f) => f.severity >= target);
    }
    const include = constraints["include_columns"];
    if (Array.isArray(include)) pinned = pinned.filter((f) => (include as unknown[]).includes(f.column));
    const exclude = constraints["exclude_columns"];
    if (Array.isArray(exclude)) pinned = pinned.filter((f) => !(exclude as unknown[]).includes(f.column));

    const rules = pinned.map((f) => {
      const rule: Record<string, unknown> = {
        column: f.column,
        check: f.check,
        severity: severityLabel(f.severity),
        message: f.message,
      };
      if (f.suggestion) rule["suggestion"] = f.suggestion;
      if (f.metadata && Object.keys(f.metadata).length > 0) rule["metadata"] = f.metadata;
      return rule;
    });

    return {
      file: filePath,
      pinned_count: pinned.length,
      review_count: triage.review.length,
      dismissed_count: triage.dismiss.length,
      rules,
      yaml_content: rulesToYaml(rules, result.profile.rowCount, result.profile.columnCount),
    };
  }

  if (name === "explain_finding") {
    const findingDict = args["finding"];
    if (typeof findingDict !== "object" || findingDict === null) {
      return { error: "Missing or invalid required parameter: finding" };
    }
    const data = readFile(filePath);
    const result = scanData(data);
    return explainFinding(findingFromDict(findingDict as Record<string, unknown>), result.profile);
  }

  if (name === "explain_column") {
    const column = typeof args["column"] === "string" ? (args["column"] as string) : "";
    if (!column) return { error: "Missing required parameter: column" };
    const data = readFile(filePath);
    const result = scanData(data);
    return explainColumn(data, column, result.findings, result.profile);
  }

  if (name === "review_queue") {
    const jobName = typeof args["job_name"] === "string" ? (args["job_name"] as string) : "";
    if (!jobName) return { error: "Missing required parameter: job_name" };
    const items = getReviewQueue()
      .pending(jobName)
      .map((it) => ({
        item_id: it.itemId,
        column: it.column,
        check: it.check,
        severity: it.severity,
        confidence: it.confidence,
        message: it.message,
        explanation: it.explanation,
        sample_values: it.sampleValues,
      }));
    return { job_name: jobName, pending_count: items.length, items };
  }

  if (name === "approve_reject") {
    const itemId = typeof args["item_id"] === "string" ? (args["item_id"] as string) : "";
    const decision = args["decision"];
    if (!itemId) return { error: "Missing required parameter: item_id" };
    if (decision !== "pin" && decision !== "dismiss") {
      return { error: "decision must be 'pin' or 'dismiss'" };
    }
    const reason = typeof args["reason"] === "string" ? (args["reason"] as string) : "";
    const queue = getReviewQueue();
    try {
      if (decision === "pin") queue.approve(itemId, "mcp_agent", reason);
      else queue.reject(itemId, "mcp_agent", reason);
    } catch {
      return { error: `Review item not found: ${itemId}` };
    }
    return { item_id: itemId, decision, reason, status: "updated" };
  }

  if (name === "compare_domains") {
    const data = readFile(filePath);
    return compareDomains(data);
  }

  if (name === "suggest_fix") {
    const mode = args["mode"] === "aggressive" ? "aggressive" : "safe";
    const data = readFile(filePath);
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    const { report } = applyFixes(data, findings, mode, mode === "aggressive");
    const fixes = report.entries.map((e) => ({
      column: e.column,
      fix_type: e.fixType,
      rows_affected: e.rowsAffected,
      sample_before: e.sampleBefore.slice(0, 5),
      sample_after: e.sampleAfter.slice(0, 5),
    }));
    return {
      file: filePath,
      mode,
      total_fixes: fixes.length,
      total_rows_fixed: report.totalRowsFixed,
      fixes,
    };
  }

  if (name === "pipeline_handoff") {
    const jobName = typeof args["job_name"] === "string" ? (args["job_name"] as string) : "";
    if (!jobName) return { error: "Missing required parameter: job_name" };
    const data = readFile(filePath);
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    const triage = autoTriage(findings);
    const pinnedRules = triage.pin.map((f) => ({
      column: f.column,
      check: f.check,
      severity: severityLabel(f.severity),
      message: f.message,
    }));
    return generateHandoff({
      filePath,
      findings,
      profile: result.profile,
      pinnedRules,
      reviewPending: triage.review.length,
      dismissed: triage.dismiss.length,
      jobName,
    });
  }

  if (name === "review_stats") {
    const jobName = typeof args["job_name"] === "string" ? (args["job_name"] as string) : "";
    if (!jobName) return { error: "Missing required parameter: job_name" };
    const stats = getReviewQueue().stats(jobName);
    return { job_name: jobName, ...stats };
  }

  return { error: `Unknown agent tool: ${name}` };
}
