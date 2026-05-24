/**
 * a2a/server.ts -- GoldenPipe A2A (Agent-to-Agent) protocol server.
 *
 * Node-only: uses node:http, node:crypto. NOT edge-safe.
 *
 * Port of goldenpipe/a2a/server.py. Mirrors the sibling GoldenFlow / GoldenMatch
 * TS A2A servers for structure (agent card at /.well-known/agent.json, skill
 * dispatch, task lifecycle). Skills delegate to the GoldenPipe MCP tools via
 * `handleTool`, exactly as the Python server delegates to its MCP tool functions.
 *
 * Endpoints:
 *   GET  /.well-known/agent.json   - agent card (4 skills)
 *   GET  /health                   - liveness probe
 *   POST /tasks                    - create a task (skill + params)
 *   GET  /tasks/{id}               - fetch task status/result
 */

import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { randomUUID } from "node:crypto";
import { handleTool } from "../mcp/server.js";

// ---------------------------------------------------------------------------
// Agent card
// ---------------------------------------------------------------------------

export interface AgentSkill {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly inputModes: readonly string[];
  readonly outputModes: readonly string[];
}

export const AGENT_CARD: {
  readonly name: string;
  readonly description: string;
  readonly version: string;
  readonly provider: { readonly organization: string };
  readonly url: string;
  readonly skills: readonly AgentSkill[];
} = {
  name: "GoldenPipe",
  description: "Pluggable pipeline framework for data quality workflows",
  version: "1.0.0",
  provider: { organization: "Golden Suite" },
  url: "http://localhost:8250",
  skills: [
    {
      id: "run-pipeline",
      name: "Run Pipeline",
      description: "Execute a data quality pipeline on a CSV file",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "validate-pipeline",
      name: "Validate Pipeline",
      description: "Validate pipeline wiring without executing",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "list-stages",
      name: "List Stages",
      description: "List all registered pipeline stages",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "explain-pipeline",
      name: "Explain Pipeline",
      description: "Describe what a pipeline config will do",
      inputModes: ["text"],
      outputModes: ["text"],
    },
  ],
};

// ---------------------------------------------------------------------------
// Task store
// ---------------------------------------------------------------------------

interface Task {
  readonly id: string;
  readonly skill: string;
  status: "pending" | "running" | "completed" | "failed";
  readonly createdAt: string;
  completedAt?: string;
  result?: unknown;
  error?: string;
}

// ---------------------------------------------------------------------------
// Skill dispatch
// ---------------------------------------------------------------------------

/** A2A skill id (kebab-case) -> MCP tool name (snake_case). */
const SKILL_TO_TOOL: Record<string, string> = {
  "run-pipeline": "run_pipeline",
  "validate-pipeline": "validate_pipeline",
  "list-stages": "list_stages",
  "explain-pipeline": "explain_pipeline",
};

async function dispatchSkill(
  skill: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  const tool = SKILL_TO_TOOL[skill];
  if (tool === undefined) {
    return { error: `Unknown skill: ${skill}` };
  }
  return handleTool(tool, params);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  let body = "";
  for await (const chunk of req) {
    body += typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
  }
  if (!body) return {};
  const parsed = JSON.parse(body);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("body must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

function sendJson(res: ServerResponse, status: number, data: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(data));
}

// ---------------------------------------------------------------------------
// Public: startA2aServer
// ---------------------------------------------------------------------------

export interface StartA2aOptions {
  readonly port?: number;
  readonly host?: string;
}

export function startA2aServer(
  options: StartA2aOptions = {},
): ReturnType<typeof createServer> {
  const port = options.port ?? 8250;
  const host = options.host ?? "127.0.0.1";
  const tasks = new Map<string, Task>();

  const server = createServer((req, res) => {
    void (async () => {
      const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
      const pathname = url.pathname;
      const methodName = req.method ?? "GET";

      try {
        if (pathname === "/.well-known/agent.json" && methodName === "GET") {
          sendJson(res, 200, AGENT_CARD);
          return;
        }

        if (pathname === "/health" && methodName === "GET") {
          sendJson(res, 200, { status: "ok", version: AGENT_CARD.version });
          return;
        }

        if (pathname === "/tasks" && methodName === "POST") {
          const body = await readJsonBody(req);
          const skill = String(body["skill"] ?? "");
          const params =
            (body["params"] as Record<string, unknown> | undefined) ??
            (body["input"] as Record<string, unknown> | undefined) ??
            {};
          if (!skill) {
            sendJson(res, 400, { error: "skill is required" });
            return;
          }
          const id = String(body["id"] ?? randomUUID());
          const createdAt = new Date().toISOString();
          const task: Task = { id, skill, status: "running", createdAt };
          tasks.set(id, task);

          try {
            const result = await dispatchSkill(skill, params);
            task.status = "completed";
            task.completedAt = new Date().toISOString();
            task.result = result;
            sendJson(res, 200, { id, status: "completed", result });
          } catch (err) {
            // Log detail server-side; don't return exception/stack text to the
            // client (CodeQL js/stack-trace-exposure).
            console.error("[goldenpipe-a2a] skill failed:", err);
            task.status = "failed";
            task.completedAt = new Date().toISOString();
            task.error = "skill execution failed";
            sendJson(res, 200, { id, status: "failed", error: "skill execution failed" });
          }
          return;
        }

        if (pathname.startsWith("/tasks/") && methodName === "GET") {
          const id = pathname.slice("/tasks/".length);
          const task = tasks.get(id);
          if (!task) {
            sendJson(res, 404, { error: `Task not found: ${id}` });
            return;
          }
          sendJson(res, 200, {
            id: task.id,
            skill: task.skill,
            status: task.status,
            created_at: task.createdAt,
            completed_at: task.completedAt ?? null,
            result: task.result ?? null,
            error: task.error ?? null,
          });
          return;
        }

        sendJson(res, 404, { error: `Not found: ${methodName} ${pathname}` });
      } catch (err) {
        console.error("[goldenpipe-a2a] request error:", err);
        sendJson(res, 500, { error: "internal server error" });
      }
    })();
  });

  server.listen(port, host, () => {
    console.log(`GoldenPipe A2A agent listening on http://${host}:${port}`);
  });
  return server;
}

export function runServer(port: number = 8250): ReturnType<typeof createServer> {
  return startA2aServer({ port });
}
