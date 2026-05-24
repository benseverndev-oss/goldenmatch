/**
 * A2A (Agent-to-Agent) server — exposes GoldenCheck skills as HTTP endpoints.
 * Port of goldencheck/a2a/server.py. Node-only.
 *
 * Uses raw Node HTTP server — no framework dependency.
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { existsSync, readFileSync } from "node:fs";
import { readFile } from "../reader.js";
import { scanData } from "../../core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../core/engine/confidence.js";
import { autoTriage } from "../../core/engine/triage.js";
import { validateData } from "../../core/engine/validator.js";
import { applyFixes } from "../../core/engine/fixer.js";
import { Severity, type Finding, healthScore } from "../../core/types.js";
import { validateConfig } from "../../core/config/schema.js";
import { listAvailableDomains } from "../../core/semantic/domains/index.js";
import {
  selectStrategy,
  buildAlternatives,
  explainFinding,
  findingsToFbc,
} from "../../core/agent/intelligence.js";
import { generateHandoff } from "../../core/agent/handoff.js";
import { ReviewQueue, type ReviewItem } from "../../core/agent/review-queue.js";

// --- Agent Card ---

const AGENT_CARD = {
  name: "goldencheck-agent",
  version: "1.0.0",
  description: "Data validation agent that discovers rules from your data.",
  skills: [
    { id: "scan", description: "Scan a data file for quality issues" },
    { id: "validate", description: "Validate against pinned rules" },
    { id: "analyze_data", description: "Detect domain and recommend strategy" },
    { id: "explain", description: "Explain a finding in natural language" },
    { id: "compare_domains", description: "Compare domain pack fits" },
    { id: "fix", description: "Preview or apply data fixes" },
    { id: "handoff", description: "Generate pipeline attestation" },
    { id: "review", description: "List and manage review queue items" },
    { id: "configure", description: "Auto-generate goldencheck.yml" },
  ],
  auth: { type: "bearer", env: "GOLDENCHECK_AGENT_TOKEN" },
  streaming: true,
};

// --- Task Registry ---

interface TaskEntry {
  id: string;
  state: "working" | "completed" | "failed";
  skill: string;
  result: unknown;
  error: string | null;
}

const taskRegistry = new Map<string, TaskEntry>();
let nextTaskId = 1;

// --- Helpers ---

function findingsByColumn(findings: readonly Finding[]): Record<string, { errors: number; warnings: number }> {
  const byCol: Record<string, { errors: number; warnings: number }> = {};
  for (const f of findings) {
    if (f.severity >= Severity.WARNING) {
      if (!byCol[f.column]) byCol[f.column] = { errors: 0, warnings: 0 };
      if (f.severity === Severity.ERROR) byCol[f.column]!.errors++;
      else byCol[f.column]!.warnings++;
    }
  }
  return byCol;
}

function serializeFinding(f: Finding): object {
  return {
    severity: f.severity === Severity.ERROR ? "ERROR" : f.severity === Severity.WARNING ? "WARNING" : "INFO",
    column: f.column,
    check: f.check,
    message: f.message,
    affected_rows: f.affectedRows,
    sample_values: f.sampleValues.slice(0, 5),
    suggestion: f.suggestion,
    confidence: Math.round(f.confidence * 10000) / 10000,
    source: f.source,
  };
}

function serializeReviewItem(item: ReviewItem): object {
  return {
    item_id: item.itemId,
    column: item.column,
    check: item.check,
    severity: item.severity,
    confidence: Math.round(item.confidence * 10000) / 10000,
    message: item.message,
    explanation: item.explanation,
    status: item.status,
  };
}

// Shared review queue (memory backend for the server lifetime). Mirrors the
// module-level `_review_queue` in goldencheck/a2a/skills.py.
const reviewQueue = new ReviewQueue();

let _jobCounter = 0;
function autoJobName(): string {
  _jobCounter += 1;
  return `a2a-${Date.now().toString(36)}${_jobCounter.toString(36)}`;
}

// --- Skill Handlers ---

function handleScan(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  if (!filePath) return { error: "file_path is required" };

  const data = readFile(filePath);
  const domain = params["domain"] as string | undefined;
  const jobName = (params["job_name"] as string) || autoJobName();

  const result = scanData(data, { domain });
  const findings = applyConfidenceDowngrade(result.findings, false);

  // Classify into the shared review queue.
  const classified = reviewQueue.classifyFindings(findings, jobName);

  const fbc = findingsToFbc(findings);
  const { grade, points } = healthScore(fbc);

  return {
    job_name: jobName,
    row_count: result.profile.rowCount,
    column_count: result.profile.columnCount,
    health: { grade, score: points },
    total_findings: findings.length,
    errors: findings.filter((f) => f.severity === Severity.ERROR).length,
    warnings: findings.filter((f) => f.severity === Severity.WARNING).length,
    infos: findings.filter((f) => f.severity === Severity.INFO).length,
    auto_pinned: classified.pinned.length,
    review_queue: classified.review.length,
    auto_dismissed: classified.dismissed.length,
    findings: findings.map(serializeFinding),
  };
}

function handleAnalyzeData(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  if (!filePath) return { error: "file_path is required" };

  const data = readFile(filePath);
  const decision = selectStrategy(data);
  const domainScores = (decision.why["domain_scores"] as Record<string, number>) ?? {};
  const alternatives = buildAlternatives(decision, domainScores);

  return {
    domain: decision.domain,
    domain_confidence: Math.round(decision.domainConfidence * 10000) / 10000,
    sample_strategy: decision.sampleStrategy,
    profiler_strategy: decision.profilerStrategy,
    llm_boost: decision.llmBoost,
    why: decision.why,
    alternatives,
  };
}

function handleValidate(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  const configPath = (params["config_path"] as string) || "goldencheck.yml";
  if (!filePath) return { error: "file_path is required" };

  if (!existsSync(configPath)) {
    return { error: `Config not found at ${configPath}` };
  }
  let config;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const yaml = require("yaml") as { parse(s: string): unknown };
    config = validateConfig(yaml.parse(readFileSync(configPath, "utf-8")));
  } catch (e) {
    return { error: `Failed to load config: ${e instanceof Error ? e.message : String(e)}` };
  }

  const data = readFile(filePath);
  const findings = validateData(data, config);
  return {
    total_findings: findings.length,
    errors: findings.filter((f) => f.severity === Severity.ERROR).length,
    warnings: findings.filter((f) => f.severity === Severity.WARNING).length,
    findings: findings.map(serializeFinding),
  };
}

function handleExplain(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  const column = params["column"] as string;
  const check = params["check"] as string;
  if (!filePath || !column || !check) {
    return { error: "file_path, column, and check are required" };
  }

  const data = readFile(filePath);
  const result = scanData(data);
  const findings = applyConfidenceDowngrade(result.findings, false);
  const target = findings.find((f) => f.column === column && f.check === check);
  if (!target) {
    return { error: `No finding for column=${JSON.stringify(column)}, check=${JSON.stringify(check)}` };
  }
  return explainFinding(target, result.profile);
}

function handleReview(params: Record<string, unknown>): object {
  const jobName = (params["job_name"] as string) || "";
  const action = (params["action"] as string) || "list";

  if (action === "list") {
    if (!jobName) return { error: "job_name is required for listing review items" };
    const pending = reviewQueue.pending(jobName);
    const stats = reviewQueue.stats(jobName);
    return {
      job_name: jobName,
      stats,
      pending: pending.map(serializeReviewItem),
    };
  }

  if (action === "approve" || action === "reject") {
    const itemId = (params["item_id"] as string) || "";
    const decidedBy = (params["decided_by"] as string) || "a2a-agent";
    const reason = (params["reason"] as string) || "";
    if (!itemId) return { error: "item_id is required for approve/reject" };
    try {
      if (action === "approve") reviewQueue.approve(itemId, decidedBy, reason);
      else reviewQueue.reject(itemId, decidedBy, reason);
      return { item_id: itemId, action, status: "ok" };
    } catch (e) {
      return { error: e instanceof Error ? e.message : String(e) };
    }
  }

  return { error: `Unknown review action: ${JSON.stringify(action)}` };
}

function handleConfigure(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  if (!filePath) return { error: "file_path is required" };

  const domain = params["domain"] as string | undefined;
  const data = readFile(filePath);
  const result = scanData(data, { domain });
  const findings = applyConfidenceDowngrade(result.findings, false);
  const triage = autoTriage(findings);

  const columns: Record<string, Record<string, unknown>> = {};
  for (const f of triage.pin) {
    const colCfg = (columns[f.column] ??= {});
    if (f.check === "nullability" && !("required" in colCfg)) {
      colCfg.required = true;
    } else if (f.check === "uniqueness" && !("unique" in colCfg)) {
      colCfg.unique = true;
    } else if (f.check === "range_distribution" && Object.keys(f.metadata).length > 0) {
      const lo = f.metadata["expected_min"];
      const hi = f.metadata["expected_max"];
      if (lo != null || hi != null) {
        colCfg.range = [lo ?? null, hi ?? null];
      }
    }
  }

  const configDict: Record<string, unknown> = { version: 1, columns };
  if (domain) configDict.domain = domain;

  return {
    config: configDict,
    pinned_count: triage.pin.length,
    review_count: triage.review.length,
    dismissed_count: triage.dismiss.length,
  };
}

function handleFix(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  const mode = (params["mode"] as "safe" | "moderate" | "aggressive") || "safe";
  if (!filePath) return { error: "file_path is required" };

  const data = readFile(filePath);
  const result = scanData(data);
  const findings = applyConfidenceDowngrade(result.findings, false);

  let report;
  try {
    ({ report } = applyFixes(data, findings, mode));
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }

  return {
    mode,
    total_rows_fixed: report.totalRowsFixed,
    fixes: report.entries.map((e) => ({
      column: e.column,
      fix_type: e.fixType,
      rows_affected: e.rowsAffected,
      sample_before: e.sampleBefore.slice(0, 3),
      sample_after: e.sampleAfter.slice(0, 3),
    })),
  };
}

function handleHandoff(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  const jobName = (params["job_name"] as string) || autoJobName();
  if (!filePath) return { error: "file_path is required" };

  const data = readFile(filePath);
  const result = scanData(data);
  const findings = applyConfidenceDowngrade(result.findings, false);
  const triage = autoTriage(findings);

  const pinnedRules = triage.pin.map((f) => ({
    column: f.column,
    check: f.check,
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

function handleCompareDomains(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  if (!filePath) return { error: "file_path required" };

  const data = readFile(filePath);
  const results: Array<{ domain: string | null; grade: string; score: number; findings: number }> = [];

  // Base scan (no domain)
  const base = scanData(data);
  const baseFindings = applyConfidenceDowngrade(base.findings, false);
  const baseHealth = healthScore(findingsByColumn(baseFindings));
  results.push({ domain: null, grade: baseHealth.grade, score: baseHealth.points, findings: baseFindings.length });

  // Per-domain scans
  for (const domain of listAvailableDomains()) {
    const result = scanData(data, { domain });
    const findings = applyConfidenceDowngrade(result.findings, false);
    const h = healthScore(findingsByColumn(findings));
    results.push({ domain, grade: h.grade, score: h.points, findings: findings.length });
  }

  results.sort((a, b) => b.score - a.score);
  return { results, recommendation: results[0]?.domain ?? null };
}

/**
 * Route a skill request to the appropriate handler. Mirrors the dispatch
 * table in goldencheck/a2a/skills.py.
 */
