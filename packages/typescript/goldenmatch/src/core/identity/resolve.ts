/**
 * resolve.ts -- map run-local clusters to durable identities.
 *
 * Edge-safe port of the Python `goldenmatch/identity/resolve.py` core
 * (dict/Map path). Per cluster it decides create / absorb / merge from which
 * existing identities cover the cluster's records, upserts nodes + records,
 * records SAME_AS evidence edges, emits an idempotent event log, and (when a
 * weak-confidence threshold is set) flags the bottleneck pair as a
 * `conflicts_with` edge.
 *
 * Parity is structural: entity ids come from `newEntityId()` (UUIDs), so the
 * cross-language fixture compares ResolveSummary counts + the record->entity
 * grouping, not literal ids (see tests/parity/resolve-clusters.test.ts).
 *
 * Deferred vs Python (documented, not ported): the postgres bulk fast-path,
 * the SP-A `cluster_frames` path, the legacy content-hash migration candidate,
 * `controllerSnapshot`, and the batch-fingerprint optimization. Distributed /
 * Ray population stays Python-only by design (see CLAUDE.md).
 */
import { pairKey, parsePairKey } from "../cluster.js";
import { recordFingerprint } from "../record-fingerprint.js";
import { newEntityId } from "./new-entity-id.js";
import type { ClusterInfo, Row } from "../types.js";
import type { IdentityStore } from "./types.js";

export interface ResolveSummary {
  created: number;
  absorbedRecords: number;
  merged: number;
  split: number;
  edgesAdded: number;
  eventsEmitted: number;
  recordsUpserted: number;
  conflictsFlagged: number;
}

export interface ResolveOptions {
  readonly clusters: ReadonlyMap<number, ClusterInfo>;
  readonly rows: readonly Row[];
  readonly store: IdentityStore;
  readonly runName?: string;
  readonly matchkeyName?: string | null;
  readonly dataset?: string | null;
  readonly sourcePkCol?: string | null;
  readonly emitSingletons?: boolean;
  readonly weakConfidenceThreshold?: number;
}

function emptySummary(): ResolveSummary {
  return {
    created: 0,
    absorbedRecords: 0,
    merged: 0,
    split: 0,
    edgesAdded: 0,
    eventsEmitted: 0,
    recordsUpserted: 0,
    conflictsFlagged: 0,
  };
}

function payloadOf(row: Row): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(row)) {
    if (!k.startsWith("__")) out[k] = v;
  }
  return out;
}

/** Most-complete rollup: per column, the longest non-empty string value. */
function goldenFromMembers(
  rows: readonly Row[],
  rowIdToIndex: ReadonlyMap<number, number>,
  members: readonly number[],
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const cols = new Set<string>();
  const memberRows: Row[] = [];
  for (const m of members) {
    const idx = rowIdToIndex.get(m);
    if (idx === undefined) continue;
    const r = rows[idx]!;
    memberRows.push(r);
    for (const k of Object.keys(r)) if (!k.startsWith("__")) cols.add(k);
  }
  for (const col of cols) {
    let best: unknown;
    let bestLen = -1;
    for (const r of memberRows) {
      const v = (r as Record<string, unknown>)[col];
      if (v === null || v === undefined || v === "") continue;
      const len = String(v).length;
      if (len > bestLen) {
        bestLen = len;
        best = v;
      }
    }
    if (bestLen >= 0) out[col] = best;
  }
  return out;
}

