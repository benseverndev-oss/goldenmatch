/**
 * Read-side API + manual ops for the Identity Graph. Mirrors the Python
 * `goldenmatch/identity/query.py` surface.
 */

import { newEntityId } from "./new-entity-id.js";
import type {
  EvidenceEdge,
  IdentityEvent,
  IdentityNode,
  IdentityStore,
  SourceRecord,
} from "./types.js";

export interface IdentityView {
  node: IdentityNode;
  records: SourceRecord[];
  edges: EvidenceEdge[];
  events: IdentityEvent[];
}

export async function getEntity(
  store: IdentityStore,
  entityId: string,
  eventLimit = 100,
): Promise<IdentityView | null> {
  const node = await store.getIdentity(entityId);
  if (!node) return null;
  const [records, edges, events] = await Promise.all([
    store.getRecordsForEntity(entityId),
    store.edgesForEntity(entityId),
    store.history(entityId, eventLimit),
  ]);
  return { node, records, edges, events };
}

export async function findByRecord(
  store: IdentityStore,
  recordId: string,
): Promise<IdentityView | null> {
  const eid = await store.findEntityByRecord(recordId);
  if (!eid) return null;
  return getEntity(store, eid);
}

export async function listEntities(
  store: IdentityStore,
  opts: { dataset?: string; status?: IdentityNode["status"]; limit?: number; offset?: number } = {},
): Promise<IdentityNode[]> {
  return store.listIdentities(opts);
}

export async function manualMerge(
  store: IdentityStore,
  keepEntityId: string,
  absorbEntityId: string,
  opts: { reason?: string; runName?: string } = {},
): Promise<{ keep: string; absorbed: string; at: string }> {
  const winner = await store.getIdentity(keepEntityId);
  const loser = await store.getIdentity(absorbEntityId);
  if (!winner || !loser) throw new Error("Both entity_ids must exist");
  if (winner.status !== "active") throw new Error("Winner must be active");

  const now = new Date();
  const losersRecords = await store.getRecordsForEntity(absorbEntityId);
  for (const r of losersRecords) {
    await store.upsertRecord({ ...r, entityId: keepEntityId, lastSeenAt: now });
  }
  await store.retireIdentity(absorbEntityId, keepEntityId);
  const runName = opts.runName ?? "manual";
  await store.emitEvent({
    eventId: null,
    entityId: keepEntityId,
    kind: "manual_merge",
    payload: { absorbed: absorbEntityId, reason: opts.reason ?? null },
    runName,
    dataset: winner.dataset,
    recordedAt: now,
  });
  await store.emitEvent({
    eventId: null,
    entityId: absorbEntityId,
    kind: "manual_merge",
    payload: { merged_into: keepEntityId, reason: opts.reason ?? null },
    runName,
    dataset: loser.dataset,
    recordedAt: now,
  });
  return { keep: keepEntityId, absorbed: absorbEntityId, at: now.toISOString() };
}

export async function manualSplit(
  store: IdentityStore,
  entityId: string,
  recordIds: readonly string[],
  opts: { reason?: string; runName?: string } = {},
): Promise<{ newEntityId: string; moved: string[]; at: string }> {
  const parent = await store.getIdentity(entityId);
  if (!parent) throw new Error(`Entity ${entityId} not found`);
  if (recordIds.length === 0) throw new Error("recordIds must be non-empty");

  const now = new Date();
  const newId = newEntityId();
  await store.upsertIdentity({
    entityId: newId,
    status: "active",
    mergedInto: null,
    goldenRecord: null,
    confidence: null,
    dataset: parent.dataset,
    createdAt: now,
    updatedAt: now,
  });

  const moved: string[] = [];
  for (const rid of recordIds) {
    const rec = await store.getRecord(rid);
    if (!rec || rec.entityId !== entityId) continue;
    await store.upsertRecord({ ...rec, entityId: newId, lastSeenAt: now });
    moved.push(rid);
  }
  const runName = opts.runName ?? "manual";
  await store.emitEvent({
    eventId: null,
    entityId,
    kind: "manual_split",
    payload: { split_to: newId, records: moved, reason: opts.reason ?? null },
    runName,
    dataset: parent.dataset,
    recordedAt: now,
  });
  await store.emitEvent({
    eventId: null,
    entityId: newId,
    kind: "manual_split",
    payload: { split_from: entityId, records: moved, reason: opts.reason ?? null },
    runName,
    dataset: parent.dataset,
    recordedAt: now,
  });
  return { newEntityId: newId, moved, at: now.toISOString() };
}