export function dispatchSkill(skillId: string, params: Record<string, unknown>): object {
  switch (skillId) {
    case "analyze_data": return handleAnalyzeData(params);
    case "scan": return handleScan(params);
    case "validate": return handleValidate(params);
    case "explain": return handleExplain(params);
    case "review": return handleReview(params);
    case "configure": return handleConfigure(params);
    case "fix": return handleFix(params);
    case "compare_domains": return handleCompareDomains(params);
    case "handoff": return handleHandoff(params);
    default: return { error: `Unknown skill: ${skillId}` };
  }
}

/** Extract structured params from an A2A message's first data part. */
export function extractParams(message: Record<string, unknown>): Record<string, unknown> {
  const parts = (message["parts"] as unknown[]) ?? [];
  for (const part of parts) {
    if (part && typeof part === "object") {
      const p = part as Record<string, unknown>;
      if (p["type"] === "data") return (p["data"] as Record<string, unknown>) ?? {};
      if (!("type" in p)) return p;
    }
  }
  return {};
}

// --- HTTP Server ---

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString()));
    req.on("error", reject);
  });
}

function jsonResponse(res: ServerResponse, data: unknown, status: number = 200): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data, null, 2));
}

function sseEncode(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

async function handleRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", `http://${req.headers.host}`);

  // Auth check
  const token = process.env["GOLDENCHECK_AGENT_TOKEN"];
  if (token) {
    const auth = req.headers.authorization;
    if (!auth || auth !== `Bearer ${token}`) {
      jsonResponse(res, { error: "Unauthorized" }, 401);
      return;
    }
  }

  // Routes
  if (url.pathname === "/.well-known/agent.json" && req.method === "GET") {
    jsonResponse(res, { ...AGENT_CARD, url: `http://${req.headers.host}` });
    return;
  }

  if (url.pathname === "/tasks/send" && req.method === "POST") {
    let body: any;
    try {
      body = JSON.parse(await readBody(req));
    } catch {
      jsonResponse(res, { error: "Invalid JSON in request body" }, 400);
      return;
    }
    const skillId = body.skill ?? body.skill_id;
    const params = body.params ?? (body.message ? extractParams(body.message) : {});

    const taskId = String(nextTaskId++);
    taskRegistry.set(taskId, { id: taskId, state: "working", skill: skillId, result: null, error: null });

    try {
      const result = dispatchSkill(skillId, params);
      taskRegistry.set(taskId, { id: taskId, state: "completed", skill: skillId, result, error: null });
      jsonResponse(res, { task_id: taskId, state: "completed", result });
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      taskRegistry.set(taskId, { id: taskId, state: "failed", skill: skillId, result: null, error });
      jsonResponse(res, { task_id: taskId, state: "failed", error }, 500);
    }
    return;
  }

  if (url.pathname === "/tasks/sendSubscribe" && req.method === "POST") {
    let body: any;
    try {
      body = JSON.parse(await readBody(req));
    } catch {
      jsonResponse(res, { error: "Invalid JSON in request body" }, 400);
      return;
    }
    const skillId = body.skill ?? body.skill_id;
    const params = body.params ?? (body.message ? extractParams(body.message) : {});

    const taskId = String(nextTaskId++);
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    res.write(sseEncode("task.started", { task_id: taskId, skill: skillId }));

    try {
      const result = dispatchSkill(skillId, params);
      taskRegistry.set(taskId, { id: taskId, state: "completed", skill: skillId, result, error: null });
      res.write(sseEncode("task.completed", { task_id: taskId, result }));
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      taskRegistry.set(taskId, { id: taskId, state: "failed", skill: skillId, result: null, error });
      res.write(sseEncode("task.failed", { task_id: taskId, error }));
    }

    res.end();
    return;
  }

  if (url.pathname.startsWith("/tasks/") && req.method === "GET") {
    const taskId = url.pathname.split("/")[2];
    const task = taskId ? taskRegistry.get(taskId) : undefined;
    if (!task) {
      jsonResponse(res, { error: "Task not found" }, 404);
      return;
    }
    jsonResponse(res, task);
    return;
  }

  jsonResponse(res, { error: "Not found" }, 404);
}

/**
 * Create and start the A2A server.
 */
export function runA2aServer(port: number = 8100): void {
  const server = createServer((req, res) => {
    handleRequest(req, res).catch((e) => {
      console.error("A2A server error:", e);
      if (!res.headersSent) {
        jsonResponse(res, { error: "Internal server error" }, 500);
      }
    });
  });

  server.listen(port, () => {
    console.log(`GoldenCheck A2A server running on http://localhost:${port}`);
    console.log(`Agent card: http://localhost:${port}/.well-known/agent.json`);
  });

  process.on("SIGINT", () => { server.close(); process.exit(0); });
  process.on("SIGTERM", () => { server.close(); process.exit(0); });
}
