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
