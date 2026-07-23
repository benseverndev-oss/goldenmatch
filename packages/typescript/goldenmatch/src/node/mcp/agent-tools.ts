/**
 * mcp/agent-tools.ts -- MCP wiring for the AgentSession skill registry.
 *
 * Renders the edge-safe `AGENT_SKILLS` (15 SkillDefs) as MCP `Tool`s and
 * routes their names through the shared `dispatchSkill`, injecting a node
 * `SkillContext` whose `loadTable` is the file connector's `readFile`.
 *
 * Node-only: `readFile` uses node:fs. The skill handlers themselves stay
 * edge-safe; this module is the node surface that supplies the I/O seam.
 */

import {
  AGENT_SKILLS,
  AgentSession,
  dispatchSkill,
} from "../../core/agent/index.js";
import type { Row } from "../../core/types.js";
import type { MemoryStore } from "../../core/memory/types.js";
import { readFile } from "../connectors/file.js";
import { SqliteMemoryStore } from "../memory/sqlite-store.js";

const DEFAULT_MEMORY_PATH = ".goldenmatch/memory.db";

// ---------------------------------------------------------------------------
// Tool type (matches the shape used in mcp/server.ts + memory-tools.ts)
// ---------------------------------------------------------------------------

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Tool definitions (derived 1:1 from AGENT_SKILLS)
// ---------------------------------------------------------------------------

export const AGENT_MCP_TOOLS: readonly Tool[] = AGENT_SKILLS.map((s) => ({
  name: s.id,
  description: s.description,
  inputSchema: s.inputSchema,
}));

export const AGENT_TOOL_NAMES: ReadonlySet<string> = new Set(
  AGENT_SKILLS.map((s) => s.id),
);

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

/**
 * Route an agent-level MCP tool call through `dispatchSkill`. Each call gets a
 * fresh `AgentSession` (stateless, matching the Python `handle_agent_tool`).
 * The injected `loadTable` wraps the synchronous file `readFile` in a Promise
 * so data-bearing skills can resolve `file_path` when no inline `rows` is
 * supplied. `dispatchSkill` never throws -- failures come back as `{ error }`.
 */
export async function handleAgentTool(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const ctx = {
    session: new AgentSession(),
    loadTable: async (source: string): Promise<Row[]> => readFile(source),
    // Durable Learning Memory store factory. Only `agent_approve_reject`
    // invokes it (to persist the agent correction, matching Python's
    // `_write_agent_correction`); every other skill leaves it untouched, so no
    // SQLite handle is opened for unrelated calls. Mirrors the `add_correction`
    // MCP tool's `openStore` (default `.goldenmatch/memory.db`, `path`
    // override, `better-sqlite3` optional peer dep).
    openMemoryStore: async (): Promise<MemoryStore> => {
      const path =
        typeof args["path"] === "string" && args["path"]
          ? (args["path"] as string)
          : DEFAULT_MEMORY_PATH;
      const store = new SqliteMemoryStore({
        enabled: true,
        backend: "sqlite",
        path,
        learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
      });
      await store.init();
      return store;
    },
  };
  return dispatchSkill(name, args ?? {}, ctx);
}
