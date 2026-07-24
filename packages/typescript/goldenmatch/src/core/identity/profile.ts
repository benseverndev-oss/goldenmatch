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
import type { IdentityStore, IdentityNode } from "./types.js";
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
