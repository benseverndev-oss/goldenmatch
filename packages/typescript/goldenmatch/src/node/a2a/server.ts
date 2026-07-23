/**
 * a2a/server.ts -- GoldenMatch A2A (Agent-to-Agent) protocol server.
 *
 * Node-only: uses node:http, node:crypto. NOT edge-safe.
 *
 * Endpoints:
 *   GET  /.well-known/agent.json   - agent card (union of all registries)
 *   GET  /health                   - liveness probe (public)
 *   POST /tasks                    - create a task (skill + input)
 *   POST /tasks/send               - alias for POST /tasks (Python parity)
 *   GET  /tasks/{id}               - fetch task status/result
 *   POST /tasks/{id}/cancel        - cancel a task
 *
 * Bearer auth mirrors goldenmatch/a2a/server.py: when
 * GOLDENMATCH_AGENT_TOKEN is set, every route except /health and
 * /.well-known/agent.json requires `Authorization: Bearer <token>`.
 * Binding to a non-loopback host without a token throws at startup
 * (fail-closed).
 *
 * Ports ideas from goldenmatch/a2a/server.py. This is a simpler
 * synchronous variant (no SSE streaming, no persistent store).
 */

import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { randomUUID } from "node:crypto";
import { env } from "node:process";
import { dedupe, match, scoreStrings } from "../../core/api.js";
import { profileRows } from "../../core/profiler.js";
import { explainPair } from "../../core/explain.js";
import type { Row } from "../../core/types.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  VALID_SCORERS,
  VALID_TRANSFORMS,
  VALID_STRATEGIES,
} from "../../core/types.js";
import { AGENT_SKILLS } from "../../core/agent/index.js";
import { AGENT_TOOL_NAMES, handleAgentTool } from "../mcp/agent-tools.js";
import {
  MEMORY_TOOLS,
  MEMORY_TOOL_NAMES,
  handleMemoryTool,
} from "../mcp/memory-tools.js";
import {
  IDENTITY_TOOLS,
  IDENTITY_TOOL_NAMES,
  handleIdentityTool,
} from "../mcp/identity-tools.js";

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

export interface AgentCard {
  readonly name: string;
  readonly description: string;
  readonly version: string;
  readonly provider: {
    readonly organization: string;
    readonly url: string;
  };
  readonly capabilities: Readonly<Record<string, boolean>>;
  readonly skills: readonly AgentSkill[];
  readonly authentication: {
    readonly schemes: readonly string[];
  };
}

/**
 * The 10 native A2A skills. These dispatch through the local `dispatchSkill`
 * switch below (not the MCP registries). `id` is the machine id; `name` is a
 * curated human label. Two ids (`deduplicate`, `explain`) are aligned to the
 * Python A2A server's canonical ids; the legacy `dedupe`/`explain_pair` ids
 * still dispatch (see dispatchSkill).
 */
