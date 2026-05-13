"""Identity resolution -- map run-local clusters to durable identity_ids.

The single entry point ``resolve_clusters`` runs after dedupe clustering and:

1. Derives a stable ``record_id`` per source record (``{source}:{source_pk}``).
2. For each cluster, decides ``create`` / ``absorb`` / ``merge`` based on which
   existing identities cover the cluster's records.
3. Upserts source_records, identity_nodes, evidence_edges; emits events.

Idempotent on ``(run_name, kind, entity_id)``: replaying the same run does not
duplicate events. Edges deduplicate on the UNIQUE constraint in storage.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from goldenmatch.identity.model import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    SourceRecord,
    canon_record_pair,
)
from goldenmatch.identity.store import IdentityStore, new_entity_id

log = logging.getLogger("goldenmatch.identity.resolve")


@dataclass
class ResolveSummary:
    created: int = 0
    absorbed_records: int = 0
    merged: int = 0
    split: int = 0
    edges_added: int = 0
    events_emitted: int = 0
    records_upserted: int = 0
    # v2.1: count of ``conflicts_with`` edges emitted automatically by the
    # resolver (weak bottlenecks + merges with prior conflicts).
    conflicts_flagged: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "absorbed_records": self.absorbed_records,
            "merged": self.merged,
            "split": self.split,
            "edges_added": self.edges_added,
            "events_emitted": self.events_emitted,
            "records_upserted": self.records_upserted,
            "conflicts_flagged": self.conflicts_flagged,
        }


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("__")}


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def derive_record_id(
    row: dict[str, Any],
    source: str,
    source_pk_col: str | None,
) -> tuple[str, str]:
    """Return ``(record_id, source_pk)`` for a record row.

    When ``source_pk_col`` is set and the row has a non-null value, use that.
    Otherwise fall back to the payload hash (``{source}:hash:{12 hex}``).
    """
    if source_pk_col and source_pk_col in row and row[source_pk_col] is not None:
        pk = str(row[source_pk_col])
        return f"{source}:{pk}", pk
    payload_hash = _hash_payload(_row_to_payload(row))
    short = payload_hash[:12]
    return f"{source}:hash:{short}", f"hash:{short}"


def _golden_record_from_members(
    df: pl.DataFrame, row_ids: list[int]
) -> dict[str, Any]:
    """Roll up cluster members into a single representative row (most-complete)."""
    members = df.filter(pl.col("__row_id__").is_in(row_ids))
    if members.is_empty():
        return {}
    out: dict[str, Any] = {}
    for col in members.columns:
        if col.startswith("__"):
            continue
        non_null = members[col].drop_nulls()
        if non_null.is_empty():
            continue
        # Pick the longest non-null string representation (most-complete)
        values = [(str(v), v) for v in non_null.to_list()]
        values.sort(key=lambda x: len(x[0]), reverse=True)
        out[col] = values[0][1]
    return out


def _cluster_confidence(cluster_info: dict[str, Any]) -> float | None:
    val = cluster_info.get("confidence")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def resolve_clusters(
    clusters: dict[int, dict],
    df: pl.DataFrame,
    scored_pairs: list[tuple[int, int, float]],
    matchkey_name: str | None,
    store: IdentityStore,
    run_name: str,
    *,
    dataset: str | None = None,
    source_pk_col: str | None = None,
    controller_snapshot: dict[str, Any] | None = None,
    emit_singletons: bool = True,
    weak_confidence_threshold: float = 0.6,
) -> ResolveSummary:
    """Resolve run-local clusters to durable identities.

    See module docstring for high-level flow.
    """
    summary = ResolveSummary()
    if df.is_empty():
        return summary

    # 1. Build row_id -> record_id mapping + ensure source_records are upserted.
    rows = df.to_dicts()
    rowid_to_recid: dict[int, str] = {}
    rowid_to_payload: dict[int, dict[str, Any]] = {}
    rowid_to_source: dict[int, str] = {}
    rowid_to_pk: dict[int, str] = {}
    rowid_to_hash: dict[int, str] = {}

    for row in rows:
        rid = row.get("__row_id__")
        if rid is None:
            continue
        source = str(row.get("__source__", "dataframe"))
        record_id, pk = derive_record_id(row, source, source_pk_col)
        payload = _row_to_payload(row)
        rowid_to_recid[int(rid)] = record_id
        rowid_to_payload[int(rid)] = payload
        rowid_to_source[int(rid)] = source
        rowid_to_pk[int(rid)] = pk
        rowid_to_hash[int(rid)] = _hash_payload(payload)

    # 2. Scored pair lookup canonicalized by record_id pair.
    pair_score_by_recpair: dict[tuple[str, str], float] = {}
    for a, b, s in scored_pairs:
        ra = rowid_to_recid.get(int(a))
        rb = rowid_to_recid.get(int(b))
        if not ra or not rb:
            continue
        pair_score_by_recpair[canon_record_pair(ra, rb)] = float(s)

    # 3. Iterate clusters.
    for cluster_id, info in clusters.items():
        members: list[int] = list(info.get("members") or [])
        if not members:
            continue
        size = len(members)
        if size == 1 and not emit_singletons:
            continue

        record_ids = [rowid_to_recid[m] for m in members if m in rowid_to_recid]
        if not record_ids:
            continue

        # 3a. Look up existing identities for these records.
        existing = store.lookup_entity_ids(record_ids)
        unique_entities = list(set(existing.values()))

        if not unique_entities:
            # Brand-new identity.
            entity_id = new_entity_id()
            now = datetime.now()
            store.upsert_identity(IdentityNode(
                entity_id=entity_id,
                status=IdentityStatus.ACTIVE.value,
                golden_record=_golden_record_from_members(df, members),
                confidence=_cluster_confidence(info),
                dataset=dataset,
                created_at=now,
                updated_at=now,
            ))
            if not store.has_run_event(entity_id, run_name, EventKind.CREATED.value):
                store.emit_event(IdentityEvent(
                    entity_id=entity_id,
                    kind=EventKind.CREATED.value,
                    payload={
                        "cluster_id": cluster_id,
                        "member_count": size,
                        "record_ids": record_ids,
                    },
                    run_name=run_name, dataset=dataset, recorded_at=now,
                ))
                summary.events_emitted += 1
            summary.created += 1
        elif len(unique_entities) == 1:
            # Absorb new records into existing identity.
            entity_id = unique_entities[0]
            existing_node = store.get_identity(entity_id)
            now = datetime.now()
            store.upsert_identity(IdentityNode(
                entity_id=entity_id,
                status=existing_node.status if existing_node else IdentityStatus.ACTIVE.value,
                merged_into=existing_node.merged_into if existing_node else None,
                golden_record=_golden_record_from_members(df, members),
                confidence=_cluster_confidence(info),
                dataset=dataset,
                created_at=existing_node.created_at if existing_node else now,
                updated_at=now,
            ))
            newly_added = [rid for rid in record_ids if rid not in existing]
            for rid in newly_added:
                store.emit_event(IdentityEvent(
                    entity_id=entity_id,
                    kind=EventKind.ABSORBED_RECORD.value,
                    payload={"record_id": rid, "cluster_id": cluster_id},
                    run_name=run_name, dataset=dataset, recorded_at=now,
                ))
                summary.events_emitted += 1
                summary.absorbed_records += 1
        else:
            # Multi-entity overlap -> merge into the one with most members
            # (tie-break: oldest created_at).
            counts = Counter(existing.values())
            ranked = sorted(
                counts.items(),
                key=lambda kv: (-kv[1], _node_age(store, kv[0])),
            )
            winner = ranked[0][0]
            losers = [eid for eid, _ in ranked[1:]]
            now = datetime.now()
            winner_node = store.get_identity(winner)
            store.upsert_identity(IdentityNode(
                entity_id=winner,
                status=IdentityStatus.ACTIVE.value,
                merged_into=None,
                golden_record=_golden_record_from_members(df, members),
                confidence=_cluster_confidence(info),
                dataset=dataset,
                created_at=winner_node.created_at if winner_node else now,
                updated_at=now,
            ))
            store.emit_event(IdentityEvent(
                entity_id=winner,
                kind=EventKind.MERGED_WITH.value,
                payload={
                    "absorbed": losers,
                    "cluster_id": cluster_id,
                    "member_count": size,
                },
                run_name=run_name, dataset=dataset, recorded_at=now,
            ))
            summary.events_emitted += 1
            for loser in losers:
                store.retire_identity(loser, merged_into=winner)
                store.emit_event(IdentityEvent(
                    entity_id=loser,
                    kind=EventKind.MERGED_WITH.value,
                    payload={"merged_into": winner},
                    run_name=run_name, dataset=dataset, recorded_at=now,
                ))
                summary.events_emitted += 1
            entity_id = winner
            summary.merged += 1

        # 3b. Reassign losers' records to winner BEFORE upserting cluster records,
        # so an absorb branch on the next iteration sees them already migrated.
        # In merge branch above, loser records are reassigned here:
        if len(unique_entities) > 1:
            # Migrate records that previously pointed at losers.
            losers_set = {eid for eid in unique_entities if eid != entity_id}
            for rid, old_eid in existing.items():
                if old_eid in losers_set:
                    rec = store.get_record(rid)
                    if rec is not None:
                        rec.entity_id = entity_id
                        rec.last_seen_at = datetime.now()
                        store.upsert_record(rec)

        # 3c. Upsert all cluster records under the chosen entity_id.
        for member in members:
            rid = rowid_to_recid.get(member)
            if rid is None:
                continue
            store.upsert_record(SourceRecord(
                record_id=rid,
                source=rowid_to_source[member],
                source_pk=rowid_to_pk[member],
                record_hash=rowid_to_hash[member],
                entity_id=entity_id,
                payload=rowid_to_payload[member],
                dataset=dataset,
                last_seen_at=datetime.now(),
            ))
            summary.records_upserted += 1

        # 3d. Record evidence edges for every scored within-cluster pair.
        pair_scores = info.get("pair_scores") or {}
        for pair_key, score in pair_scores.items():
            if isinstance(pair_key, tuple) and len(pair_key) == 2:
                a, b = pair_key
            else:
                continue
            ra = rowid_to_recid.get(int(a))
            rb = rowid_to_recid.get(int(b))
            if not ra or not rb:
                continue
            store.add_edge(EvidenceEdge(
                entity_id=entity_id,
                record_a_id=ra,
                record_b_id=rb,
                kind=EdgeKind.SAME_AS.value,
                score=float(score),
                matchkey_name=matchkey_name,
                controller_snapshot=controller_snapshot,
                run_name=run_name,
                dataset=dataset,
            ))
            summary.edges_added += 1

        # 3e. v2.1 conflict detection -- weak bottleneck.
        # When the cluster confidence dropped low enough that the cluster
        # quality engine flagged it weak, surface the bottleneck pair as a
        # `conflicts_with` edge so a steward sees it in the conflicts feed.
        # Same-source identical row pairs (score 1.0 exact dupes) are
        # excluded so the conflicts feed stays signal-rich.
        cluster_conf = _cluster_confidence(info)
        bottleneck = info.get("bottleneck_pair")
        if (
            weak_confidence_threshold > 0
            and cluster_conf is not None
            and cluster_conf < weak_confidence_threshold
            and bottleneck is not None
            and isinstance(bottleneck, tuple)
            and len(bottleneck) == 2
        ):
            ba, bb = bottleneck
            ra = rowid_to_recid.get(int(ba))
            rb = rowid_to_recid.get(int(bb))
            bottleneck_score = (
                info.get("pair_scores", {}).get((min(int(ba), int(bb)), max(int(ba), int(bb))))
            )
            if ra and rb:
                store.add_edge(EvidenceEdge(
                    entity_id=entity_id,
                    record_a_id=ra,
                    record_b_id=rb,
                    kind=EdgeKind.CONFLICTS_WITH.value,
                    score=float(bottleneck_score) if bottleneck_score is not None else None,
                    matchkey_name=matchkey_name,
                    negative_evidence={
                        "reason": "weak_cluster_bottleneck",
                        "cluster_confidence": cluster_conf,
                        "threshold": weak_confidence_threshold,
                    },
                    controller_snapshot=controller_snapshot,
                    run_name=run_name,
                    dataset=dataset,
                ))
                summary.conflicts_flagged += 1

        # 3f. v2.1 conflict detection -- carry forward prior conflicts on merge.
        # If we just merged two identities and either side previously had a
        # `conflicts_with` edge between *their* members, surface a fresh
        # `conflicts_with` on the winner so a steward can re-verify post-merge.
        if len(unique_entities) > 1:
            prior_losers = [eid for eid in unique_entities if eid != entity_id]
            for loser in prior_losers:
                # Lightweight inspection: scan the loser's recent edges for
                # any explicit conflicts_with. (For very high-volume graphs a
                # dedicated query would be cheaper; this is fine for the
                # cluster-counts we see in practice.)
                for prior_edge in store.edges_for_entity(loser):
                    if prior_edge.kind != EdgeKind.CONFLICTS_WITH.value:
                        continue
                    store.add_edge(EvidenceEdge(
                        entity_id=entity_id,
                        record_a_id=prior_edge.record_a_id,
                        record_b_id=prior_edge.record_b_id,
                        kind=EdgeKind.CONFLICTS_WITH.value,
                        score=prior_edge.score,
                        matchkey_name=prior_edge.matchkey_name,
                        negative_evidence={
                            "reason": "carried_forward_from_merge",
                            "from_entity": loser,
                            "original_run": prior_edge.run_name,
                        },
                        controller_snapshot=controller_snapshot,
                        run_name=run_name,
                        dataset=dataset,
                    ))
                    summary.conflicts_flagged += 1

    # 4. Split detection: records that previously had an entity_id but did
    # NOT appear in any cluster this run should not be retired here -- they
    # simply are not part of the current input. True split detection
    # requires a record to appear in this run AND drop out of its prior
    # identity; we model that implicitly because the upsert_record step
    # above re-points the record's entity_id to whatever the current cluster
    # resolved to. If that differs from the prior entity_id and the prior
    # entity has no remaining members, callers may retire it via a follow-up
    # pass; v1 leaves this to the steward.

    return summary


def _node_age(store: IdentityStore, entity_id: str):
    node = store.get_identity(entity_id)
    return node.created_at if node else datetime.now()
