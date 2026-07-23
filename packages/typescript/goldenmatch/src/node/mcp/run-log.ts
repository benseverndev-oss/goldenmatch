/**
 * mcp/run-log.ts -- file-based run log for the rollback subsystem.
 *
 * A faithful port of Python's `goldenmatch/core/rollback.py`. This is a
 * SEPARATE state layer from the in-memory `run-store.ts` (`RUN_STORE`): that
 * store is ephemeral (TTL-evicted, lost on restart) and backs the read-query
 * tools; this is an on-disk JSON snapshot log (`.goldenmatch_runs.json`) that
 * survives restarts and records which output FILES a run wrote so `rollback`
 * can delete them.
 *
 * Node-only: touches the filesystem. Path jailing reuses `sanitizePath` from
 * paths.ts (the exact cwd jail the rest of the MCP server uses), mirroring
 * Python's `safe_path`: a jailed / bad path is SKIPPED and reported under
 * `not_found`, never thrown out of the whole call.
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";

import { sanitizePath } from "./paths.js";

/** File name of the run log, kept in the output directory. Parity with Python. */
export const RUN_LOG_FILE = ".goldenmatch_runs.json";

/** Keep only the last N runs in the log (Python parity). */
const MAX_RUNS = 50;

/** One recorded run. `config`/`stats` are opaque JSON blobs (dict in Python). */
export interface RunSnapshot {
  readonly run_id: string;
  readonly timestamp: string;
  readonly config: unknown;
  readonly stats: unknown;
  readonly output_files: readonly string[];
  readonly original_file: string | null;
  rolled_back: boolean;
  rolled_back_at?: string;
}

/** Result of a `rollback` call (Python parity shape). */
export interface RollbackResult {
  readonly run_id: string;
  readonly deleted: string[];
  readonly not_found: string[];
  readonly status: "rolled_back";
}

/** Result of a rollback that could not proceed. */
export interface RollbackError {
  readonly error: string;
  readonly available_runs?: string[];
}

function logPathFor(outputDir: string): string {
  return resolve(outputDir, RUN_LOG_FILE);
}

/** Load the run log; empty list if the file is absent or corrupt (Python parity). */
function loadRunLog(path: string): RunSnapshot[] {
  if (!existsSync(path)) return [];
  try {
    const parsed = JSON.parse(readFileSync(path, "utf-8")) as unknown;
    return Array.isArray(parsed) ? (parsed as RunSnapshot[]) : [];
  } catch {
    return [];
  }
}

/**
 * Append a snapshot of a run so it can later be rolled back. Mirrors Python's
 * `save_run_snapshot` -- appends, keeps only the last 50, rewrites the log.
 *
 * NOTE (parity): like Python, this is NOT auto-called by the dedupe pipeline;
 * it is a callable the caller (or a future pipeline hook) invokes explicitly.
 */
export function saveRunSnapshot(
  runId: string,
  outputDir: string,
  config: unknown,
  stats: unknown,
  outputFiles: readonly string[],
  originalFile: string | null = null,
): void {
  const dir = sanitizePath(outputDir);
  const path = logPathFor(dir);
  const runs = loadRunLog(path);

  runs.push({
    run_id: runId,
    timestamp: new Date().toISOString(),
    config,
    stats,
    output_files: [...outputFiles],
    original_file: originalFile,
    rolled_back: false,
  });

  const trimmed = runs.length > MAX_RUNS ? runs.slice(runs.length - MAX_RUNS) : runs;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(trimmed, null, 2), "utf-8");
}

/** List all saved runs. Empty list if the log is absent/corrupt (Python parity). */
export function listRuns(outputDir = "."): RunSnapshot[] {
  return loadRunLog(logPathFor(sanitizePath(outputDir)));
}

/**
 * Roll back a run by DELETING its output files, then marking it rolled back.
 * Faithful port of Python's `rollback_run`:
 *   - not found            -> { error, available_runs }
 *   - already rolled back   -> { error }
 *   - each output file is path-jailed via `sanitizePath` (relative paths are
 *     resolved against `outputDir`); a jailed / missing file is reported under
 *     `not_found` and never aborts the whole call.
 */
export function rollbackRun(runId: string, outputDir = "."): RollbackResult | RollbackError {
  const dir = sanitizePath(outputDir);
  const path = logPathFor(dir);
  const runs = loadRunLog(path);

  const target = runs.find((r) => r.run_id === runId);
  if (target === undefined) {
    return { error: `Run ${runId} not found`, available_runs: runs.map((r) => r.run_id) };
  }
  if (target.rolled_back) {
    return { error: `Run ${runId} was already rolled back` };
  }

  const deleted: string[] = [];
  const notFound: string[] = [];
  for (const filepath of target.output_files ?? []) {
    const candidate = isAbsolute(filepath) ? filepath : resolve(dir, filepath);
    let safe: string;
    try {
      safe = sanitizePath(candidate);
    } catch {
      // Jailed (outside allowed root) -- skip and report, don't throw the call.
      notFound.push(candidate);
      continue;
    }
    if (existsSync(safe)) {
      rmSync(safe);
      deleted.push(safe);
    } else {
      notFound.push(safe);
    }
  }

  target.rolled_back = true;
  target.rolled_back_at = new Date().toISOString();
  writeFileSync(path, JSON.stringify(runs, null, 2), "utf-8");

  return { run_id: runId, deleted, not_found: notFound, status: "rolled_back" };
}
