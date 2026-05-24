/**
 * mcp/identity-tools.ts -- Six MCP tools for the Identity Graph.
 *
 * Mirrors goldenmatch/mcp/identity_tools.py:
 *   identity_resolve, identity_list, identity_history,
 *   identity_conflicts, identity_merge, identity_split.
 *
 * Each handler opens its own SqliteIdentityStore, traps errors and returns
 * structured TextContent rather than crashing the JSON-RPC loop. Wire format
 * is snake_case to match the Python sibling.
 *
 * Node-only: depends on SqliteIdentityStore (better-sqlite3 optional peer dep).
 */

import { SqliteIdentityStore } from "../identity/sqlite-store.js";
import {
  findByRecord,
  listEntities,
  manualMerge,
  manualSplit,
  type IdentityView,
} from "../../core/identity/query.js";
import type {
  EvidenceEdge,
  IdentityEvent,
  IdentityNode,
  IdentityStatus,
  IdentityStore,
  SourceRecord,
} from "../../core/identity/types.js";

export interface Tool {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

export interface TextContent {
  readonly type: "text";
  readonly text: string;
}

const DEFAULT_PATH = ".goldenmatch/identity.db";

// ---------------------------------------------------------------------------
// Tool definitions (mirror Python identity_tools.py)
// ---------------------------------------------------------------------------

export const IDENTITY_TOOLS: readonly Tool[] = [
  {
    name: "identity_resolve",
    description:
      "Resolve a record_id to its durable identity. Returns the full identity " +
      "view (members, evidence edges, recent events) or { found: false } when " +
      "no identity exists for that record.",
    inputSchema: {
      type: "object",
      properties: {
        record_id: {
          type: "string",
          description: "record id in `{source}:{source_pk}` form",
        },
        path: { type: "string", description: "Identity DB path. Default: .goldenmatch/identity.db" },
      },
      required: ["record_id"],
    },
  },
  {
    name: "identity_list",
    description: "List identities, optionally filtered by dataset/status.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        status: { type: "string", description: "active | merged_into | split | retired" },
        limit: { type: "integer", default: 50 },
        offset: { type: "integer", default: 0 },
        path: { type: "string" },
      },
    },
  },
  {
    name: "identity_history",
    description: "Return the temporal event log for an identity.",
    inputSchema: {
      type: "object",
      properties: {
        entity_id: { type: "string" },
        limit: { type: "integer", default: 100 },
        path: { type: "string" },
      },
      required: ["entity_id"],
    },
  },
  {
    name: "identity_conflicts",
    description: "List evidence edges marked `conflicts_with`.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        path: { type: "string" },
      },
    },
  },
  {
    name: "identity_merge",
    description:
      "Manually merge two identities. All records from `absorb_entity_id` are " +
      "reassigned to `keep_entity_id`.",
    inputSchema: {
      type: "object",
      properties: {
        keep_entity_id: { type: "string" },
        absorb_entity_id: { type: "string" },
        reason: { type: "string" },
        path: { type: "string" },
      },
      required: ["keep_entity_id", "absorb_entity_id"],
    },
  },
  {
    name: "identity_split",
    description:
      "Split a subset of records off an identity into a brand-new identity. " +
      "The original keeps the remaining records.",
    inputSchema: {
      type: "object",
      properties: {
        entity_id: { type: "string" },
        record_ids: { type: "array", items: { type: "string" } },
        reason: { type: "string" },
        path: { type: "string" },
      },
      required: ["entity_id", "record_ids"],
    },
  },
];

export const IDENTITY_TOOL_NAMES: ReadonlySet<string> = new Set(
  IDENTITY_TOOLS.map((t) => t.name),
);

// ---------------------------------------------------------------------------
// snake_case serializers (match the Python wire format)
// ---------------------------------------------------------------------------

function nodeToDict(n: IdentityNode): Record<string, unknown> {
  return {
    entity_id: n.entityId,
    status: n.status,
    merged_into: n.mergedInto,
    golden_record: n.goldenRecord,
    confidence: n.confidence,
    dataset: n.dataset,
    created_at: n.createdAt.toISOString(),
    updated_at: n.updatedAt.toISOString(),
  };
}

function recordToDict(r: SourceRecord): Record<string, unknown> {
  return {
    record_id: r.recordId,
    source: r.source,
    source_pk: r.sourcePk,
    record_hash: r.recordHash,
    entity_id: r.entityId,
    payload: r.payload,
    dataset: r.dataset,
    first_seen_at: r.firstSeenAt.toISOString(),
    last_seen_at: r.lastSeenAt.toISOString(),
  };
}

function edgeToDict(e: EvidenceEdge): Record<string, unknown> {
  return {
    edge_id: e.edgeId,
    entity_id: e.entityId,
    record_a_id: e.recordAId,
    record_b_id: e.recordBId,
    kind: e.kind,
    score: e.score,
    matchkey_name: e.matchkeyName,
    field_scores: e.fieldScores,
    negative_evidence: e.negativeEvidence,
    controller_snapshot: e.controllerSnapshot,
    run_name: e.runName,
    dataset: e.dataset,
    recorded_at: e.recordedAt.toISOString(),
  };
}

