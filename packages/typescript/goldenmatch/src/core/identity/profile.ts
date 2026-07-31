/**
 * profile.ts -- entity profiles + stewardship-ops views (MDM read-side).
 *
 * Edge-safe port of Python `identity/profile.py` (#1114). The read surface an
 * MDM steward needs: per-entity profile, a graph-level health summary, and a
 * prioritized worklist. Read-only, computed from the durable store.
 *
 * Backs the `identity_profile` / `identity_stats` / `identity_worklist` MCP
 * tools. Each function returns the Python `as_dict()`-shaped object directly.
 */
import type { IdentityStore, IdentityNode, SourceRecord, IdentityEvent } from "./types.js";
import { pyIsoformat } from "./pyDatetime.js";

const PAGE = 500;

// Events that advance an entity's canonical version (a structural change).
const STRUCTURAL_EVENTS: ReadonlySet<string> = new Set([
  "created",
  "absorbed_record",
  "merged_with",
  "split_from",
  "manual_merge",
  "manual_split",
]);

function iso(d: Date | null | undefined): string | null {
  return d ? pyIsoformat(d) : null;
}

async function* iterEntities(
  store: IdentityStore,
  dataset: string | null,
  status: string | null,
): AsyncGenerator<IdentityNode> {
  let offset = 0;
  for (;;) {
    const page = await store.listIdentities({
      ...(dataset !== null ? { dataset } : {}),
      ...(status !== null ? { status: status as IdentityNode["status"] } : {}),
      limit: PAGE,
      offset,
    });
    for (const n of page) yield n;
    if (page.length < PAGE) break;
    offset += PAGE;
  }
}

/** Full profile of one entity, or `null` if it doesn't exist. Mirrors Python
 * `entity_profile`. */
export async function entityProfile(
  store: IdentityStore,
  entityId: string,
): Promise<Record<string, unknown> | null> {
  const node = await store.getIdentity(entityId);
  if (node === null) return null;

  const records = await store.getRecordsForEntity(entityId);
  const sourceCounts: Record<string, number> = {};
  let firstSeen: Date | null = null;
  let lastSeen: Date | null = null;
  for (const rec of records) {
    sourceCounts[rec.source] = (sourceCounts[rec.source] ?? 0) + 1;
    if (rec.firstSeenAt && (firstSeen === null || rec.firstSeenAt < firstSeen)) {
      firstSeen = rec.firstSeenAt;
    }
    if (rec.lastSeenAt && (lastSeen === null || rec.lastSeenAt > lastSeen)) {
      lastSeen = rec.lastSeenAt;
    }
  }

  const edges = await store.edgesForEntity(entityId);
  let conflictCount = 0;
  for (const e of edges) if (e.kind === "conflicts_with") conflictCount += 1;
  const events = await store.history(entityId);
  let version = 0;
  for (const ev of events) if (STRUCTURAL_EVENTS.has(ev.kind)) version += 1;

  return {
    entity_id: node.entityId,
    status: node.status,
    merged_into: node.mergedInto,
    dataset: node.dataset,
    confidence: node.confidence,
    golden_record: node.goldenRecord,
    record_count: records.length,
    sources: Object.keys(sourceCounts).sort(),
    source_counts: sourceCounts,
    conflict_count: conflictCount,
    edge_count: edges.length,
    version,
    created_at: iso(node.createdAt),
    updated_at: iso(node.updatedAt),
    first_seen: iso(firstSeen),
    last_seen: iso(lastSeen),
  };
}

