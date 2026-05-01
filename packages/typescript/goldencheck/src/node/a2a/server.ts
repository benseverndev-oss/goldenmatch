/**
 * A2A (Agent-to-Agent) server — exposes GoldenCheck skills as HTTP endpoints.
 * Port of goldencheck/a2a/server.py. Node-only.
 *
 * Uses raw Node HTTP server — no framework dependency.
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readFile } from "../reader.js";
import { scanData } from "../../core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../core/engine/confidence.js";
import { autoTriage } from "../../core/engine/triage.js";
import { Severity, type Finding, type DatasetProfile, healthScore } from "../../core/types.js";
import { listAvailableDomains } from "../../core/semantic/domains/index.js";

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
    sample_values: f.sampleValues,
    confidence: f.confidence,
    source: f.source,
  };
}

// --- Skill Handlers ---

function handleScan(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  if (!filePath) return { error: "file_path required" };

  const data = readFile(filePath);
  const domain = params["domain"] as string | undefined;
  const result = scanData(data, { domain });
  const findings = applyConfidenceDowngrade(result.findings, false);
  const triage = autoTriage(findings);
  const { grade, points } = healthScore(findingsByColumn(findings));

  return {
    file: filePath,
    rows: result.profile.rowCount,
    columns: result.profile.columnCount,
    health_grade: grade,
    health_score: points,
    findings: findings.map(serializeFinding),
    triage: {
      pinned: triage.pin.length,
      review: triage.review.length,
      dismissed: triage.dismiss.length,
    },
  };
}

function handleAnalyzeData(params: Record<string, unknown>): object {
  const filePath = params["file_path"] as string;
  if (!filePath) return { error: "file_path required" };

  const data = readFile(filePath);

  // Simple domain detection — score each domain's name hints
  const domains = listAvailableDomains();
  const colNames = data.columns.map((c) => c.toLowerCase());
  const scores: Record<string, number> = {};

  for (const domain of domains) {
    const { getDomainTypes } = require("../../core/semantic/domains/index.js");
    const types = getDomainTypes(domain);
    if (!types) continue;
    let hits = 0;
    for (const def of Object.values(types) as Array<{ nameHints: string[] }>) {
      for (const hint of def.nameHints) {
        if (colNames.some((c) => c.includes(hint.toLowerCase()))) hits++;
      }
    }
    scores[domain] = data.columns.length > 0 ? hits / data.columns.length : 0;
  }

  const best = Object.entries(scores).sort((a, b) => b[1] - a[1])[0];

  return {
    file: filePath,
    rows: data.rowCount,
    columns: data.columns.length,
    column_names: data.columns,
    strategy: {
      domain: best && best[1] > 0.2 ? best[0] : null,
      domain_confidence: best ? best[1] : 0,
      sample_strategy: data.rowCount <= 50000 ? "full" : "sample_100k",
      profiler_strategy: data.columns.length <= 20 ? "standard" : data.columns.length <= 80 ? "parallel_batched" : "wide_table",
    },
    domain_scores: scores,
  };
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

function dispatchSkill(skillId: string, params: Record<string, unknown>): object {
  switch (skillId) {
    case "scan": return handleScan(params);
    case "analyze_data": return handleAnalyzeData(params);
    case "compare_domains": return handleCompareDomains(params);
    default: return { error: `Unknown skill: ${skillId}` };
  }
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
    const params = body.params ?? body.message?.parts?.[0]?.content ?? {};

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
    const params = body.params ?? {};

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