function eventToDict(ev: IdentityEvent): Record<string, unknown> {
  return {
    event_id: ev.eventId,
    entity_id: ev.entityId,
    kind: ev.kind,
    payload: ev.payload,
    run_name: ev.runName,
    dataset: ev.dataset,
    recorded_at: ev.recordedAt.toISOString(),
  };
}

function viewToDict(v: IdentityView): Record<string, unknown> {
  return {
    node: nodeToDict(v.node),
    records: v.records.map(recordToDict),
    edges: v.edges.map(edgeToDict),
    events: v.events.map(eventToDict),
  };
}

// ---------------------------------------------------------------------------
// Store factory (overridable in tests)
// ---------------------------------------------------------------------------

let _storeFactory: (path: string) => Promise<IdentityStore> = (path) =>
  SqliteIdentityStore.open({ path });

/** Test seam: override how the identity store is opened (e.g. inject an
 *  InMemoryIdentityStore so tests don't need the better-sqlite3 peer dep). */
export function __setIdentityStoreFactoryForTests(
  factory: ((path: string) => Promise<IdentityStore>) | null,
): void {
  _storeFactory = factory ?? ((path) => SqliteIdentityStore.open({ path }));
}

async function openStore(path: string): Promise<IdentityStore> {
  return _storeFactory(path);
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

export async function handleIdentityTool(
  name: string,
  args: Record<string, unknown>,
): Promise<TextContent[]> {
  let result: Record<string, unknown>;
  try {
    result = await dispatch(name, args ?? {});
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    result = { error: msg };
  }
  return [{ type: "text", text: JSON.stringify(result, null, 2) }];
}

function strArg(args: Record<string, unknown>, key: string): string | undefined {
  return typeof args[key] === "string" && args[key] ? (args[key] as string) : undefined;
}

function intArg(args: Record<string, unknown>, key: string, dflt: number): number {
  const raw = args[key];
  const n = typeof raw === "number" ? raw : parseInt(String(raw), 10);
  return Number.isFinite(n) ? n : dflt;
}

async function dispatch(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const path = strArg(args, "path") ?? DEFAULT_PATH;
  const store = await openStore(path);
  try {
    if (name === "identity_resolve") {
      const recordId = strArg(args, "record_id");
      if (!recordId) return { error: "Missing required parameter: record_id" };
      const view = await findByRecord(store, recordId);
      return view ? viewToDict(view) : { found: false };
    }

    if (name === "identity_list") {
      const opts: { dataset?: string; status?: IdentityStatus; limit?: number; offset?: number } = {
        limit: intArg(args, "limit", 50),
        offset: intArg(args, "offset", 0),
      };
      const dataset = strArg(args, "dataset");
      if (dataset) opts.dataset = dataset;
      const status = strArg(args, "status");
      if (status) opts.status = status as IdentityStatus;
      const nodes = await listEntities(store, opts);
      return { items: nodes.map(nodeToDict) };
    }

    if (name === "identity_history") {
      const entityId = strArg(args, "entity_id");
      if (!entityId) return { error: "Missing required parameter: entity_id" };
      const events = await store.history(entityId, intArg(args, "limit", 100));
      return { items: events.map(eventToDict) };
    }

    if (name === "identity_conflicts") {
      const dataset = strArg(args, "dataset");
      const edges = await store.findConflicts(dataset);
      return { items: edges.map(edgeToDict) };
    }

    if (name === "identity_merge") {
      const keep = strArg(args, "keep_entity_id");
      const absorb = strArg(args, "absorb_entity_id");
      if (!keep || !absorb) {
        return { error: "Missing required parameters: keep_entity_id, absorb_entity_id" };
      }
      const reason = strArg(args, "reason");
      const res = await manualMerge(store, keep, absorb, {
        ...(reason !== undefined ? { reason } : {}),
        runName: "mcp",
      });
      return { keep: res.keep, absorbed: res.absorbed, at: res.at };
    }

    if (name === "identity_split") {
      const entityId = strArg(args, "entity_id");
      const recordIdsRaw = args["record_ids"];
      if (!entityId || !Array.isArray(recordIdsRaw)) {
        return { error: "Missing required parameters: entity_id, record_ids" };
      }
      const recordIds = recordIdsRaw.map((r) => String(r));
      const reason = strArg(args, "reason");
      const res = await manualSplit(store, entityId, recordIds, {
        ...(reason !== undefined ? { reason } : {}),
        runName: "mcp",
      });
      return { new_entity_id: res.newEntityId, moved: res.moved, at: res.at };
    }

    return { error: `Unknown identity tool: ${name}` };
  } finally {
    await store.close();
  }
}
