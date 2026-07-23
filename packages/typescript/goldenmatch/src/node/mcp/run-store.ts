/**
 * mcp/run-store.ts -- server-held run state for the stateful MCP tools.
 *
 * The TS MCP server's `dedupe()` returns everything inline, so the query tools
 * (get_stats / list_clusters / get_cluster / get_golden_record / export_results)
 * had no server-held run to read. This is the state layer that closes that gap.
 * It lives in `src/node/**` (the MCP server is already node-only, NOT edge-safe),
 * so the edge-safe `src/core/**` stays pure and inline -- the state is a
 * server-side wrapper, exactly as documented in
 * `docs/superpowers/specs/2026-07-23-ts-mcp-stateful-run-store-design.md`.
 *
 * Mirrors Python's `mcp/_session_store.py` bounds: a bounded, TTL-evicted store
 * keyed by an internal run id, with a "current run" pointer (the stdio server is
 * single-session, so no per-session ContextVar is needed). Env parity:
 *   GOLDENMATCH_MCP_SESSION_MAX  (default 64)   -- max retained runs
 *   GOLDENMATCH_MCP_SESSION_TTL  (default 3600) -- run TTL, seconds
 */
import type { DedupeResult, Row } from "../../core/types.js";

function envInt(name: string, def: number): number {
  const raw = process.env[name];
  if (raw === undefined) return def;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? n : def;
}

export interface RunState {
  readonly runId: string;
  readonly result: DedupeResult;
  /** `__row_id__` -> the source row, for reconstructing cluster members. */
  readonly rowsById: ReadonlyMap<number, Row>;
  readonly sourcePath: string | null;
  /** Milliseconds since epoch, from the store clock. */
  readonly createdAt: number;
}

/**
 * Bounded run store. Insertion-ordered (a `Map`), lazily TTL-evicted on every
 * `put`/`get`, then FIFO-evicted down to `max`. The clock is injectable so
 * tests can drive TTL expiry deterministically.
 */
export class RunStore {
  private readonly entries = new Map<string, { run: RunState; touched: number }>();
  private currentId: string | null = null;
  private seq = 0;
  private readonly max: number;
  private readonly ttlMs: number;
  private readonly clock: () => number;

  constructor(opts?: { clock?: () => number; max?: number; ttlSeconds?: number }) {
    this.max = Math.max(1, opts?.max ?? envInt("GOLDENMATCH_MCP_SESSION_MAX", 64));
    this.ttlMs = (opts?.ttlSeconds ?? envInt("GOLDENMATCH_MCP_SESSION_TTL", 3600)) * 1000;
    this.clock = opts?.clock ?? ((): number => Date.now());
  }

  /** Store a run and make it current. Returns the assigned run id. */
  put(run: Omit<RunState, "runId" | "createdAt">): string {
    const now = this.clock();
    const runId = `run-${++this.seq}`;
    const full: RunState = { ...run, runId, createdAt: now };
    this.entries.set(runId, { run: full, touched: now });
    this.currentId = runId;
    this.evict(now);
    return runId;
  }

  /** The most-recently-stored run, or null if none / expired. */
  getCurrent(): RunState | null {
    return this.currentId === null ? null : this.get(this.currentId);
  }

  get(runId: string): RunState | null {
    const now = this.clock();
    const entry = this.entries.get(runId);
    if (entry === undefined) return null;
    if (now - entry.touched > this.ttlMs) {
      this.entries.delete(runId);
      if (this.currentId === runId) this.currentId = null;
      return null;
    }
    return entry.run;
  }

  /**
   * Replace a stored run's `result` IN PLACE, preserving its `runId`,
   * `createdAt`, `rowsById`, `sourcePath`, insertion order, and the "current"
   * pointer. This is the in-place surgery path (unmerge/shatter mutate the
   * current run's clusters) -- distinct from `put`, which mints a NEW run id.
   * Returns true if the run existed (and is unexpired), false otherwise.
   */
  update(runId: string, newResult: DedupeResult): boolean {
    const now = this.clock();
    const entry = this.entries.get(runId);
    if (entry === undefined) return false;
    if (now - entry.touched > this.ttlMs) {
      this.entries.delete(runId);
      if (this.currentId === runId) this.currentId = null;
      return false;
    }
    // Map.set on an existing key keeps its insertion position, so FIFO
    // eviction order is preserved. Touch refreshes TTL (surgery = activity).
    const updated: RunState = { ...entry.run, result: newResult };
    this.entries.set(runId, { run: updated, touched: now });
    return true;
  }

  private evict(now: number): void {
    for (const [k, v] of [...this.entries]) {
      if (now - v.touched > this.ttlMs) {
        this.entries.delete(k);
        if (this.currentId === k) this.currentId = null;
      }
    }
    while (this.entries.size > this.max) {
      const oldest = this.entries.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
      if (this.currentId === oldest) this.currentId = null;
    }
  }

  /** Test isolation -- drop all runs. */
  clear(): void {
    this.entries.clear();
    this.currentId = null;
    this.seq = 0;
  }
}

/** Process-singleton run store the MCP server writes and the run tools read. */
export const RUN_STORE = new RunStore();

/** Drop `__`-prefixed internal columns from a row for external output. */
export function stripInternal(row: Row): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(row)) {
    if (!k.startsWith("__")) out[k] = v;
  }
  return out;
}
