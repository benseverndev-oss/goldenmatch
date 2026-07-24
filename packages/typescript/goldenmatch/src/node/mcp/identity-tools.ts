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
  claimRecord,
  findByRecord,
  getEntity,
  listEntities,
  manualMerge,
  manualSplit,
  type IdentityView,
} from "../../core/identity/query.js";
import { mediateConflict } from "../../core/identity/mediation.js";
import {
  entityProfile,
  identitySummaryStats,
  stewardWorklist,
} from "../../core/identity/profile.js";
import { sealAuditLog, verifyAuditChain, verificationSummary } from "../../core/identity/audit.js";
import { pyIsoformat } from "../../core/identity/pyDatetime.js";
import { trustForSource } from "../../core/memory/types.js";
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
        actor: { type: "string", description: "Provenance principal id. Default: agent" },
        trust: { type: "number", description: "Provenance trust [0,1]. Default: derived from actor" },
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
        actor: { type: "string", description: "Provenance principal id. Default: agent" },
        trust: { type: "number", description: "Provenance trust [0,1]. Default: derived from actor" },
        path: { type: "string" },
      },
      required: ["entity_id", "record_ids"],
    },
  },
  {
    name: "identity_claim",
    description:
      "Claim a record into an identity, moving it out of any prior entity " +
      "('this record belongs to that identity'). Emits a `claimed` event on " +
      "both the gaining and losing entities. Idempotent: claiming a record " +
      "already in the target entity is a no-op.",
    inputSchema: {
      type: "object",
      properties: {
        entity_id: { type: "string", description: "Entity to claim the record into" },
        record_id: {
          type: "string",
          description: "record id in `{source}:{source_pk}` form",
        },
        reason: { type: "string" },
        actor: { type: "string", description: "Provenance principal id. Default: agent" },
        trust: { type: "number", description: "Provenance trust [0,1]. Default: derived from actor" },
        path: { type: "string" },
      },
      required: ["entity_id", "record_id"],
    },
  },
  {
    name: "identity_resolve_conflict",
    description:
      "Adjudicate a `conflicts_with` pair: 'same' keeps the entity intact, " +
      "'distinct' splits the second record out into a new identity, 'defer' " +
      "only logs. Records a durable mediation verdict + event and stops the " +
      "conflict re-surfacing in the open-conflicts queue.",
    inputSchema: {
      type: "object",
      properties: {
        record_a_id: { type: "string" },
        record_b_id: { type: "string" },
        resolution: { type: "string", enum: ["same", "distinct", "defer"] },
        reason: { type: "string" },
        dataset: { type: "string" },
        apply: {
          type: "boolean",
          default: true,
          description: "Act on the verdict (split on 'distinct'); false = log only.",
        },
        actor: { type: "string", description: "Provenance principal id. Default: agent" },
        trust: { type: "number", description: "Provenance trust [0,1]. Default: derived from actor" },
        path: { type: "string" },
      },
      required: ["record_a_id", "record_b_id", "resolution"],
    },
  },
  {
    name: "identity_audit",
    description:
      "Export the append-only identity audit log in commit order: every event " +
      "with actor / trust / timestamp / reason, so a reviewer can reconstruct " +
      "exactly which actor changed what, when, and why. Optionally filtered by " +
      "dataset / actor.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        actor: { type: "string" },
        limit: { type: "integer", default: 500 },
        path: { type: "string" },
      },
    },
  },
  {
    name: "identity_audit_seal",
    description:
      "Anchor the append-only audit log with a tamper-evidence seal: a chained " +
      "sha256 root over every event since the last seal. Cheap and idempotent " +
      "(a no-op when nothing new has been logged). Publish/mirror the returned " +
      "root_hash to make tampering detectable by an external party.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        actor: { type: "string", description: "Principal sealing the log. Defaults to 'agent'." },
        path: { type: "string", description: "Identity DB path" },
      },
    },
  },
  {
    name: "identity_audit_verify",
    description:
      "Verify the append-only audit log against its seal chain. Replays the " +
      "per-event content hashes and the seal roots to detect content edits, " +
      "deletion, reordering, and insertion of any sealed event. Returns " +
      "{ok, events_checked, seals_checked} plus the ids of any mismatches.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        path: { type: "string", description: "Identity DB path" },
      },
    },
  },
  {
    name: "identity_show",
    description:
      "Fetch the full detail of one identity by entity_id: its member records, " +
      "evidence edges, and recent event log. Returns {found: false} when no " +
      "such entity exists.",
    inputSchema: {
      type: "object",
      properties: {
        entity_id: { type: "string" },
        event_limit: { type: "integer", default: 100 },
        path: { type: "string", description: "Identity DB path" },
      },
      required: ["entity_id"],
    },
  },
  {
    name: "identity_profile",
    description:
      "One entity's full MDM profile: record count + per-source breakdown, " +
      "golden record, confidence, conflict count, a canonical version (count of " +
      "structural events), and first/last activity.",
    inputSchema: {
      type: "object",
      properties: {
        entity_id: { type: "string" },
        path: { type: "string", description: "Identity DB path" },
      },
      required: ["entity_id"],
    },
  },
  {
    name: "identity_stats",
    description:
      "Graph-level identity health: entities by status, total records, " +
      "records-per-entity distribution, conflict total, source mix, and the " +
      "largest entities. Optionally scoped to a dataset.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        path: { type: "string", description: "Identity DB path" },
      },
    },
  },
  {
    name: "identity_worklist",
    description:
      "Prioritized queue of active entities needing a steward's attention " +
      "(open conflicts and/or confidence below weak_confidence), highest " +
      "conflict count first then lowest confidence.",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        weak_confidence: { type: "number", default: 0.6 },
        limit: { type: "integer", default: 50 },
        path: { type: "string", description: "Identity DB path" },
      },
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
    actor: e.actor ?? null,
    trust: e.trust ?? null,
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
    actor: ev.actor ?? null,
    trust: ev.trust ?? null,
    claim_type: ev.claimType ?? null,
    evidence_ref: ev.evidenceRef ?? null,
    previous_claim_id: ev.previousClaimId ?? null,
    entry_hash: ev.entryHash ?? null,
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

