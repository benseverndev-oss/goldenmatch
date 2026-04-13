/**
 * GoldenCheck Node layer — file reading, Polars integration, YAML config.
 * Requires Node.js >= 20.
 */

// Re-export core for convenience
export * from "../core/index.js";

// Node-only: file reader
export { readFile, readCsv, type ReadOptions } from "./reader.js";

// Node-only: watcher
export { watchDirectory, type WatchOptions } from "./watcher.js";

// Node-only: MCP server
export { TOOL_DEFINITIONS, handleTool } from "./mcp/server.js";

// Node-only: TUI renderer
export { renderTui } from "./tui/app.js";

// Node-only: database scanner
export { scanDatabase } from "./db-scanner.js";

// Node-only: history, scheduler, notifier (use node:fs / process signals)
export { recordScan, loadHistory, getPreviousScan } from "../core/engine/history.js";
export { shouldNotify, sendWebhook } from "../core/engine/notifier.js";
export { runSchedule } from "../core/engine/scheduler.js";

// Node-only: A2A server
export { runA2aServer } from "./a2a/server.js";
