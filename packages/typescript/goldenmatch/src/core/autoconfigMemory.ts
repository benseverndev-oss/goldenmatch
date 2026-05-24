/**
 * autoconfigMemory.ts — cross-run memory for the auto-config controller.
 * Edge-safe: no `node:` imports (in-memory map; the SQLite persistence in
 * Python's autoconfig_memory.py is a Node concern, deferrable to src/node/).
 *
 * Ports goldenmatch/core/autoconfig_memory.py: a per-shape signature keying a
 * store of past committed configs so the controller can short-circuit the v0
 * heuristic when a previous run with the same shape converged.
 *
 * Signature note: Python hashes `repr((mode, tuple(sorted((name, dtype)))))`.
 * The edge-safe TS port has no polars dtypes, so it derives the per-column
 * type from row values via the same lightweight classifier the rest of the TS
 * core uses. The signature is therefore stable within TS but NOT byte-identical
 * to Python's repr-based hash — the memory short-circuit is a single-language
 * optimization, not a cross-language wire contract.
 */

import type { GoldenMatchConfig, Row } from "./types.js";
import { sha256_16 } from "./memory/hash.js";

/** Lightweight per-column type inference (mirrors the spirit of polars dtype
 *  bucketing for the signature: int / float / bool / str / null). */
function inferColumnType(rows: readonly Row[], col: string): string {
  let sawFloat = false;
  let sawInt = false;
  let sawBool = false;
  let sawStr = false;
  let sawNonNull = false;
  for (const r of rows) {
    const v = (r as Record<string, unknown>)[col];
    if (v === null || v === undefined) continue;
    sawNonNull = true;
    if (typeof v === "boolean") sawBool = true;
    else if (typeof v === "number") {
      if (Number.isInteger(v)) sawInt = true;
      else sawFloat = true;
    } else sawStr = true;
  }
  if (!sawNonNull) return "null";
  if (sawStr) return "str";
  if (sawBool && !sawInt && !sawFloat) return "bool";
  if (sawFloat) return "float";
  if (sawInt) return "int";
  return "str";
}

/**
 * Compute a per-(column-name, dtype) signature for a set of rows. Two row sets
 * hash to the same signature only when they share the same user-facing column
 * names AND inferred per-column types. Mirrors Python ``profile_signature``.
 */
export async function profileSignature(
  rows: readonly Row[],
  mode: "dedupe" | "match" = "dedupe",
): Promise<string> {
  const first = (rows[0] ?? {}) as Record<string, unknown>;
  const cols = Object.keys(first).filter((c) => !c.startsWith("__"));
  const pairs = cols
    .map((c) => [c, inferColumnType(rows, c)] as const)
    .sort((a, b) => a[0].localeCompare(b[0]));
  const key = JSON.stringify([mode, pairs]);
  return sha256_16(key);
}

interface MemoryRow {
  readonly signature: string;
  readonly config: GoldenMatchConfig;
  readonly succeeded: boolean;
  readonly nIterations: number;
  readonly f1Proxy: number | null;
  readonly createdAt: number;
}

/**
 * In-memory store of past auto-config runs keyed by data-shape signature.
 * Mirrors Python ``AutoConfigMemory`` minus SQLite persistence (a Node
 * concern). ``lookupBest`` returns the most recent succeeded config for a
 * signature.
 */
export class AutoConfigMemory {
  private readonly rows: MemoryRow[] = [];
  private seq = 0;

  remember(
    signature: string,
    config: GoldenMatchConfig,
    opts: { succeeded: boolean; nIterations: number; f1Proxy?: number | null },
  ): void {
    this.rows.push({
      signature,
      config,
      succeeded: opts.succeeded,
      nIterations: opts.nIterations,
      f1Proxy: opts.f1Proxy ?? null,
      // Monotonic insert order doubles as created_at for "most recent".
      createdAt: this.seq++,
    });
  }

  lookupBest(signature: string): GoldenMatchConfig | null {
    let best: MemoryRow | null = null;
    for (const r of this.rows) {
      if (r.signature !== signature || !r.succeeded) continue;
      if (best === null || r.createdAt > best.createdAt) best = r;
    }
    return best === null ? null : best.config;
  }

  allFor(signature: string): ReadonlyArray<{
    signature: string;
    succeeded: boolean;
    nIterations: number;
    f1Proxy: number | null;
  }> {
    return this.rows
      .filter((r) => r.signature === signature)
      .sort((a, b) => b.createdAt - a.createdAt)
      .map((r) => ({
        signature: r.signature,
        succeeded: r.succeeded,
        nIterations: r.nIterations,
        f1Proxy: r.f1Proxy,
      }));
  }

  clear(): void {
    this.rows.length = 0;
    this.seq = 0;
  }
}