function median(nums: number[]): number {
  if (nums.length === 0) return 0;
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/** Graph-level health summary. Mirrors Python `identity_summary_stats`. */
export async function identitySummaryStats(
  store: IdentityStore,
  dataset: string | null = null,
): Promise<Record<string, unknown>> {
  const byStatus: Record<string, number> = {};
  let totalEntities = 0;
  for await (const node of iterEntities(store, dataset, null)) {
    totalEntities += 1;
    byStatus[node.status] = (byStatus[node.status] ?? 0) + 1;
  }

  const recordCounts: number[] = [];
  const sourceBreakdown: Record<string, number> = {};
  const largest: Array<[string, number]> = [];
  for await (const node of iterEntities(store, dataset, "active")) {
    const recs = await store.getRecordsForEntity(node.entityId);
    recordCounts.push(recs.length);
    for (const rec of recs) sourceBreakdown[rec.source] = (sourceBreakdown[rec.source] ?? 0) + 1;
    largest.push([node.entityId, recs.length]);
  }

  let totalRecords = 0;
  let singleton = 0;
  let multi = 0;
  let mx = 0; // loop-based max (avoid spread that can overflow on big arrays)
  for (const c of recordCounts) {
    totalRecords += c;
    if (c === 1) singleton += 1;
    if (c > 1) multi += 1;
    if (c > mx) mx = c;
  }
  const avg = recordCounts.length ? totalRecords / recordCounts.length : 0;
  const p50 = median(recordCounts);
  largest.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const conflicts = await store.findConflicts(dataset ?? undefined);

  return {
    dataset,
    total_entities: totalEntities,
    by_status: byStatus,
    total_records: totalRecords,
    records_per_entity_avg: Math.round(avg * 1e4) / 1e4,
    records_per_entity_p50: p50,
    records_per_entity_max: mx,
    singleton_entities: singleton,
    multi_record_entities: multi,
    total_conflicts: conflicts.length,
    source_breakdown: sourceBreakdown,
    largest_entities: largest.slice(0, 10).map(([entity_id, record_count]) => ({
      entity_id,
      record_count,
    })),
  };
}

// ── Customer 360 (unified serving read) ──────────────────────────────────────
//
// One read that composes the durable spine into the whole picture of a customer:
// golden record + per-field source provenance + every linked source record + the
// event timeline + the relationship overlay. Edge-safe port of Python
// `identity/profile.py::customer_360` — the serving realization of decision 0049.
// Backs the `customer_360` MCP tool and the REST `/identities/{id}/360` route.

/** Normalize a payload/golden cell for provenance comparison: `null` stays
 * `null`; everything else is string-cast + trimmed, so a golden `42` matches a
 * source payload `"42"` without treating an empty string as a real value.
 * (Float formatting differs Python↔JS — `String(5.0)` is `"5"` — the documented
 * PPRL-class gap; irrelevant for the string payloads a 360 typically carries.) */
function norm(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const s = String(value).trim();
  return s || null;
}

interface FieldContribution {
  source: string;
  record_id: string;
  source_pk: string;
  value: unknown;
  last_seen: string | null;
}

/** Attribute each golden field to the source records that carry its value.
 * Winner = the contributing record with the most recent `lastSeenAt` (ties broken
 * by `recordId` descending, matching Python). Mirrors `_field_provenance`. */
function fieldProvenance(
  goldenRecord: Record<string, unknown> | null,
  records: readonly SourceRecord[],
): Array<Record<string, unknown>> {
  if (!goldenRecord) return [];
  const out: Array<Record<string, unknown>> = [];
  for (const [fname, gvalue] of Object.entries(goldenRecord)) {
    const gnorm = norm(gvalue);
    const contributors: Array<FieldContribution & { _ts: number }> = [];
    const conflicts = new Map<string, Record<string, unknown>>();
    for (const rec of records) {
      const payload = rec.payload ?? {};
      if (!(fname in payload)) continue;
      const rnorm = norm(payload[fname]);
      if (rnorm !== null && rnorm === gnorm) {
        contributors.push({
          source: rec.source,
          record_id: rec.recordId,
          source_pk: rec.sourcePk,
          value: payload[fname],
          last_seen: rec.lastSeenAt ? pyIsoformat(rec.lastSeenAt) : null,
          _ts: rec.lastSeenAt ? rec.lastSeenAt.getTime() : -Infinity,
        });
      } else if (rnorm !== null && !conflicts.has(rnorm)) {
        conflicts.set(rnorm, {
          value: payload[fname],
          source: rec.source,
          record_id: rec.recordId,
        });
      }
    }
    // Most recent first; ties broken by record_id descending (Python reverse sort).
    contributors.sort((a, b) => {
      if (a._ts !== b._ts) return b._ts - a._ts;
      return a.record_id < b.record_id ? 1 : a.record_id > b.record_id ? -1 : 0;
    });
    const winner = contributors[0];
    out.push({
      field: fname,
      value: gvalue,
      winning_source: winner ? winner.source : null,
      winning_record_id: winner ? winner.record_id : null,
      contributors: contributors.map(({ _ts, ...c }) => c),
      conflicting_values: [...conflicts.values()].sort((a, b) =>
        String(a.value) < String(b.value) ? -1 : String(a.value) > String(b.value) ? 1 : 0,
      ),
    });
  }
  return out;
}

function recordAsDict(rec: SourceRecord): Record<string, unknown> {
  return {
    record_id: rec.recordId,
    source: rec.source,
    source_pk: rec.sourcePk,
    dataset: rec.dataset,
    first_seen: rec.firstSeenAt ? pyIsoformat(rec.firstSeenAt) : null,
    last_seen: rec.lastSeenAt ? pyIsoformat(rec.lastSeenAt) : null,
    payload: rec.payload,
  };
}

function eventAsDict(ev: IdentityEvent): Record<string, unknown> {
  const reason =
    ev.payload && typeof ev.payload === "object" ? (ev.payload as Record<string, unknown>).reason ?? null : null;
  return {
    event_id: ev.eventId,
    kind: ev.kind,
    actor: ev.actor ?? null,
    trust: ev.trust ?? null,
    run_name: ev.runName,
    dataset: ev.dataset,
    reason,
    recorded_at: ev.recordedAt ? pyIsoformat(ev.recordedAt) : null,
  };
}

/** The single Customer 360 serving read: everything known about one entity,
 * composed from the durable store in one call. Returns `null` if the entity
 * doesn't exist. JSON-ready (the `as_dict()`-shaped object Python returns), so
 * every serving surface returns the same 360 shape. Mirrors Python
 * `customer_360_page`.
 *
 * The relationship overlay (decision 0049) is a federated read the edge-safe
 * store doesn't implement; like Python's `AttributeError → []` fallback, it
 * degrades to an empty list here (a store MAY provide an optional
 * `getRelationships(entityId)` and it will be used). */
export async function customer360Page(
  store: IdentityStore,
  entityId: string,
  opts: { includeRelationships?: boolean; timelineLimit?: number } = {},
): Promise<Record<string, unknown> | null> {
  const includeRelationships = opts.includeRelationships ?? true;

  const profile = await entityProfile(store, entityId);
  if (profile === null) return null;

  const records = await store.getRecordsForEntity(entityId);
  const goldenRecord = (profile.golden_record ?? null) as Record<string, unknown> | null;

  let relationships: Array<Record<string, unknown>> = [];
  if (includeRelationships) {
    const getRel = (store as { getRelationships?: (id: string) => Promise<Array<Record<string, unknown>>> })
      .getRelationships;
    if (typeof getRel === "function") {
      try {
        relationships = await getRel.call(store, entityId);
      } catch {
        relationships = [];
      }
    }
  }

  const events = await store.history(entityId, opts.timelineLimit);

  return {
    entity_id: entityId,
    status: profile.status,
    merged_into: profile.merged_into,
    dataset: profile.dataset,
    confidence: profile.confidence,
    version: profile.version,
    record_count: profile.record_count,
    sources: profile.sources,
    source_counts: profile.source_counts,
    conflict_count: profile.conflict_count,
    first_seen: profile.first_seen,
    last_seen: profile.last_seen,
    golden_record: goldenRecord,
    field_provenance: fieldProvenance(goldenRecord, records),
    source_records: records.map(recordAsDict),
    timeline: events.map(eventAsDict),
    relationships,
  };
}

/** Prioritized queue of active entities needing a steward's attention. Mirrors
 * Python `steward_worklist`. */
export async function stewardWorklist(
  store: IdentityStore,
  opts: { dataset?: string | null; weakConfidence?: number; limit?: number } = {},
): Promise<Array<Record<string, unknown>>> {
  const dataset = opts.dataset ?? null;
  const weakConfidence = opts.weakConfidence ?? 0.6;
  const limit = opts.limit ?? 50;

  const conflictsByEntity: Record<string, number> = {};
  for (const e of await store.findConflicts(dataset ?? undefined)) {
    conflictsByEntity[e.entityId] = (conflictsByEntity[e.entityId] ?? 0) + 1;
  }

  const items: Array<{
    entity_id: string;
    reasons: string[];
    conflict_count: number;
    confidence: number | null;
    record_count: number;
  }> = [];
  for await (const node of iterEntities(store, dataset, "active")) {
    const cc = conflictsByEntity[node.entityId] ?? 0;
    const lowConf = node.confidence !== null && node.confidence < weakConfidence;
    if (cc === 0 && !lowConf) continue;
    const reasons: string[] = [];
    if (cc > 0) reasons.push("has_conflicts");
    if (lowConf) reasons.push("low_confidence");
    items.push({
      entity_id: node.entityId,
      reasons,
      conflict_count: cc,
      confidence: node.confidence,
      record_count: (await store.getRecordsForEntity(node.entityId)).length,
    });
  }

  items.sort((a, b) => {
    if (b.conflict_count !== a.conflict_count) return b.conflict_count - a.conflict_count;
    const ac = a.confidence ?? 1.0;
    const bc = b.confidence ?? 1.0;
    if (ac !== bc) return ac - bc;
    return a.entity_id < b.entity_id ? -1 : a.entity_id > b.entity_id ? 1 : 0;
  });
  return items.slice(0, limit);
}
