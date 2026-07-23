/**
 * mcp/rollback-tools.ts -- the `list_runs` + `rollback` MCP tools.
 *
 * Ports the Python server's rollback subsystem tools
 * (goldenmatch/mcp/server.py `_tool_list_runs` / `_tool_rollback`) onto the TS
 * MCP surface. These read/operate on the on-disk `.goldenmatch_runs.json` run
 * log (run-log.ts), NOT the ephemeral in-memory `RUN_STORE` -- rollback needs a
 * durable record of which output files a run wrote.
 *
 * Split out from run-tools.ts on purpose: run-tools.ts covers the in-memory
 * run-cache read tools and explicitly deferred these two "with the rollback
 * subsystem" (see its header). This module is that subsystem's MCP surface.
 *
 * Node-only: the run log lives on the filesystem.
 */
import { sanitizePath } from "./paths.js";
import { listRuns, rollbackRun } from "./run-log.js";

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Tool definitions (names + schemas mirror the Python server exactly)
// ---------------------------------------------------------------------------

export const ROLLBACK_TOOLS: readonly Tool[] = [
  {
    name: "list_runs",
    description: "List previous dedupe/match runs (for rollback) from the run log.",
    inputSchema: {
      type: "object",
      properties: {
        output_dir: { type: "string", description: "Directory holding the run log (default '.')" },
      },
    },
  },
  {
    name: "rollback",
    description:
      "Undo a previous run by DELETING its output files (looked up by run_id in " +
      "the run log). Destructive: removes the files that run wrote. Use list_runs " +
      "first to find the run_id.",
    inputSchema: {
      type: "object",
      properties: {
        run_id: { type: "string", description: "The run id to roll back" },
        output_dir: { type: "string", description: "Directory holding the run log (default '.')" },
      },
      required: ["run_id"],
    },
  },
];

export const ROLLBACK_TOOL_NAMES: ReadonlySet<string> = new Set(ROLLBACK_TOOLS.map((t) => t.name));

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/**
 * Validate `output_dir` through the cwd jail, mirroring Python's
 * `_safe_path_or_error` guard on the dispatch. Returns the resolved dir, or an
 * error object the caller returns verbatim.
 */
function safeDirOrError(raw: unknown): string | { error: string } {
  const dir = typeof raw === "string" && raw.length > 0 ? raw : ".";
  try {
    return sanitizePath(dir);
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

function toolListRuns(args: Record<string, unknown>): unknown {
  const dir = safeDirOrError(args["output_dir"]);
  if (typeof dir !== "string") return dir;
  return { runs: listRuns(dir) };
}

function toolRollback(args: Record<string, unknown>): unknown {
  const runId = args["run_id"];
  if (typeof runId !== "string" || runId.length === 0) {
    return { error: "run_id is required" };
  }
  const dir = safeDirOrError(args["output_dir"]);
  if (typeof dir !== "string") return dir;
  return rollbackRun(runId, dir);
}

/** Dispatch a rollback-subsystem tool. Returns a plain object (the wrap applies). */
export function handleRollbackTool(name: string, args: Record<string, unknown>): unknown {
  switch (name) {
    case "list_runs":
      return toolListRuns(args);
    case "rollback":
      return toolRollback(args);
    default:
      return { error: `unknown rollback tool: ${name}` };
  }
}