/**
 * Resolve the (actor, trust) provenance for an agent-driven mutation, mirroring
 * Python `mcp/identity_tools.py::_actor_trust`: `actor` defaults to `"agent"`
 * (MCP is the agent surface); when `trust` is absent it's derived from the
 * actor's prefix (`steward:` -> 1.0, else 0.5) via the shared `trustForSource`
 * map, so an agent write is recorded at lower authority than a steward's.
 */
function actorTrust(args: Record<string, unknown>): { actor: string; trust: number } {
  const actor = strArg(args, "actor") ?? "agent";
  const rawTrust = args["trust"];
  const trust =
    typeof rawTrust === "number" ? rawTrust : trustForSource(actor.split(":")[0] ?? actor);
  return { actor, trust };
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
        ...actorTrust(args),
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
        ...actorTrust(args),
      });
      return { new_entity_id: res.newEntityId, moved: res.moved, at: res.at };
    }

    if (name === "identity_claim") {
      const entityId = strArg(args, "entity_id");
      const recordId = strArg(args, "record_id");
      if (!entityId || !recordId) {
        return { error: "Missing required parameters: entity_id, record_id" };
      }
      const reason = strArg(args, "reason");
      const res = await claimRecord(store, entityId, recordId, {
        ...(reason !== undefined ? { reason } : {}),
        runName: "mcp",
        ...actorTrust(args),
      });
      return {
        entity_id: res.entityId,
        record_id: res.recordId,
        moved: res.moved,
        from_entity: res.fromEntity,
        at: res.at,
      };
    }

    if (name === "identity_resolve_conflict") {
      const recordAId = strArg(args, "record_a_id");
      const recordBId = strArg(args, "record_b_id");
      const resolution = strArg(args, "resolution");
      if (!recordAId || !recordBId || !resolution) {
        return {
          error: "Missing required parameters: record_a_id, record_b_id, resolution",
        };
      }
      const reason = strArg(args, "reason");
      const dataset = strArg(args, "dataset");
      const apply = args["apply"] === undefined ? true : Boolean(args["apply"]);
      const res = await mediateConflict(store, recordAId, recordBId, resolution, {
        ...(reason !== undefined ? { reason } : {}),
        ...(dataset !== undefined ? { dataset } : {}),
        apply,
        ...actorTrust(args),
      });
      return {
        record_a_id: res.recordAId,
        record_b_id: res.recordBId,
        resolution: res.resolution,
        entity_id: res.entityId,
        applied: res.applied,
        action: res.action,
        at: res.at,
      };
    }

    if (name === "identity_audit") {
      const limit = intArg(args, "limit", 500);
      const dataset = strArg(args, "dataset");
      const actorFilter = strArg(args, "actor");
      let events = await store.exportAuditLog(dataset);
      if (actorFilter !== undefined) events = events.filter((e) => e.actor === actorFilter);
      const items = events.slice(0, limit).map((e) => ({
        event_id: e.eventId,
        entity_id: e.entityId,
        kind: e.kind,
        actor: e.actor ?? null,
        trust: e.trust ?? null,
        claim_type: e.claimType ?? null,
        evidence_ref: e.evidenceRef ?? null,
        previous_claim_id: e.previousClaimId ?? null,
        recorded_at: e.recordedAt ? pyIsoformat(e.recordedAt) : null,
        run_name: e.runName,
        dataset: e.dataset,
        payload: e.payload,
      }));
      return { items, total: events.length };
    }

    if (name === "identity_audit_seal") {
      const { actor } = actorTrust(args);
      const dataset = strArg(args, "dataset");
      const seal = await sealAuditLog(store, { actor, dataset: dataset ?? null });
      if (seal === null) return { sealed: false, reason: "no new events to seal" };
      return {
        sealed: true,
        seal_id: seal.sealId,
        root_hash: seal.rootHash,
        event_count: seal.eventCount,
        last_event_id: seal.lastEventId,
        dataset: seal.dataset,
        actor: seal.actor,
      };
    }

    if (name === "identity_audit_verify") {
      const dataset = strArg(args, "dataset");
      const result = await verifyAuditChain(store, { dataset: dataset ?? null });
      return {
        ok: result.ok,
        events_checked: result.eventsChecked,
        seals_checked: result.sealsChecked,
        content_mismatches: result.contentMismatches,
        seal_mismatches: result.sealMismatches,
        missing_sealed_events: result.missingSealedEvents,
        summary: verificationSummary(result),
      };
    }

    if (name === "identity_show") {
      const entityId = strArg(args, "entity_id");
      if (!entityId) return { error: "Missing required parameter: entity_id" };
      const view = await getEntity(store, entityId, intArg(args, "event_limit", 100));
      return view ? viewToDict(view) : { found: false };
    }

    if (name === "identity_profile") {
      const entityId = strArg(args, "entity_id");
      if (!entityId) return { error: "Missing required parameter: entity_id" };
      const prof = await entityProfile(store, entityId);
      return prof ?? { found: false };
    }

    if (name === "identity_stats") {
      const dataset = strArg(args, "dataset");
      return await identitySummaryStats(store, dataset ?? null);
    }

    if (name === "identity_worklist") {
      const dataset = strArg(args, "dataset");
      const rawWeak = args["weak_confidence"];
      const items = await stewardWorklist(store, {
        dataset: dataset ?? null,
        weakConfidence: typeof rawWeak === "number" ? rawWeak : 0.6,
        limit: intArg(args, "limit", 50),
      });
      return { items };
    }

    return { error: `Unknown identity tool: ${name}` };
  } finally {
    await store.close();
  }
}
