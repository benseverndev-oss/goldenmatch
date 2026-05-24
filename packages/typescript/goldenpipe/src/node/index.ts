/**
 * GoldenPipe node entry — Node-only helpers (file I/O, YAML config).
 *
 * Re-exports the full core surface plus the file-based `run`, CSV reading, and
 * YAML config loading.
 */

export * from "../core/index.js";
export { run } from "./run.js";
export type { RunOptions } from "./run.js";
export { readCsv, parseCsv } from "./csv.js";
export { loadConfig, normalizeConfig } from "./loadConfig.js";
export { TOOLS as MCP_TOOLS, handleTool as mcpHandleTool, startMcpServer } from "./mcp/server.js";
export {
  AGENT_CARD,
  startA2aServer,
  runServer as runA2aServer,
} from "./a2a/server.js";
export type { AgentSkill, StartA2aOptions } from "./a2a/server.js";
export { createApp as createApiApp, runServer as runApiServer } from "./api/server.js";
