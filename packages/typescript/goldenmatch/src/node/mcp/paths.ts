/**
 * mcp/paths.ts -- shared filesystem-path jail for the MCP server.
 *
 * Extracted from server.ts so the run-tools module reuses the exact same
 * guard (no second, drifting copy). Node-only.
 */
import { resolve, isAbsolute, sep } from "node:path";

/**
 * Resolve `raw` to an absolute path and refuse anything outside the current
 * working directory. Mirrors Python's `_safe_path_or_error` intent.
 */
export function sanitizePath(raw: string): string {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new Error("path must be a non-empty string");
  }
  const resolved = isAbsolute(raw) ? resolve(raw) : resolve(process.cwd(), raw);
  const cwd = resolve(process.cwd());
  // Guard against prefix-bypass: cwd="/app/foo" must NOT accept "/app/foobar".
  if (resolved !== cwd && !resolved.startsWith(cwd + sep)) {
    throw new Error(`Path '${raw}' is outside the working directory`);
  }
  return resolved;
}
