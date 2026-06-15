/**
 * session-file.ts — Node file-loading entry points for the agent surface.
 *
 * Mirrors the `dedupe` (core, rows) vs `dedupeFile` (node, loads) split: the
 * edge-safe core `AgentSession` operates on `Row[]`; this node layer reads
 * CSV/JSON from disk via `readFile` and delegates to a fresh session.
 */
import type {
  AnalyzeResult,
  DeduplicateResult,
  MatchSourcesResult,
} from "../../core/agent/index.js";
import { AgentSession } from "../../core/agent/index.js";
import type { GoldenMatchConfig } from "../../core/index.js";
import { readFile } from "../connectors/file.js";

/** Profile a file + recommend a strategy (sync; mirrors `AgentSession.analyze`). */
export function analyzeFile(path: string): AnalyzeResult {
  return new AgentSession().analyze(readFile(path));
}

/** Run the full agent dedupe pipeline on a file. */
export function deduplicateFile(
  path: string,
  config?: GoldenMatchConfig,
): Promise<DeduplicateResult> {
  return new AgentSession().deduplicate(readFile(path), config);
}

/** Match two files via the agent session. */
export function matchSourcesFile(
  pathA: string,
  pathB: string,
  config?: GoldenMatchConfig,
): Promise<MatchSourcesResult> {
  return new AgentSession().matchSources(
    readFile(pathA),
    readFile(pathB),
    config,
  );
}
