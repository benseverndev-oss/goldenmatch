/**
 * memory/api.ts -- Python API mirror for Learning Memory.
 *
 * Mirrors `goldenmatch._api.get_memory / add_correction / learn / memory_stats`
 * (packages/python/goldenmatch/goldenmatch/_api.py:882-973).
 *
 * Node-only: instantiates `SqliteMemoryStore`, which depends on the optional
 * `better-sqlite3` peer dep. Lives in `src/node/memory/` rather than
 * `src/core/api.ts` to respect the edge-safety boundary -- core MUST NOT
 * import node-only modules.
 */

import type {
  Correction,
  LearnedAdjustment,
  MemoryStore,
} from "../../core/memory/types.js";
import { trustForSource } from "../../core/memory/types.js";
import { MemoryLearner } from "../../core/memory/learner.js";
import { SqliteMemoryStore } from "./sqlite-store.js";

const DEFAULT_PATH = ".goldenmatch/memory.db";

export interface GetMemoryOptions {
  readonly path?: string;
}

/**
 * Open (or create) a Learning Memory store. Caller is responsible for
 * `await store.close?.()`. Mirrors Python `goldenmatch.get_memory`.
 */
export async function getMemory(opts?: GetMemoryOptions): Promise<MemoryStore> {
  const store = new SqliteMemoryStore({
    enabled: true,
    backend: "sqlite",
    path: opts?.path ?? DEFAULT_PATH,
    learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
  });
  await store.init();
  return store;
}

export interface AddCorrectionOptions {
  readonly idA: number;
  readonly idB: number;
  readonly decision: "approve" | "reject";
  readonly source?: string;
  readonly reason?: string | null;
  readonly dataset?: string | null;
  readonly matchkeyName?: string | null;
  readonly path?: string;
}

/**
 * Add a correction to the Learning Memory store. Trust derived from `source`
 * via `trustForSource`. Default `source = "api"` (agent-tier 0.5 trust).
 * Hashes are written empty; `applyCorrections` handles empty-hash entries via
 * the row-id-presence path. Mirrors Python `goldenmatch.add_correction`.
 */
export async function addCorrection(opts: AddCorrectionOptions): Promise<void> {
  const source = opts.source ?? "api";
  const trust = trustForSource(source);
  const correction: Correction = {
    id: crypto.randomUUID(),
    idA: opts.idA,
    idB: opts.idB,
    decision: opts.decision,
    source: source as Correction["source"],
    trust,
    fieldHash: "",
    recordHash: "",
    originalScore: 0.0,
    matchkeyName: opts.matchkeyName ?? null,
    reason: opts.reason ?? null,
    dataset: opts.dataset ?? null,
    createdAt: new Date(),
  };
  const path = opts.path ?? DEFAULT_PATH;
  const store = await getMemory({ path });
  try {
    await store.addCorrection(correction);
  } finally {
    await store.close?.();
  }
}

export interface LearnOptions {
  readonly matchkeyName?: string;
  readonly path?: string;
}

/**
 * Force a learning pass over stored corrections. Mirrors Python
 * `goldenmatch.learn`.
 */
export async function learn(opts?: LearnOptions): Promise<LearnedAdjustment[]> {
  const path = opts?.path ?? DEFAULT_PATH;
  const store = await getMemory({ path });
  try {
    const learner = new MemoryLearner(store);
    return await learner.learn(opts?.matchkeyName);
  } finally {
    await store.close?.();
  }
}

export interface MemoryStatsOptions {
  readonly path?: string;
}

export interface MemoryStatsResult {
  readonly count: number;
  readonly lastLearnTime: Date | null;
  readonly adjustments: readonly LearnedAdjustment[];
}

/**
 * Return summary stats about the memory store. Mirrors Python
 * `goldenmatch.memory_stats`.
 */
export async function memoryStats(
  opts?: MemoryStatsOptions,
): Promise<MemoryStatsResult> {
  const path = opts?.path ?? DEFAULT_PATH;
  const store = await getMemory({ path });
  try {
    const count = await store.countCorrections();
    const lastLearnTime = await store.lastLearnTime();
    const adjustments = await store.getAllAdjustments();
    return { count, lastLearnTime, adjustments };
  } finally {
    await store.close?.();
  }
}
