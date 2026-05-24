#!/usr/bin/env node
/**
 * mcp/server.ts -- GoldenPipe MCP server (stdio transport, JSON-RPC 2.0).
 *
 * Node-only: uses node:path, node:readline. NOT edge-safe.
 *
 * Exposes 4 tools (`list_stages`, `validate_pipeline`, `run_pipeline`,
 * `explain_pipeline`) wired to the GoldenPipe TS core/node APIs -- byte-parity
 * with the Python sibling at `packages/python/goldenpipe/goldenpipe/mcp/server.py`.
 *
 * Every tool dispatch is wrapped in try/catch so a single failure never crashes
 * the JSON-RPC loop; errors come back as `{ error: "<msg>" }`.
 *
 * Mirrors the hand-rolled JSON-RPC pattern in the sibling InferMap / GoldenFlow
 * TS servers (no MCP SDK dep).
 */

import { resolve, isAbsolute } from "node:path";
import { createInterface } from "node:readline";

import {
  buildDefaultRegistry,
  Resolver,
  WiringError,
  makePipelineConfig,
} from "../../core/index.js";
import { run } from "../run.js";
import { loadConfig } from "../loadConfig.js";

// ---------------------------------------------------------------------------
// Path safety
// ---------------------------------------------------------------------------

/** Resolve a path relative to cwd and reject traversal outside it. */
function sanitizePath(raw: string): string {
  const resolved = isAbsolute(raw) ? resolve(raw) : resolve(process.cwd(), raw);
  const cwd = resolve(process.cwd());
  if (!resolved.startsWith(cwd)) {
    throw new Error(`Path '${raw}' is outside the working directory`);
  }
  return resolved;
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

export const TOOLS: readonly Tool[] = [
  {
    name: "list_stages",
    description: "List all registered pipeline stages with their produces/consumes contracts.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "validate_pipeline",
    description: "Validate pipeline wiring — every stage's consumes must be produced by an earlier stage.",
    inputSchema: {
      type: "object",
      properties: {
        pipeline: { type: "string", description: "Pipeline name." },
        stages: {
          type: "array",
          items: { type: "string" },
          description: "Ordered list of stage names (e.g. ['goldencheck.scan', 'goldenflow.transform']).",
        },
      },
      required: ["pipeline", "stages"],
    },
  },
  {
    name: "run_pipeline",
    description: "Run a pipeline on a CSV file. Zero-config or from a YAML config path.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", description: "Path to the input CSV file." },
        config_path: { type: "string", description: "Optional path to a YAML pipeline config." },
      },
      required: ["source"],
    },
  },
  {
    name: "explain_pipeline",
    description: "Explain what a pipeline config will do — resolves it into an ordered stage plan.",
    inputSchema: {
      type: "object",
      properties: {
        config_path: { type: "string", description: "Path to a YAML pipeline config." },
      },
      required: ["config_path"],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool handler
// ---------------------------------------------------------------------------

export async function handleTool(
  name: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  try {
    switch (name) {
      case "list_stages": {
        const registry = buildDefaultRegistry();
        const out: Record<string, { produces: string[]; consumes: string[] }> = {};
        for (const [stageName, info] of Object.entries(registry.listAll())) {
          out[stageName] = { produces: info.produces, consumes: info.consumes };
        }
        return out;
      }

      case "validate_pipeline": {
        const pipeline = String(args["pipeline"] ?? "");
        const stages = (args["stages"] as unknown[] | undefined ?? []).map((s) => String(s));
        try {
          const config = makePipelineConfig({ pipeline, stages });
          const plan = Resolver.resolve(config, buildDefaultRegistry());
          return { valid: true, stages: plan.stages.map((s) => s.name) };
        } catch (e) {
          if (e instanceof WiringError) {
            return { valid: false, error: e.message };
          }
          throw e;
        }
      }

      case "run_pipeline": {
        const source = sanitizePath(String(args["source"]));
        const configArg = args["config_path"];
        const options =
          configArg !== undefined && configArg !== null
            ? { config: sanitizePath(String(configArg)) }
            : undefined;
        const result = await run(source, options);
        return {
          status: result.status,
          source: result.source,
          input_rows: result.inputRows,
          errors: result.errors,
          skipped: result.skipped,
        };
      }

      case "explain_pipeline": {
        const configPath = sanitizePath(String(args["config_path"]));
        const config = await loadConfig(configPath);
        const plan = Resolver.resolve(config, buildDefaultRegistry());
        return {
          pipeline: config.pipeline,
          stages: plan.stages.map((s) => ({
            name: s.name,
            produces: s.stage.info.produces,
            consumes: s.stage.info.consumes,
          })),
        };
      }

      default:
        return { error: `Unknown tool: ${name}` };
    }
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
}

// ---------------------------------------------------------------------------
// JSON-RPC over stdio
// ---------------------------------------------------------------------------

interface JsonRpcRequest {
  jsonrpc?: string;
  id?: number | string | null;
  method?: string;
  params?: Record<string, unknown>;
}

function writeMessage(msg: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

/**
 * Start the MCP server reading JSON-RPC messages one per line from stdin and
 * writing responses to stdout. Intended for Claude Desktop / any MCP client
 * using stdio transport.
 */
export function startMcpServer(): void {
  const rl = createInterface({ input: process.stdin, terminal: false });

  rl.on("line", (line: string) => {
    if (line.trim() === "") return;
    let req: JsonRpcRequest;
    try {
      req = JSON.parse(line) as JsonRpcRequest;
    } catch (err) {
      console.warn("MCP parse error:", err instanceof Error ? err.message : String(err));
      return;
    }

    const id = req.id ?? null;

    void (async () => {
      try {
        if (req.method === "initialize") {
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: {
              protocolVersion: "2024-11-05",
              serverInfo: { name: "goldenpipe", version: "0.2.0" },
              capabilities: { tools: {}, resources: {}, prompts: {} },
            },
          });
          return;
        }

        if (req.method === "tools/list") {
          writeMessage({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
          return;
        }

        if (req.method === "tools/call") {
          const params = req.params ?? {};
          const toolName = String(params["name"] ?? "");
          const toolArgs = (params["arguments"] as Record<string, unknown> | undefined) ?? {};
          const result = await handleTool(toolName, toolArgs);
          writeMessage({
            jsonrpc: "2.0",
            id,
            result: { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] },
          });
          return;
        }

        if (req.method === "resources/list") {
          writeMessage({ jsonrpc: "2.0", id, result: { resources: [] } });
          return;
        }

        if (req.method === "prompts/list") {
          writeMessage({ jsonrpc: "2.0", id, result: { prompts: [] } });
          return;
        }

        if (
          req.method === "notifications/initialized" ||
          req.method === "notifications/cancelled"
        ) {
          return;
        }

        writeMessage({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `Method not found: ${req.method}` },
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        writeMessage({ jsonrpc: "2.0", id, error: { code: -32603, message: msg } });
      }
    })();
  });

  rl.on("close", () => {
    process.exit(0);
  });
}

// Run as a bin when invoked directly (the `goldenpipe-mcp` entry point).
// tsup compiles this to dist/node/mcp/server.{js,cjs}; the cjs build is the bin.
const isMain = (() => {
  try {
    return typeof require !== "undefined" && require.main === module;
  } catch {
    return false;
  }
})();

if (isMain) {
  startMcpServer();
}