export async function resolveClusters(
  opts: ResolveOptions,
): Promise<ResolveSummary> {
  const {
    clusters,
    rows,
    store,
    runName = "",
    matchkeyName = null,
    dataset = null,
    sourcePkCol = null,
    emitSingletons = true,
    weakConfidenceThreshold = 0.6,
  } = opts;

  const summary = emptySummary();
  if (rows.length === 0) return summary;

  // 1. row_id -> record_id / source / payload.
  const rowIdToIndex = new Map<number, number>();
  const rowIdToRecId = new Map<number, string>();
  const rowIdToSource = new Map<number, string>();
  const rowIdToPk = new Map<number, string>();
  const rowIdToPayload = new Map<number, Record<string, unknown>>();

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]!;
    const rawRid = (row as Record<string, unknown>)["__row_id__"];
    if (rawRid === null || rawRid === undefined) continue;
    const rid = Number(rawRid);
    const source = String(
      (row as Record<string, unknown>)["__source__"] ?? "dataframe",
    );
    const payload = payloadOf(row);
    let recId: string;
    let pk: string;
    const pkVal =
      sourcePkCol != null ? (row as Record<string, unknown>)[sourcePkCol] : undefined;
    if (sourcePkCol != null && pkVal !== null && pkVal !== undefined) {
      pk = String(pkVal);
      recId = `${source}:${pk}`;
    } else {
      recId = await recordFingerprint({ ...payload, __source__: source });
      pk = recId.startsWith(`${source}:`) ? recId.slice(source.length + 1) : recId;
    }
    rowIdToIndex.set(rid, i);
    rowIdToRecId.set(rid, recId);
    rowIdToSource.set(rid, source);
    rowIdToPk.set(rid, pk);
    rowIdToPayload.set(rid, payload);
  }

  // 2. Pre-flight: which record_ids already map to an entity.
  const allRecIds = [...new Set(rowIdToRecId.values())];
  const existingById = await store.lookupEntityIds(allRecIds);

  // 3. Iterate clusters in ascending cluster_id order (matches Python).
  const clusterIds = [...clusters.keys()].sort((a, b) => a - b);
  for (const clusterId of clusterIds) {
    const info = clusters.get(clusterId)!;
    const members = info.members;
    if (members.length === 0) continue;
    const size = members.length;
    if (size === 1 && !emitSingletons) continue;

    const recordIds = members
      .map((m) => rowIdToRecId.get(m))
      .filter((r): r is string => r !== undefined);
    if (recordIds.length === 0) continue;

    // 3a. Existing identities covering these records.
    const existing = new Map<string, string>();
    for (const rid of recordIds) {
      const eid = existingById.get(rid);
      if (eid !== undefined) existing.set(rid, eid);
    }
    const uniqueEntities = [...new Set(existing.values())];

    const confidence = info.confidence ?? null;
    const golden = goldenFromMembers(rows, rowIdToIndex, members);
    const now = new Date();
    let entityId: string;

    if (uniqueEntities.length === 0) {
      // create
      entityId = newEntityId();
      await store.upsertIdentity({
        entityId,
        status: "active",
        mergedInto: null,
        goldenRecord: golden,
        confidence,
        dataset,
        createdAt: now,
        updatedAt: now,
      });
      if (!(await store.hasRunEvent(entityId, runName, "created"))) {
        await store.emitEvent({
          eventId: null,
          entityId,
          kind: "created",
          payload: { cluster_id: clusterId, member_count: size, record_ids: recordIds },
          runName,
          dataset,
          recordedAt: now,
        });
        summary.eventsEmitted += 1;
      }
      summary.created += 1;
    } else if (uniqueEntities.length === 1) {
      // absorb
      entityId = uniqueEntities[0]!;
      const node = await store.getIdentity(entityId);
      await store.upsertIdentity({
        entityId,
        status: node?.status ?? "active",
        mergedInto: node?.mergedInto ?? null,
        goldenRecord: golden,
        confidence,
        dataset,
        createdAt: node?.createdAt ?? now,
        updatedAt: now,
      });
      for (const rid of recordIds) {
        if (!existing.has(rid)) {
          await store.emitEvent({
            eventId: null,
            entityId,
            kind: "absorbed_record",
            payload: { record_id: rid, cluster_id: clusterId },
            runName,
            dataset,
            recordedAt: now,
          });
          summary.eventsEmitted += 1;
          summary.absorbedRecords += 1;
        }
      }
    } else {
      // merge: winner = most members in this cluster, tie-break oldest createdAt.
      const counts = new Map<string, number>();
      for (const eid of existing.values()) counts.set(eid, (counts.get(eid) ?? 0) + 1);
      const ages = new Map<string, number>();
      for (const eid of uniqueEntities) {
        const n = await store.getIdentity(eid);
        ages.set(eid, n ? n.createdAt.getTime() : now.getTime());
      }
      const ranked = [...counts.entries()].sort(
        (x, y) => y[1] - x[1] || ages.get(x[0])! - ages.get(y[0])!,
      );
      const winner = ranked[0]![0];
      const losers = ranked.slice(1).map((kv) => kv[0]);
      entityId = winner;
      const winnerNode = await store.getIdentity(winner);
      await store.upsertIdentity({
        entityId: winner,
        status: "active",
        mergedInto: null,
        goldenRecord: golden,
        confidence,
        dataset,
        createdAt: winnerNode?.createdAt ?? now,
        updatedAt: now,
      });
      await store.emitEvent({
        eventId: null,
        entityId: winner,
        kind: "merged_with",
        payload: { absorbed: losers, cluster_id: clusterId, member_count: size },
        runName,
        dataset,
        recordedAt: now,
      });
      summary.eventsEmitted += 1;
      for (const loser of losers) {
        await store.retireIdentity(loser, winner);
        await store.emitEvent({
          eventId: null,
          entityId: loser,
          kind: "merged_with",
          payload: { merged_into: winner },
          runName,
          dataset,
          recordedAt: now,
        });
        summary.eventsEmitted += 1;
      }
      summary.merged += 1;

      // Reassign loser records to the winner before re-upserting cluster rows.
      const loserSet = new Set(losers);
      for (const [rid, oldEid] of existing) {
        if (loserSet.has(oldEid)) {
          const rec = await store.getRecord(rid);
          if (rec) {
            await store.upsertRecord({ ...rec, entityId: winner, lastSeenAt: new Date() });
          }
        }
      }
    }

    // 3c. Upsert all cluster records under the chosen entity.
    for (const m of members) {
      const rid = rowIdToRecId.get(m);
      if (rid === undefined) continue;
      await store.upsertRecord({
        recordId: rid,
        source: rowIdToSource.get(m)!,
        sourcePk: rowIdToPk.get(m)!,
        recordHash: "",
        entityId,
        payload: rowIdToPayload.get(m) ?? null,
        dataset,
        firstSeenAt: now,
        lastSeenAt: now,
      });
      summary.recordsUpserted += 1;
      existingById.set(rid, entityId);
    }

    // 3d. SAME_AS evidence edges for every scored within-cluster pair.
    for (const [pk, score] of info.pairScores) {
      const [a, b] = parsePairKey(pk);
      const ra = rowIdToRecId.get(a);
      const rb = rowIdToRecId.get(b);
      if (!ra || !rb) continue;
      await store.addEdge({
        edgeId: null,
        entityId,
        recordAId: ra,
        recordBId: rb,
        kind: "same_as",
        score,
        matchkeyName,
        fieldScores: null,
        negativeEvidence: null,
        controllerSnapshot: null,
        runName,
        dataset,
        recordedAt: now,
      });
      summary.edgesAdded += 1;
    }

    // 3e. Weak-bottleneck conflict edge (v2.1).
    if (
      weakConfidenceThreshold > 0 &&
      confidence !== null &&
      confidence < weakConfidenceThreshold &&
      info.bottleneckPair
    ) {
      const [ba, bb] = info.bottleneckPair;
      const ra = rowIdToRecId.get(ba);
      const rb = rowIdToRecId.get(bb);
      const bScore = info.pairScores.get(pairKey(ba, bb)) ?? null;
      if (ra && rb) {
        await store.addEdge({
          edgeId: null,
          entityId,
          recordAId: ra,
          recordBId: rb,
          kind: "conflicts_with",
          score: bScore,
          matchkeyName,
          fieldScores: null,
          negativeEvidence: {
            reason: "weak_cluster_bottleneck",
            cluster_confidence: confidence,
            threshold: weakConfidenceThreshold,
          },
          controllerSnapshot: null,
          runName,
          dataset,
          recordedAt: now,
        });
        summary.conflictsFlagged += 1;
      }
    }
  }

  return summary;
}
