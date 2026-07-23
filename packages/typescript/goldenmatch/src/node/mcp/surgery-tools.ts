/**
 * mcp/surgery-tools.ts -- the `unmerge_record` + `shatter_cluster` MCP tools.
 *
 * Ports the Python server's cluster-surgery tools
 * (goldenmatch/mcp/server.py `_tool_unmerge_record` / `_tool_shatter_cluster`,
 * which delegate to `MatchEngine.unmerge_record` / `unmerge_cluster`) onto the
 * TS MCP surface. Both operate on the CURRENT run held in the in-memory
 * `RUN_STORE` (the last `dedupe` in this stdio session):
 *   - unmerge_record: pull a record out of its cluster and RE-CLUSTER the
 *     remainder using the stored `pairScores` (no re-scoring).
 *   - shatter_cluster: break a whole cluster into singletons (pair scores
 *     discarded -- correct, the cluster is being rejected wholesale).
 *
 * These mutate the current run IN PLACE via `RUN_STORE.update(...)`, preserving
 * its run id (surgery edits an existing run; it does not start a new one). The
 * surgery kernels (`unmergeRecord`/`unmergeCluster` in core/cluster.ts) already
 * exist and are fully edge-safe; this module is just the node-side stateful
 * wiring over them.
 *
 * Node-only: reads/writes server-held run state (`src/node/**`).
 *
 * Memory-write parity: the surgery kernels accept an OPTIONAL `memoryStore` that
 * auto-writes `reject` corrections. Python's MCP path does NOT pass one, so the
 * MCP wiring here deliberately omits it too (no auto-emitted corrections).
 */
import { unmergeRecord, unmergeCluster } from "../../core/cluster.js";
import type { ClusterInfo, DedupeResult } from "../../core/types.js";
import { RUN_STORE } from "./run-store.js";

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

const NO_RUN = {
  error: "No run loaded. Run dedupe (or find_duplicates) in this session first.",
} as const;

// ---------------------------------------------------------------------------
// Tool definitions (names + schemas mirror the Python server exactly)
// ---------------------------------------------------------------------------

export const SURGERY_TOOLS: readonly Tool[] = [
  {
    name: "unmerge_record",
    description:
      "Remove a record from its cluster in the current run and re-cluster the " +
      "remaining members using the stored pair scores (no re-scoring). The removed " +
      "record becomes a singleton. Use when one record does not belong in a cluster.",
    inputSchema: {
      type: "object",
      properties: {
        record_id: { type: "integer", description: "Row ID of the record to unmerge" },
      },
      required: ["record_id"],
    },
  },
  {
    name: "shatter_cluster",
    description:
      "Break an entire cluster in the current run into individual records. " +
      "All members become singletons. Use when a cluster is completely wrong.",
    inputSchema: {
      type: "object",
      properties: {
        cluster_id: { type: "integer", description: "Cluster ID to shatter" },
      },
      required: ["cluster_id"],
    },
  },
];

export const SURGERY_TOOL_NAMES: ReadonlySet<string> = new Set(
  SURGERY_TOOLS.map((t) => t.name),
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Rebuild a `DedupeResult` after cluster surgery: swap in the new clusters map
 * and recompute the derived stats the same way the dedupe pipeline does
 * (totalClusters = clusters.size; matchedRecords = members of size>=2 clusters).
 * Everything else (golden records, dupes/unique rows, scored pairs, config) is
 * carried over unchanged -- mirroring Python's engine, which only replaces
 * clusters + stats on an unmerge/shatter.
 */
function rebuildResult(
  prev: DedupeResult,
  clusters: Map<number, ClusterInfo>,
): DedupeResult {
  const totalRecords = prev.stats.totalRecords;
  let matchedRecords = 0;
  for (const info of clusters.values()) {
    if (info.size >= 2) matchedRecords += info.size;
  }
  const uniqueRecords = totalRecords - matchedRecords;
  const matchRate = totalRecords > 0 ? matchedRecords / totalRecords : 0;
  return {
    ...prev,
    clusters,
    stats: {
      totalRecords,
      totalClusters: clusters.size,
      matchRate,
      matchedRecords,
      uniqueRecords,
    },
  };
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async function toolUnmergeRecord(args: Record<string, unknown>): Promise<unknown> {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const recordId = Number(args["record_id"]);
  if (!Number.isFinite(recordId)) return { error: "record_id is required" };

  // Feed the LIVE clusters (pairScores intact) into the kernel; a shallow Map
  // copy makes it mutable without touching the frozen ReadonlyMap. No
  // memoryStore -> no auto-emitted corrections (Python MCP parity).
  const clusters = new Map(run.result.clusters);
  const updated = await unmergeRecord(recordId, clusters);
  const newResult = rebuildResult(run.result, updated);
  RUN_STORE.update(run.runId, newResult);

  // Report the record's new (singleton) cluster, mirroring Python.
  for (const [cid, info] of updated) {
    if (info.members.includes(recordId)) {
      return {
        status: "unmerged",
        record_id: recordId,
        new_cluster_id: cid,
        new_cluster_size: info.size,
        total_clusters: newResult.stats.totalClusters,
      };
    }
  }
  return { status: "unmerged", record_id: recordId };
}

async function toolShatterCluster(args: Record<string, unknown>): Promise<unknown> {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const clusterId = Number(args["cluster_id"]);
  const info = run.result.clusters.get(clusterId);
  if (info === undefined) return { error: `Cluster ${clusterId} not found` };
  const recordsFreed = info.size;

  const clusters = new Map(run.result.clusters);
  const updated = await unmergeCluster(clusterId, clusters);
  const newResult = rebuildResult(run.result, updated);
  RUN_STORE.update(run.runId, newResult);

  return {
    status: "shattered",
    cluster_id: clusterId,
    records_freed: recordsFreed,
    total_clusters: newResult.stats.totalClusters,
  };
}

/** Dispatch a cluster-surgery tool. Returns a plain object (the wrap applies). */
export async function handleSurgeryTool(
  name: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  switch (name) {
    case "unmerge_record":
      return toolUnmergeRecord(args);
    case "shatter_cluster":
      return toolShatterCluster(args);
    default:
      return { error: `unknown surgery tool: ${name}` };
  }
}