const BASE_SKILLS: readonly AgentSkill[] = [
  {
    id: "deduplicate",
    name: "Deduplicate",
    description: "Deduplicate a list of records and return golden records plus clusters.",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
  {
    id: "match",
    name: "Match",
    description: "Match target records against reference records.",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
  {
    id: "score",
    name: "Score",
    description: "Score similarity between two strings.",
    inputModes: ["text"],
    outputModes: ["text"],
  },
  {
    id: "profile",
    name: "Profile",
    description: "Profile a dataset (types, null rates, cardinality).",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
  {
    id: "suggest_config",
    name: "Suggest Config",
    description: "Auto-generate a shorthand dedupe config from a dataset profile.",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
  {
    id: "explain",
    name: "Explain",
    description: "Explain why two records match using weighted field scorers.",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
  {
    id: "evaluate",
    name: "Evaluate",
    description: "Evaluate predicted pairs vs ground truth (precision/recall/F1).",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
  {
    id: "list_scorers",
    name: "List Scorers",
    description: "List all available similarity scorers.",
    inputModes: ["text"],
    outputModes: ["data/json"],
  },
  {
    id: "list_transforms",
    name: "List Transforms",
    description: "List all available field transforms.",
    inputModes: ["text"],
    outputModes: ["data/json"],
  },
  {
    id: "list_strategies",
    name: "List Strategies",
    description: "List all golden-record survivorship strategies.",
    inputModes: ["text"],
    outputModes: ["data/json"],
  },
];

/** Title-case a machine id into a human label: `agent_deduplicate` -> "Agent Deduplicate". */
function humanize(id: string): string {
  return id.split("_").map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w)).join(" ");
}

/** Map a registry entry ({id, description}) to the spec-shaped `AgentSkill`. */
function toAgentSkill(entry: { readonly id: string; readonly description: string }): AgentSkill {
  return {
    id: entry.id,
    name: humanize(entry.id),
    description: entry.description,
    inputModes: ["application/json"],
    outputModes: ["application/json"],
  };
}

// A2A naming reconciliation: 3 agent skills advertise Python's canonical id on
// the A2A card (a2a_skills parity). The underlying agent-tool id (also the MCP
// tool id) is UNCHANGED -- the A2A card is a separate surface. tool-id -> canonical.
const A2A_AGENT_ID_ALIASES: Record<string, string> = {
  auto_configure: "autoconfig",
  agent_compare_strategies: "compare_strategies",
  run_transforms: "transform",
};
// canonical -> tool-id, for dispatch resolution.
const A2A_CANONICAL_TO_TOOL: Record<string, string> = Object.fromEntries(
  Object.entries(A2A_AGENT_ID_ALIASES).map(([tool, canon]) => [canon, tool]),
);

/**
 * Build the card's skill list from the union of every registry: the 10 base
 * A2A skills + the 15 `AGENT_SKILLS` + the memory tools + the identity tools.
 * De-duped by skill `id`; first occurrence wins, so a base skill shadows a
 * same-id registry entry.
 *
 * A2A parity note: the card is A2A-spec-shaped ({id, name}). The core skills
 * share canonical ids with the Python server (deduplicate, match, explain,
 * evaluate, ...); the legacy ids `dedupe`/`explain_pair` still dispatch (see
 * dispatchSkill). Three agent skills advertise Python's canonical id
 * (autoconfig/compare_strategies/transform for the
 * auto_configure/agent_compare_strategies/run_transforms handlers) via
 * A2A_AGENT_ID_ALIASES; the legacy ids still dispatch. The remaining catalog
 * differences (agent_* skills, Python's
 * finer granularity, TS-only score/profile, Python-only identity_audit/etc.,
 * and genuinely different ops like pprl vs suggest_pprl) are INTENTIONAL, not
 * drift — A2A is not gated for parity (MCP tools + CLI are).
 */
function buildCardSkills(): readonly AgentSkill[] {
  const out: AgentSkill[] = [];
  const seen = new Set<string>();
  const push = (skill: AgentSkill): void => {
    if (seen.has(skill.id)) return;
    seen.add(skill.id);
    out.push(skill);
  };
  for (const skill of BASE_SKILLS) push(skill);
  for (const def of AGENT_SKILLS)
    push(toAgentSkill({ id: A2A_AGENT_ID_ALIASES[def.id] ?? def.id, description: def.description }));
  for (const tool of MEMORY_TOOLS) push(toAgentSkill({ id: tool.name, description: tool.description }));
  for (const tool of IDENTITY_TOOLS) push(toAgentSkill({ id: tool.name, description: tool.description }));
  return out;
}

export const AGENT_CARD: AgentCard = {
  name: "goldenmatch-js",
  description:
    "Entity resolution agent -- dedupe, match, profile, score, explain, evaluate.",
  version: "1.8.0",
  provider: {
    organization: "goldenmatch",
    url: "https://github.com/benseverndev-oss/goldenmatch",
  },
  capabilities: {
    streaming: false,
    pushNotifications: false,
    stateTransitionHistory: false,
  },
  skills: buildCardSkills(),
  authentication: {
    schemes: ["bearer"],
  },
};

// ---------------------------------------------------------------------------
// Task store
// ---------------------------------------------------------------------------

interface Task {
  readonly id: string;
  readonly skill: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  readonly createdAt: string;
  completedAt?: string;
  result?: unknown;
  error?: string;
}

// ---------------------------------------------------------------------------
// Auth (mirrors goldenmatch/a2a/server.py auth middleware)
// ---------------------------------------------------------------------------

/** Endpoints reachable without a bearer token (liveness + discovery). */
const PUBLIC_PATHS: ReadonlySet<string> = new Set([
  "/health",
  "/.well-known/agent.json",
]);

/** Hosts considered loopback for the fail-closed startup guard. */
const LOOPBACK_HOSTS: ReadonlySet<string> = new Set([
  "127.0.0.1",
  "localhost",
  "::1",
]);

/**
 * Decide whether a request is authorized.
 *
 * - Public paths (`/health`, `/.well-known/agent.json`) always pass.
 * - When `token` is unset/empty, every path passes (auth disabled).
 * - Otherwise a non-public path requires `Authorization: Bearer <token>`.
 *
 * Pure + side-effect free so it can be unit-tested without a live server.
 */
export function isAuthorized(
  pathname: string,
  authHeader: string | undefined,
  token: string | undefined,
): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  if (!token) return true;
  const header = authHeader ?? "";
  return header.startsWith("Bearer ") && header.slice("Bearer ".length) === token;
}

// ---------------------------------------------------------------------------
// Skill dispatch
// ---------------------------------------------------------------------------

async function dispatchSkill(
  skill: string,
  input: Record<string, unknown>,
): Promise<unknown> {
  switch (skill) {
    case "deduplicate":   // A2A canonical id
    case "dedupe": {
      if (!Array.isArray(input["rows"])) throw new Error("rows must be an array");
      const rows = input["rows"] as Row[];
      const opts: {
        exact?: readonly string[];
        fuzzy?: Readonly<Record<string, number>>;
        blocking?: readonly string[];
        threshold?: number;
      } = {};
      if (Array.isArray(input["exact"])) opts.exact = input["exact"].map(String);
      if (Array.isArray(input["blocking"])) opts.blocking = input["blocking"].map(String);
      if (input["fuzzy"] && typeof input["fuzzy"] === "object" && !Array.isArray(input["fuzzy"])) {
        const f: Record<string, number> = {};
        for (const [k, v] of Object.entries(input["fuzzy"] as Record<string, unknown>)) {
          const n = typeof v === "number" ? v : Number(v);
          if (Number.isFinite(n)) f[k] = n;
        }
        opts.fuzzy = f;
      }
      if (typeof input["threshold"] === "number") opts.threshold = input["threshold"];
      const result = await dedupe(rows, opts);
      return {
        stats: {
          total_records: result.stats.totalRecords,
          total_clusters: result.stats.totalClusters,
          match_rate: result.stats.matchRate,
        },
        golden_records: result.goldenRecords,
      };
    }

    case "match": {
      if (!Array.isArray(input["target"])) throw new Error("target must be an array");
      if (!Array.isArray(input["reference"])) throw new Error("reference must be an array");
      const target = (input["target"] as Row[]).map((r) => ({ ...r, __source__: "target" }));
      const reference = (input["reference"] as Row[]).map((r) => ({
        ...r,
        __source__: "reference",
      }));
      const opts: {
        exact?: readonly string[];
        fuzzy?: Readonly<Record<string, number>>;
        blocking?: readonly string[];
        threshold?: number;
      } = {};
      if (Array.isArray(input["exact"])) opts.exact = input["exact"].map(String);
      if (Array.isArray(input["blocking"])) opts.blocking = input["blocking"].map(String);
      if (input["fuzzy"] && typeof input["fuzzy"] === "object" && !Array.isArray(input["fuzzy"])) {
        const f: Record<string, number> = {};
        for (const [k, v] of Object.entries(input["fuzzy"] as Record<string, unknown>)) {
          const n = typeof v === "number" ? v : Number(v);
          if (Number.isFinite(n)) f[k] = n;
        }
        opts.fuzzy = f;
      }
      if (typeof input["threshold"] === "number") opts.threshold = input["threshold"];
      const result = await match(target, reference, opts);
      return {
        matched: result.matched,
        unmatched: result.unmatched,
      };
    }

    case "score": {
      const a = String(input["a"] ?? "");
      const b = String(input["b"] ?? "");
      const scorer = typeof input["scorer"] === "string" ? (input["scorer"] as string) : "jaro_winkler";
      return { scorer, score: scoreStrings(a, b, scorer) };
    }

    case "profile": {
      if (!Array.isArray(input["rows"])) throw new Error("rows must be an array");
      const profile = profileRows(input["rows"] as Row[]);
      return {
        row_count: profile.rowCount,
        columns: profile.columns.map((c) => ({
          name: c.name,
          inferred_type: c.inferredType,
          null_rate: c.nullRate,
          cardinality_ratio: c.cardinalityRatio,
        })),
      };
    }

    case "suggest_config": {
      if (!Array.isArray(input["rows"])) throw new Error("rows must be an array");
      const profile = profileRows(input["rows"] as Row[]);
      const exact: string[] = [];
      const fuzzy: Record<string, number> = {};
      const blocking: string[] = [];
      for (const col of profile.columns) {
        if (col.nullRate > 0.2) continue;
        if (col.inferredType === "email" && col.cardinalityRatio >= 0.5) exact.push(col.name);
        else if (col.inferredType === "phone" && col.cardinalityRatio >= 0.5) exact.push(col.name);
        else if (col.inferredType === "zip" || col.inferredType === "geo") blocking.push(col.name);
        else if (col.inferredType === "name") fuzzy[col.name] = 0.85;
        else if (
          (col.inferredType === "string" ||
            col.inferredType === "address" ||
            col.inferredType === "description") &&
          col.avgLength > 4
        )
          fuzzy[col.name] = 0.8;
      }
      return { suggested: { exact, fuzzy, blocking, threshold: 0.85 } };
    }

    case "explain":        // A2A canonical id
    case "explain_pair": {
      const rowA = input["row_a"] as Row | undefined;
      const rowB = input["row_b"] as Row | undefined;
      if (!rowA || !rowB) throw new Error("row_a and row_b are required");
      const fieldsRaw = input["fields"];
      if (!Array.isArray(fieldsRaw)) throw new Error("fields must be an array");
      const fields = fieldsRaw.map((entry) => {
        const e = entry as Record<string, unknown>;
        return makeMatchkeyField({
          field: String(e["field"]),
          transforms: Array.isArray(e["transforms"])
            ? (e["transforms"] as unknown[]).map(String)
            : ["lowercase", "strip"],
          scorer: typeof e["scorer"] === "string" ? (e["scorer"] as string) : "jaro_winkler",
          weight: typeof e["weight"] === "number" ? (e["weight"] as number) : 1.0,
        });
      });
      const mk = makeMatchkeyConfig({
        name: "adhoc",
        type: "weighted",
        fields,
        threshold: typeof input["threshold"] === "number" ? (input["threshold"] as number) : 0.85,
      });
      const result = explainPair(rowA, rowB, mk);
      return {
        score: result.score,
        confidence: result.confidence,
        explanation: result.explanation,
      };
    }

    case "evaluate": {
      // Accept pre-computed predicted/truth pairs for simplicity.
      const predicted = Array.isArray(input["predicted"])
        ? (input["predicted"] as unknown[]).map((p) => {
            const pair = p as Record<string, unknown>;
            return [Number(pair["id_a"]), Number(pair["id_b"])] as const;
          })
        : [];
      const truth = Array.isArray(input["truth"])
        ? (input["truth"] as unknown[]).map((p) => {
            const pair = p as Record<string, unknown>;
            return [Number(pair["id_a"]), Number(pair["id_b"])] as const;
          })
        : [];
      const truthSet = new Set(truth.map(([a, b]) => `${Math.min(a, b)}:${Math.max(a, b)}`));
      const predSet = new Set(
        predicted.map(([a, b]) => `${Math.min(a, b)}:${Math.max(a, b)}`),
      );
      let tp = 0;
      let fp = 0;
      for (const p of predSet) {
        if (truthSet.has(p)) tp++;
        else fp++;
      }
      let fn = 0;
      for (const t of truthSet) {
        if (!predSet.has(t)) fn++;
      }
      const precision = tp + fp > 0 ? tp / (tp + fp) : 0;
      const recall = tp + fn > 0 ? tp / (tp + fn) : 0;
      const f1 = precision + recall > 0 ? (2 * precision * recall) / (precision + recall) : 0;
      return { tp, fp, fn, precision, recall, f1 };
    }

    case "list_scorers":
      return { scorers: [...VALID_SCORERS] };

    case "list_transforms":
      return { transforms: [...VALID_TRANSFORMS] };

    case "list_strategies":
      return { strategies: [...VALID_STRATEGIES] };

    default:
      throw new Error(`Unknown skill: ${skill}`);
  }
}

/**
 * Unwrap a memory/identity handler's `TextContent[]` into a plain object.
 *
 * Those handlers return `[{ type: "text", text: JSON.stringify(payload) }]`;
 * we parse the first element's text back into the payload so the A2A task
 * `result` field carries structured JSON (matching the agent/base skills,
 * which return plain objects). If the text is missing or unparseable we fall
 * back to wrapping the raw text.
 */
function unwrapTextContent(
  content: readonly { readonly type: string; readonly text: string }[],
): unknown {
  const first = content[0];
  if (first === undefined) return {};
  try {
    return JSON.parse(first.text);
  } catch {
    return { text: first.text };
  }
}

/**
 * Route a skill id to the correct registry:
 *   - `AGENT_TOOL_NAMES` -> `handleAgentTool` (Wave 2 node ctx)
 *   - memory id          -> `handleMemoryTool` (TextContent -> parsed)
 *   - identity id        -> `handleIdentityTool` (TextContent -> parsed)
 *   - else               -> the local base `dispatchSkill` switch
 *
 * Unknown ids fall through to `dispatchSkill`, which throws.
 */
export async function dispatchAnySkill(
  skill: string,
  input: Record<string, unknown>,
): Promise<unknown> {
  const resolved = A2A_CANONICAL_TO_TOOL[skill] ?? skill;
  if (AGENT_TOOL_NAMES.has(resolved)) {
    return handleAgentTool(resolved, input);
  }
  if (MEMORY_TOOL_NAMES.has(resolved)) {
    return unwrapTextContent(await handleMemoryTool(resolved, input));
  }
  if (IDENTITY_TOOL_NAMES.has(resolved)) {
    return unwrapTextContent(await handleIdentityTool(resolved, input));
  }
  return dispatchSkill(resolved, input);
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

/** See `src/node/api/server.ts::sanitiseForWire` for rationale. */
function sanitiseForWire(data: unknown): unknown {
  if (data instanceof Error) {
    return { error: data.message };
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(data)) {
      if (k === "stack" || k === "errno" || k === "syscall") continue;
      out[k] = v;
    }
    return out;
  }
  return data;
}

function sendJson(res: ServerResponse, status: number, data: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(sanitiseForWire(data)));
}

// ---------------------------------------------------------------------------
// Public: startA2aServer
// ---------------------------------------------------------------------------

export interface StartA2aOptions {
  readonly port?: number;
  readonly host?: string;
}

export function startA2aServer(options: StartA2aOptions = {}): ReturnType<typeof createServer> {
  const port = options.port ?? 8200;
  const host = options.host ?? "127.0.0.1";
  const tasks = new Map<string, Task>();

  // Fail-closed startup guard (mirrors Python create_app): binding to a
  // non-loopback host without GOLDENMATCH_AGENT_TOKEN is refused so an exposed
  // agent server is never unauthenticated by accident.
  const token = env["GOLDENMATCH_AGENT_TOKEN"];
  if (!token && !LOOPBACK_HOSTS.has(host)) {
    throw new Error(
      `Refusing to start an unauthenticated A2A server on host '${host}'. ` +
        "Set GOLDENMATCH_AGENT_TOKEN, or bind to 127.0.0.1 for local use.",
    );
  }

  // Shared handler for POST /tasks and its POST /tasks/send alias.
  const handleSendTask = async (
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> => {
    const body = await readJsonBody(req);
    const skill = String(body["skill"] ?? "");
    const input =
      (body["input"] as Record<string, unknown> | undefined) ??
      (body["params"] as Record<string, unknown> | undefined) ??
      {};
    if (!skill) {
      sendJson(res, 400, { error: "skill is required" });
      return;
    }
    const id = randomUUID();
    const createdAt = new Date().toISOString();
    const task: Task = {
      id,
      skill,
      status: "running",
      createdAt,
    };
    tasks.set(id, task);

    try {
      const result = await dispatchAnySkill(skill, input);
      task.status = "completed";
      task.completedAt = new Date().toISOString();
      task.result = result;
      sendJson(res, 200, {
        id,
        status: task.status,
        skill,
        created_at: createdAt,
        completed_at: task.completedAt,
        result,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      task.status = "failed";
      task.completedAt = new Date().toISOString();
      task.error = msg;
      sendJson(res, 200, {
        id,
        status: task.status,
        skill,
        created_at: createdAt,
        completed_at: task.completedAt,
        error: msg,
      });
    }
  };

  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
    const pathname = url.pathname;
    const methodName = req.method ?? "GET";

    try {
      // Bearer auth (mirrors Python _auth_middleware): public paths always
      // pass; everything else needs the matching token when one is set.
      const authHeader = req.headers["authorization"];
      const headerStr = Array.isArray(authHeader) ? authHeader[0] : authHeader;
      if (!isAuthorized(pathname, headerStr, token)) {
        sendJson(res, 401, { error: "Unauthorized" });
        return;
      }

      if (pathname === "/.well-known/agent.json" && methodName === "GET") {
        sendJson(res, 200, AGENT_CARD);
        return;
      }

      if (pathname === "/health" && methodName === "GET") {
        sendJson(res, 200, { status: "ok", agent: "goldenmatch-js" });
        return;
      }

      if (
        (pathname === "/tasks" || pathname === "/tasks/send") &&
        methodName === "POST"
      ) {
        await handleSendTask(req, res);
        return;
      }

      if (
        pathname.startsWith("/tasks/") &&
        pathname.endsWith("/cancel") &&
        methodName === "POST"
      ) {
        const id = pathname.slice("/tasks/".length, -"/cancel".length);
        const task = tasks.get(id);
        if (!task) {
          sendJson(res, 404, { error: `Task not found: ${id}` });
          return;
        }
        task.status = "cancelled";
        task.completedAt = new Date().toISOString();
        sendJson(res, 200, {
          id: task.id,
          skill: task.skill,
          status: task.status,
        });
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
    console.log(`GoldenMatch A2A agent listening on http://${host}:${port}`);
  });
  return server;
}
