/**
 * mcp/run-tools.ts -- stateful "run" MCP tools backed by the run store.
 *
 * Ports the Python server's run-query tools (goldenmatch/mcp/server.py) onto the
 * TS MCP surface, now that `RUN_STORE` holds the last `dedupe()` result:
 *   get_stats / list_clusters / get_cluster / get_golden_record / export_results
 * plus the stateless file-stager `upload_dataset` (agent_tools.py). All read the
 * CURRENT run (the last dedupe in this stdio session) -- none take a run id, matching
 * Python's implicit "current run" contract. `list_runs` is intentionally NOT ported
 * here: its Python impl reads the on-disk rollback snapshot log, so it belongs with
 * the (unported) rollback subsystem, not the in-memory run cache.
 *
 * Node-only: `upload_dataset`/`export_results` touch the filesystem.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import type { Row } from "../../core/types.js";
import { writeCsv, writeJson } from "../connectors/file.js";
import { sanitizePath } from "./paths.js";
import { RUN_STORE, stripInternal } from "./run-store.js";

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

const NO_RUN = {
  error: "No run loaded. Run dedupe (or find_duplicates) in this session first.",
} as const;

function envInt(name: string, def: number): number {
  const raw = process.env[name];
  if (raw === undefined) return def;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? n : def;
}

function intArg(v: unknown, def: number): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.floor(v) : def;
}

function round2(x: number): number {
  // Mirrors Python round(x, 2); half-way ties at 1e-2 are within the suite's
  // established cross-language tolerance.
  return Math.round(x * 100) / 100;
}

// ---------------------------------------------------------------------------
// Tool definitions (names + schemas mirror the Python server exactly)
// ---------------------------------------------------------------------------

export const RUN_TOOLS: readonly Tool[] = [
  {
    name: "get_stats",
    description:
      "Summary statistics for the current run (the last dedupe). Returns record/" +
      "cluster counts, singleton count, match rate, avg/max cluster size, and pair count.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_clusters",
    description:
      "List clusters from the current run, largest first. Filters to clusters of at " +
      "least `min_size` and returns the first `limit`.",
    inputSchema: {
      type: "object",
      properties: {
        min_size: { type: "integer", description: "Minimum cluster size (default 2)" },
        limit: { type: "integer", description: "Max clusters to return (default 20)" },
      },
    },
  },
  {
    name: "get_cluster",
    description: "Return one cluster from the current run: its size and member records.",
    inputSchema: {
      type: "object",
      properties: { cluster_id: { type: "integer", description: "Cluster id" } },
      required: ["cluster_id"],
    },
  },
  {
    name: "get_golden_record",
    description: "Return the golden (survivorship) record for a cluster in the current run.",
    inputSchema: {
      type: "object",
      properties: { cluster_id: { type: "integer", description: "Cluster id" } },
      required: ["cluster_id"],
    },
  },
  {
    name: "export_results",
    description:
      "Write the current run's golden records to a file (csv or json) and return the " +
      "path and record count.",
    inputSchema: {
      type: "object",
      properties: {
        output_path: { type: "string", description: "Destination file path" },
        format: { type: "string", enum: ["csv", "json"], description: "Output format (default csv)" },
      },
      required: ["output_path"],
    },
  },
  {
    name: "upload_dataset",
    description:
      "Stage a dataset file server-side (base64 or text) and return its path, so a " +
      "later dedupe/match tool can reference it. Does not start a run.",
    inputSchema: {
      type: "object",
      properties: {
        file_content: { type: "string", description: "File contents (base64 by default)" },
        filename: { type: "string", description: "Destination file name" },
        encoding: { type: "string", enum: ["base64", "text"], description: "Content encoding (default base64)" },
      },
      required: ["file_content", "filename"],
    },
  },
];

export const RUN_TOOL_NAMES: ReadonlySet<string> = new Set(RUN_TOOLS.map((t) => t.name));

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

function toolGetStats(): unknown {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const { result } = run;
  let singleton = 0;
  let maxSize = 0;
  let sum = 0;
  let count = 0;
  for (const info of result.clusters.values()) {
    count++;
    sum += info.size;
    if (info.size === 1) singleton++;
    if (info.size > maxSize) maxSize = info.size;
  }
  return {
    total_records: result.stats.totalRecords,
    total_clusters: result.stats.totalClusters,
    singleton_count: singleton,
    match_rate: round2(result.stats.matchRate),
    avg_cluster_size: round2(count > 0 ? sum / count : 0),
    max_cluster_size: maxSize,
    total_pairs: result.scoredPairs.length,
  };
}

function toolListClusters(args: Record<string, unknown>): unknown {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const minSize = intArg(args["min_size"], 2);
  const limit = intArg(args["limit"], 20);
  const rows = [...run.result.clusters.entries()]
    .filter(([, info]) => info.size >= minSize)
    .map(([cid, info]) => ({ cluster_id: cid, size: info.size, oversized: info.oversized }));
  rows.sort((a, b) => b.size - a.size); // stable: ties keep cluster-id order
  return { clusters: rows.slice(0, limit), total: rows.length };
}

function toolGetCluster(args: Record<string, unknown>): unknown {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const cid = Number(args["cluster_id"]);
  const info = run.result.clusters.get(cid);
  if (info === undefined) return { error: `Cluster ${cid} not found` };
  const members = info.members
    .map((mid) => run.rowsById.get(mid))
    .filter((r): r is Row => r !== undefined)
    .map(stripInternal);
  return { cluster_id: cid, size: info.size, members };
}

function toolGetGoldenRecord(args: Record<string, unknown>): unknown {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const cid = Number(args["cluster_id"]);
  const golden = run.result.goldenRecords.find((r) => Number(r["__cluster_id__"]) === cid);
  if (golden === undefined) return { error: `No golden record for cluster ${cid}` };
  return { cluster_id: cid, golden_record: stripInternal(golden) };
}

function toolExportResults(args: Record<string, unknown>): unknown {
  const run = RUN_STORE.getCurrent();
  if (run === null) return NO_RUN;
  const fmt = args["format"] === "json" ? "json" : "csv";
  const outPath = sanitizePath(String(args["output_path"]));
  const cleaned = run.result.goldenRecords.map(stripInternal);
  if (fmt === "json") writeJson(outPath, cleaned);
  else writeCsv(outPath, cleaned as readonly Row[]);
  return { exported: outPath, format: fmt, records: cleaned.length };
}

function safeFilename(name: string): string {
  // basename only, then neutralize anything that isn't a safe file char.
  const base = String(name).split(/[\\/]/).pop() ?? "";
  const cleaned = base.replace(/[^A-Za-z0-9._-]/g, "_").replace(/^\.+/, "");
  return cleaned.length > 0 ? cleaned : "upload.dat";
}

function toolUploadDataset(args: Record<string, unknown>): unknown {
  const encoding = args["encoding"] === "text" ? "text" : "base64";
  const content = String(args["file_content"] ?? "");
  const filename = safeFilename(String(args["filename"] ?? ""));
  const bytes: Buffer =
    encoding === "text"
      ? Buffer.from(content, "utf-8")
      : Buffer.from(content, "base64");

  const maxBytes = envInt("GOLDENMATCH_MCP_MAX_UPLOAD_BYTES", 64 * 1024 * 1024);
  if (bytes.byteLength > maxBytes) {
    return { error: `Upload exceeds ${maxBytes} bytes (${bytes.byteLength})` };
  }

  const dir = sanitizePath(resolve(process.cwd(), ".goldenmatch", "uploads"));
  const outPath = sanitizePath(resolve(dir, filename));
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, bytes);
  return { path: outPath, bytes: bytes.byteLength, filename };
}

/** Dispatch a run tool. Returns a plain object (the tools/call wrap applies). */
export function handleRunTool(name: string, args: Record<string, unknown>): unknown {
  switch (name) {
    case "get_stats":
      return toolGetStats();
    case "list_clusters":
      return toolListClusters(args);
    case "get_cluster":
      return toolGetCluster(args);
    case "get_golden_record":
      return toolGetGoldenRecord(args);
    case "export_results":
      return toolExportResults(args);
    case "upload_dataset":
      return toolUploadDataset(args);
    default:
      return { error: `unknown run tool: ${name}` };
  }
}
