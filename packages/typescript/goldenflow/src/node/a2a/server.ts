/**
 * a2a/server.ts -- GoldenFlow A2A (Agent-to-Agent) protocol server.
 *
 * Node-only: uses node:http, node:crypto. NOT edge-safe.
 *
 * Port of goldenflow/a2a/server.py. Mirrors the goldenmatch / goldencheck
 * TS A2A servers for structure (agent card at /.well-known/agent.json, skill
 * dispatch, task lifecycle). Skills delegate to the GoldenFlow MCP tools via
 * `handleTool`, exactly as the Python server delegates to its MCP `handle_tool`.
 *
 * Endpoints:
 *   GET  /.well-known/agent.json   - agent card (6 skills)
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
  name: "GoldenFlow",
  description:
    "Data transformation -- standardize, clean, and normalize data with auto-detection and domain-aware transforms",
  version: "1.0.0",
  provider: { organization: "Golden Suite" },
  url: "http://localhost:8150",
  skills: [
    {
      id: "transform-data",
      name: "Transform Data",
      description:
        "Full transform workflow: profile data, apply transforms (zero-config or config-driven), return manifest of changes",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "map-schemas",
      name: "Map Schemas",
      description:
        "Auto-map columns between source and target datasets with confidence scores",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "discover",
      name: "Discover Capabilities",
      description: "List all available transforms and domain packs",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "diff-results",
      name: "Diff Results",
      description: "Compare before and after datasets to show what changed",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "configure",
      name: "Configure",
      description:
        "Auto-generate transform config from data patterns, with profile-based recommendations",
      inputModes: ["text"],
      outputModes: ["text"],
    },
    {
      id: "handoff",
      name: "Handoff from GoldenCheck",
      description:
        "Map GoldenCheck findings to GoldenFlow transforms -- bridge for Check-to-Flow pipeline",
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

/** Run an MCP tool and parse its JSON-string result. */
function callTool(name: string, params: Record<string, unknown>): unknown {
  return JSON.parse(handleTool(name, params));
}

function dispatchSkill(skill: string, params: Record<string, unknown>): unknown {
  switch (skill) {
    case "transform-data": {
      // Workflow: profile then transform.
      const parts: Array<{ step: string; result: unknown }> = [];
      if ("path" in params) {
        parts.push({ step: "profile", result: callTool("profile", { path: params["path"] }) });
        parts.push({ step: "transform", result: callTool("transform", params) });
      }
      return parts;
    }

    case "map-schemas":
      return callTool("map", params);

    case "discover":
      return {
        transforms: callTool("list_transforms", {}),
        domains: callTool("list_domains", {}),
      };

    case "diff-results":
      return callTool("diff", params);

    case "configure": {
      const parts: Array<{ step: string; result: unknown }> = [];
      if ("path" in params) {
        parts.push({ step: "profile", result: callTool("profile", { path: params["path"] }) });
        parts.push({ step: "config", result: callTool("learn", { path: params["path"] }) });
      }
      return parts;
    }

    case "handoff":
      return callTool("select_from_findings", params);

    default:
      return { error: `Unknown skill: ${skill}` };
  }
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
  const port = options.port ?? 8150;
  const host = options.host ?? "127.0.0.1";
  const tasks = new Map<string, Task>();

  const server = createServer(async (req, res) => {
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
          const result = dispatchSkill(skill, params);
          task.status = "completed";
          task.completedAt = new Date().toISOString();
          task.result = result;
          sendJson(res, 200, { id, status: "completed", result });
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          task.status = "failed";
          task.completedAt = new Date().toISOString();
          task.error = msg;
          sendJson(res, 200, { id, status: "failed", error: msg });
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
      const msg = err instanceof Error ? err.message : String(err);
      sendJson(res, 500, { error: msg });
    }
  });

  server.listen(port, host, () => {
    // eslint-disable-next-line no-console
    console.log(`GoldenFlow A2A agent listening on http://${host}:${port}`);
  });
  return server;
}

export function runServer(port: number = 8150): ReturnType<typeof createServer> {
  return startA2aServer({ port });
}
