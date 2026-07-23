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

/**
 * Conflicting evidence edges awaiting steward review. Mirrors Python
 * `goldenmatch.identity.query.find_conflicts` (TS returns the rich
 * `EvidenceEdge` type; the snake_case dict shape is applied at the MCP/REST
 * boundary, as in `node/api/server.ts`).
 */
export async function findConflicts(
  store: IdentityStore,
  dataset?: string,
): Promise<EvidenceEdge[]> {
  return store.findConflicts(dataset);
}

/**
 * Temporal event log for one identity. Mirrors Python
 * `goldenmatch.identity.query.history`.
 */
export async function history(
  store: IdentityStore,
  entityId: string,
  limit?: number,
): Promise<IdentityEvent[]> {
  return store.history(entityId, limit);
}

export async function manualMerge(
  store: IdentityStore,
  keepEntityId: string,
  absorbEntityId: string,
  opts: { reason?: string; runName?: string; actor?: string; trust?: number } = {},
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
  // Provenance (#1075): stamp WHO merged these + their trust onto both events.
  // Spread so an absent actor/trust is never set (exactOptionalPropertyTypes).
  const prov = provenance(opts);
  await store.emitEvent({
    eventId: null,
    entityId: keepEntityId,
    kind: "manual_merge",
    payload: { absorbed: absorbEntityId, reason: opts.reason ?? null },
    runName,
    dataset: winner.dataset,
    ...prov,
    recordedAt: now,
  });
  await store.emitEvent({
    eventId: null,
    entityId: absorbEntityId,
    kind: "manual_merge",
    payload: { merged_into: keepEntityId, reason: opts.reason ?? null },
    runName,
    dataset: loser.dataset,
    ...prov,
    recordedAt: now,
  });
  return { keep: keepEntityId, absorbed: absorbEntityId, at: now.toISOString() };
}

/**
 * Build the `{ actor?, trust? }` provenance spread from an option bag. Omits a
 * key entirely when its value is undefined so `exactOptionalPropertyTypes`
 * never sees `undefined` assigned, and a provenance-free call stays byte-for
 * -byte identical to the pre-provenance events (Python actor/trust = None).
 */
function provenance(opts: {
  actor?: string;
  trust?: number;
}): { actor?: string; trust?: number } {
  return {
    ...(opts.actor !== undefined ? { actor: opts.actor } : {}),
    ...(opts.trust !== undefined ? { trust: opts.trust } : {}),
  };
}

export async function manualSplit(
  store: IdentityStore,
  entityId: string,
  recordIds: readonly string[],
  opts: { reason?: string; runName?: string; actor?: string; trust?: number } = {},
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
  const prov = provenance(opts);
  await store.emitEvent({
    eventId: null,
    entityId,
    kind: "manual_split",
    payload: { split_to: newId, records: moved, reason: opts.reason ?? null },
    runName,
    dataset: parent.dataset,
    ...prov,
    recordedAt: now,
  });
  await store.emitEvent({
    eventId: null,
    entityId: newId,
    kind: "manual_split",
    payload: { split_from: entityId, records: moved, reason: opts.reason ?? null },
    runName,
    dataset: parent.dataset,
    ...prov,
    recordedAt: now,
  });
  return { newEntityId: newId, moved, at: now.toISOString() };
}

/**
 * Claim `recordId` into `entityId`, moving it out of any prior entity
 * ("this record belongs to that identity"). Ports Python
 * `goldenmatch.identity.query.claim_record`.
 *
 * Emits a `claimed` event on BOTH the gaining entity and (when the record
 * was previously attached elsewhere) the losing entity. Idempotent: claiming
 * a record already in `entityId` is a no-op (`moved: false`, no events), so a
 * replay does nothing.
 */
export async function claimRecord(
  store: IdentityStore,
  entityId: string,
  recordId: string,
  opts: { reason?: string; runName?: string; actor?: string; trust?: number } = {},
): Promise<{
  entityId: string;
  recordId: string;
  moved: boolean;
  fromEntity: string | null;
  at: string;
}> {
  const target = await store.getIdentity(entityId);
  if (!target) throw new Error(`Entity ${entityId} not found`);
  if (target.status !== "active") throw new Error("Target entity must be active");
  const rec = await store.getRecord(recordId);
  if (!rec) throw new Error(`Record ${recordId} not found`);

  const prevEntity = rec.entityId;
  const now = new Date();
  if (prevEntity === entityId) {
    return {
      entityId,
      recordId,
      moved: false,
      fromEntity: prevEntity,
      at: now.toISOString(),
    };
  }

  await store.upsertRecord({ ...rec, entityId, lastSeenAt: now });
  const runName = opts.runName ?? "manual";
  const prov = provenance(opts);
  await store.emitEvent({
    eventId: null,
    entityId,
    kind: "claimed",
    payload: { record_id: recordId, from_entity: prevEntity, reason: opts.reason ?? null },
    runName,
    dataset: target.dataset,
    ...prov,
    recordedAt: now,
  });
  if (prevEntity) {
    await store.emitEvent({
      eventId: null,
      entityId: prevEntity,
      kind: "claimed",
      payload: { record_id: recordId, to_entity: entityId, reason: opts.reason ?? null },
      runName,
      dataset: target.dataset,
      ...prov,
      recordedAt: now,
    });
  }
  return {
    entityId,
    recordId,
    moved: true,
    fromEntity: prevEntity,
    at: now.toISOString(),
  };
}
