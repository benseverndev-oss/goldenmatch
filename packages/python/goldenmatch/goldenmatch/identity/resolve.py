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
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from goldenmatch._polars_lazy import pl

if TYPE_CHECKING:
    from goldenmatch.config.schemas import MatchkeyConfig
    from goldenmatch.core.cluster import ClusterFrames
    from goldenmatch.core.cluster_pairscores import ClusterPairScores

from goldenmatch.core._hashing import record_fingerprint
from goldenmatch.identity.fingerprint_batch import (
    _canonical_payload,
    batch_fingerprints,
)
from goldenmatch.identity.model import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    SourceRecord,
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


# Sentinel: ``_record_id_candidates`` was called WITHOUT a precomputed batch
# hash, so it must compute the fingerprint per-row (the legacy default path).
# Distinct from ``None``, which means the batch *determined* this row is
# un-fingerprintable (-> legacy-only id).
_NOT_BATCHED = object()


def _batch_fingerprint_enabled() -> bool:
    """Batch fingerprinting for ``resolve_clusters``: no-PK record h1 hashes are
    computed once via ``batch_fingerprints(df)`` (Arrow kernel) instead of per-row
    inside the cluster loop. Default ON -- byte-identical to the per-row path
    (verified across the full identity suite + the real Arrow path in CI), so it
    is a no-op on OUTPUT, only faster. Bench (run 26793348836, fresh native, 1M/5M
    no-PK): 2.6x on the fingerprinting step (5.54s->2.13s @1M, 27.5s->10.5s @5M).
    Kill-switch ``GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT=0`` restores the per-row
    path."""
    return os.environ.get(
        "GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT", "1"
    ).strip() != "0"


def _record_id_candidates(
    row: dict[str, Any],
    source: str,
    source_pk_col: str | None,
    *,
    precomputed_h1: object = _NOT_BATCHED,
) -> tuple[str, list[str]]:
    """Return ``(primary_id, lookup_candidates)`` for a record row.

    Natural PK -> ``("{source}:{pk}", ["{source}:{pk}"])`` (unchanged; never
    touches ``precomputed_h1``).
    No PK -> a content-hash id. The primary and sole lookup candidate is the
    canonical cross-surface fingerprint (``"{source}:h1:{12}"``). A record
    whose values the canonical spec can't yet fingerprint stays on the legacy
    ``"{source}:hash:{12}"`` scheme (the sole candidate in that case too).

    ``precomputed_h1`` (keyword-only) lets a caller supply the FULL 64-char h1
    hash computed in bulk via ``batch_fingerprints`` (the
    ``GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT`` path), skipping the per-row
    ``record_fingerprint`` call. ``_NOT_BATCHED`` (default) -> compute per-row.
    A ``str`` -> use it as the full h1 hash. ``None`` -> the batch determined
    the row un-fingerprintable, take the legacy-only path (byte-identical to
    the per-row ``except`` branch).
    """
    if source_pk_col and source_pk_col in row and row[source_pk_col] is not None:
        pk = str(row[source_pk_col])
        rid = f"{source}:{pk}"
        return rid, [rid]
    payload = _row_to_payload(row)
    legacy_id = f"{source}:hash:{_hash_payload(payload)[:12]}"
    if precomputed_h1 is _NOT_BATCHED:
        try:
            full_h1 = record_fingerprint(_canonical_payload(payload))
        except (TypeError, ValueError):
            # Belt-and-suspenders: anything the canonical spec can't handle
            # stays legacy-only (still fully resolvable). Rare after coercion.
            return legacy_id, [legacy_id]
    elif precomputed_h1 is None:
        # Batch flagged this row un-fingerprintable -> legacy-only (matches the
        # per-row except branch byte-for-byte).
        return legacy_id, [legacy_id]
    else:
        full_h1 = precomputed_h1  # type: ignore[assignment]
    h1_id = f"{source}:h1:{full_h1[:12]}"
    return h1_id, [h1_id]


def derive_record_id(
    row: dict[str, Any],
    source: str,
    source_pk_col: str | None,
) -> tuple[str, str]:
    """Return ``(record_id, source_pk)`` for a record row, using the primary
    id scheme (see ``_record_id_candidates``). Kept for back-compat."""
    primary, _ = _record_id_candidates(row, source, source_pk_col)
    pk = primary[len(source) + 1:] if primary.startswith(f"{source}:") else primary
    return primary, pk


def _golden_record_from_members(
    df, row_ids: list[int]
) -> dict[str, Any]:
    """Roll up cluster members into a single representative row (most-complete).

    A5: seam-driven both lanes (column reads + Python folds).
    """
    from goldenmatch.core.frame import to_frame

    members = to_frame(df).filter_in("__row_id__", row_ids)
    if members.height == 0:
        return {}
    out: dict[str, Any] = {}
    for col in members.columns:
        if col.startswith("__"):
            continue
        non_null = [v for v in members.column(col).to_list() if v is not None]
        if not non_null:
            continue
        # Pick the longest non-null string representation (most-complete)
        values = [(str(v), v) for v in non_null]
        values.sort(key=lambda x: len(x[0]), reverse=True)
        out[col] = values[0][1]
    return out


def _golden_record_from_payloads(
    payload_by_row_id: dict[int, dict[str, Any]], row_ids: list[int]
) -> dict[str, Any]:
    """Roll up pre-indexed member payloads without re-scanning the frame."""
    members = [payload_by_row_id[row_id] for row_id in row_ids if row_id in payload_by_row_id]
    if not members:
        return {}
    out: dict[str, Any] = {}
    for col in members[0]:
        non_null = [member[col] for member in members if member.get(col) is not None]
        if not non_null:
            continue
        values = [(str(value), value) for value in non_null]
        values.sort(key=lambda item: len(item[0]), reverse=True)
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


def _frames_iter(
    cluster_frames: ClusterFrames,
) -> list[tuple[int, dict[str, Any]]]:
    """Yield ``(cluster_id, info)`` pairs from a ``ClusterFrames`` in
    ASCENDING cluster_id order, where ``info`` carries ONLY the keys the
    resolution body reads: ``members``, ``confidence``, ``bottleneck_pair``.

    Ascending cluster_id reproduces the dict path's insertion order (build
    emits 1..N, splits append higher cids), so brand-new clusters mint entity
    ids in the identical order. ``bottleneck_pair`` maps ``(a, b) == (0, 0)``
    to ``None`` EXACTLY as ``cluster_frames_to_dict`` does (``cluster.py``) --
    a raw ``(0, 0)`` 2-tuple would wrongly trip the weak-conflict guard.
    """
    from goldenmatch.core.frame import to_frame as _tf_i

    assignments = _tf_i(cluster_frames.assignments)
    metadata = _tf_i(cluster_frames.metadata)

    # members per cid (assignments is one row per (cluster_id, member_id)).
    # Member ORDER within a cid is intentionally unconstrained: the resolution
    # body compares members as a set (PR #598). The ascending-cid ``rows.sort``
    # below IS load-bearing (mint order) and must stay.
    members_by_cid: dict[int, list[int]] = {}
    if assignments.height > 0:
        for cid, mid in zip(
            assignments.column("cluster_id").to_list(),
            assignments.column("member_id").to_list(),
            strict=True,
        ):
            members_by_cid.setdefault(int(cid), []).append(int(mid))

    rows: list[tuple[int, dict[str, Any]]] = []
    _m = {
        name: metadata.column(name).to_list()
        for name in ("cluster_id", "confidence", "bottleneck_pair_a", "bottleneck_pair_b")
    }
    for cid_v, conf_v, bot_a, bot_b in zip(
        _m["cluster_id"], _m["confidence"],
        _m["bottleneck_pair_a"], _m["bottleneck_pair_b"], strict=True,
    ):
        cid = int(cid_v)
        bot = (int(bot_a), int(bot_b))
        rows.append((
            cid,
            {
                "members": members_by_cid.get(cid, []),
                "confidence": float(conf_v),
                "bottleneck_pair": bot if bot != (0, 0) else None,
            },
        ))
    rows.sort(key=lambda kv: kv[0])
    return rows


def _referenced_row_ids(cluster_items: Any, emit_singletons: bool) -> set[int]:
    """Row ids the resolution body can actually read.

    Everything downstream of the prep -- record ids, the payload rollup, the
    evidence-edge endpoints, the bottleneck pair -- is reached through a
    cluster's ``members``. Pair-score endpoints are guaranteed to be members
    too: ``ClusterPairScores`` only keeps a pair when BOTH endpoints map to the
    same cluster, and the legacy dict path stores within-cluster pairs. So a row
    no surviving cluster lists is never looked up, and prepping it is pure cost.

    Mirrors the loop's own skip rules (empty members, and singletons when
    ``emit_singletons`` is False) so the bound can never drop a row the loop
    would have used.
    """
    needed: set[int] = set()
    for _cluster_id, info in cluster_items:
        members = info.get("members") or []
        if not members:
            continue
        if len(members) == 1 and not emit_singletons:
            continue
        for member in members:
            needed.add(int(member))
    return needed


def resolve_clusters(
    clusters: dict[int, dict] | None = None,
    df: Any = None,  # pl.DataFrame | pa.Table (A5: dual-rep dedupe path)
    scored_pairs: list[tuple[int, int, float]] | None = None,
    matchkey_name: str | None = None,
    store: IdentityStore | None = None,
    run_name: str = "",
    *,
    dataset: str | None = None,
    source_pk_col: str | None = None,
    controller_snapshot: dict[str, Any] | None = None,
    emit_singletons: bool = True,
    weak_confidence_threshold: float = 0.6,
    pair_score_view: ClusterPairScores | None = None,
    cluster_frames: ClusterFrames | None = None,
    actor: str = "pipeline",
) -> ResolveSummary:
    """Resolve run-local clusters to durable identities.

    The cluster partition is supplied EITHER as the legacy ``clusters``
    ``dict[int, dict]`` OR as the SP-A ``cluster_frames`` two-frame
    ``ClusterFrames`` (exactly one). The frames path lets the pipeline stop
    rebuilding the dict; it iterates the same ``(cluster_id, info)`` pairs in
    ascending-cluster_id order (= the dict's insertion order) and runs the
    UNCHANGED resolution body. When ``cluster_frames`` is given,
    ``pair_score_view`` MUST be supplied -- the frames carry no per-cluster
    ``pair_scores``, so without the view every evidence edge would be empty.

    See module docstring for high-level flow.
    """
    if (clusters is None) == (cluster_frames is None):
        raise ValueError(
            "resolve_clusters requires exactly one of `clusters` / "
            "`cluster_frames`"
        )
    if cluster_frames is not None and pair_score_view is None:
        raise ValueError(
            "resolve_clusters(cluster_frames=...) requires `pair_score_view` "
            "(the frames carry no per-cluster pair_scores; evidence edges "
            "would be empty otherwise)"
        )
    if df is None or store is None:
        raise ValueError("resolve_clusters requires `df` and `store`")
    if scored_pairs is None:
        scored_pairs = []

    summary = ResolveSummary()
    from goldenmatch.core.frame import to_frame as _tf_a5e

    if _tf_a5e(df).height == 0:
        return summary

    # Iteration source: ascending-cluster_id ``(cluster_id, info)`` pairs.
    # For the dict path this is ``clusters.items()`` (insertion order = build's
    # ascending 1..N, splits appended higher). For the frames path we rebuild
    # the same ascending-cid stream from the two frames (see ``_frames_iter``)
    # so brand-new clusters mint entity ids in the identical order.
    cluster_items: Any = (
        clusters.items()
        if clusters is not None
        else _frames_iter(cluster_frames)  # type: ignore[arg-type]
    )

    # 1. Build row_id -> record_id mapping + ensure source_records are upserted.
    #
    # rows without a natural source PK get a content-hash id: the canonical
    # cross-surface fingerprint ("{source}:h1:{12}"). Records whose values the
    # canonical spec can't fingerprint fall back to the legacy json.dumps id
    # ("{source}:hash:{12}"). Run `goldenmatch identity migrate-ids` to rewrite
    # any pre-v2 ":hash:" ids in the store to their ":h1:" equivalents.
    from goldenmatch.core.frame import to_frame as _tf_a5

    _fa5 = _tf_a5(df)

    # Bound the prep to rows a surviving cluster actually references (#2105).
    # The block below turns every row it sees into ~2.5 KB of Python heap (row
    # dict + payload dict + hash + source + pk + record-id candidates). Run over
    # a whole 14M-row frame that is ~35 GB on top of the pipeline's own resident
    # set -- enough to OOM a 64 GB box -- and with ``emit_singletons=False`` the
    # overwhelming majority of those rows are never read again (the reported run
    # had 107,723 records across 1M rows, ~11%). Filtering first makes the prep
    # scale with the identity graph rather than with the input frame.
    # ``emit_singletons=True`` legitimately needs every row and degenerates to
    # the old behaviour.
    if "__row_id__" in _fa5.columns:
        _needed = _referenced_row_ids(cluster_items, emit_singletons)
        if len(_needed) < _fa5.height:
            _fa5 = _fa5.filter_in("__row_id__", sorted(_needed))
    rows = _fa5.select_dicts(list(_fa5.columns))
    rowid_to_recid: dict[int, str] = {}
    rowid_to_payload: dict[int, dict[str, Any]] = {}
    rowid_to_source: dict[int, str] = {}
    rowid_to_pk: dict[int, str] = {}
    rowid_to_hash: dict[int, str] = {}
    _rowid_primary: dict[int, str] = {}
    _rowid_candidates: dict[int, list[str]] = {}

    # Optional batch fingerprinting (GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT=1):
    # compute every no-PK row's h1 hash once via the Arrow batch kernel instead
    # of per-row inside the loop. ``batch_fingerprints`` returns FULL 64-char
    # hashes positionally aligned to its input's rows (None = un-fingerprintable),
    # so it MUST run on the SAME frame ``rows`` came from -- i.e. the bounded
    # ``_fa5``, not the caller's ``df`` -- to stay aligned with
    # ``enumerate(rows)``. Each row's hash covers only that row's own values, so
    # restricting the frame changes which hashes are computed, never their
    # values. Only NO-PK rows get a precomputed hash -- the PK-detection here
    # mirrors ``_record_id_candidates`` exactly, so a PK row never receives one
    # (it would be ignored anyway) and a no-PK row always does. Gate off ->
    # ``h1_by_rowid`` empty -> every row falls to _NOT_BATCHED -> byte-identical
    # to the per-row path.
    h1_by_rowid: dict[int, str | None] = {}
    if _batch_fingerprint_enabled() and rows:
        h1_list = batch_fingerprints(_fa5.native)
        for i, row in enumerate(rows):
            rid = row.get("__row_id__")
            if rid is None:
                continue
            has_pk = (
                source_pk_col is not None
                and source_pk_col in row
                and row[source_pk_col] is not None
            )
            if not has_pk:
                h1_by_rowid[int(rid)] = h1_list[i]

    for row in rows:
        rid = row.get("__row_id__")
        if rid is None:
            continue
        irid = int(rid)
        source = str(row.get("__source__", "dataframe"))
        primary_id, candidates = _record_id_candidates(
            row, source, source_pk_col,
            precomputed_h1=h1_by_rowid.get(irid, _NOT_BATCHED),
        )
        _rowid_primary[irid] = primary_id
        _rowid_candidates[irid] = candidates
        rowid_to_payload[irid] = _row_to_payload(row)
        rowid_to_source[irid] = source
        rowid_to_hash[irid] = _hash_payload(rowid_to_payload[irid])

    # One bulk lookup over the candidate union resolves each record to an
    # existing id (legacy-fallback) and doubles as the pre-flight check the
    # bulk fast-path below uses to spot brand-new clusters (the 500K-cluster
    # bench, #368 Phase 6, depends on this single pre-flight lookup).
    _all_candidates = sorted({c for cs in _rowid_candidates.values() for c in cs})
    _existing_by_id: dict[str, str] = (
        store.lookup_entity_ids(_all_candidates) if _all_candidates else {}
    )
    preflight_existing: dict[str, str] = {}
    for irid, candidates in _rowid_candidates.items():
        chosen = next(
            (c for c in candidates if c in _existing_by_id), _rowid_primary[irid]
        )
        rowid_to_recid[irid] = chosen
        src = rowid_to_source[irid]
        rowid_to_pk[irid] = (
            chosen[len(src) + 1:] if chosen.startswith(f"{src}:") else chosen
        )
        if chosen in _existing_by_id:
            preflight_existing[chosen] = _existing_by_id[chosen]

    # 2. ``scored_pairs`` is accepted for call-signature compatibility but is
    # NOT read: evidence edges come from the per-cluster ``pair_scores`` /
    # ``pair_score_view``, which the cluster build already restricted to
    # within-cluster pairs. It used to be folded into a
    # ``{(record_a, record_b): score}`` dict that nothing ever looked up --
    # ~1.2 s and ~102 bytes per million pairs, over the FULL scored set, on
    # every resolve (#2105). The pipeline passes the complete pre-cluster pair
    # stream here, which on wide-block data is far larger than the edge set, so
    # dropping the dict removes a cost that tracked candidate-pair growth rather
    # than identity count.

    # Bulk-path eligibility: postgres-only, no overlap with existing
    # identities, no weak-conflict edges (the conflict path mutates per
    # cluster and is rare anyway). Anything ineligible falls through to
    # the slow per-row loop, which preserves correctness for the
    # absorb / merge / conflict-detection branches.
    use_bulk_fast_path = getattr(store, "_backend", None) == "postgres"
    bulk_node_rows: list[dict[str, Any]] = []
    bulk_record_rows: list[dict[str, Any]] = []
    bulk_edge_rows: list[dict[str, Any]] = []
    bulk_event_rows: list[dict[str, Any]] = []
    bulk_cluster_ids: set = set()

    # Pre-flight the existing identity NODES in one batched read (#1912) so the
    # per-record absorb / merge branches read status/created_at from a dict
    # instead of a per-cluster ``get_identity`` SELECT -- which, under the
    # ``write_pipeline`` below, would force a pipeline sync per cluster and undo
    # the batching. Only entities the input records already point at are needed.
    preflight_nodes: dict[str, Any] = (
        store.get_identities(set(preflight_existing.values()))
        if preflight_existing else {}
    )

    # Fire-and-forget event/edge writes: the resolve path ignores the generated
    # id, and skipping the read-back is what lets ``write_pipeline`` actually
    # batch (an id read-back would sync the pipeline on every call). The store
    # methods are otherwise unchanged.
    def _emit(ev: IdentityEvent) -> None:
        store.emit_event(ev, return_id=False)

    def _add_edge(e: EvidenceEdge) -> None:
        store.add_edge(e, return_id=False)

    with store.bulk_writes():
        with store.write_pipeline():
            # 3. Iterate clusters.
            for cluster_id, info in cluster_items:
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
                # Use the pre-flight dict instead of per-cluster SELECT.
                existing = {
                    rid: preflight_existing[rid]
                    for rid in record_ids if rid in preflight_existing
                }
                unique_entities = list(set(existing.values()))

                # 3a'. Bulk fast-path: brand-new cluster on postgres, no weak
                # conflict edge -> accumulate rows for bulk flush, skip the
                # per-row writes for this cluster.
                cluster_conf_check = _cluster_confidence(info)
                is_weak = (
                    weak_confidence_threshold > 0
                    and cluster_conf_check is not None
                    and cluster_conf_check < weak_confidence_threshold
                    and info.get("bottleneck_pair") is not None
                )
                if (
                    use_bulk_fast_path
                    and not unique_entities
                    and not is_weak
                ):
                    entity_id = new_entity_id()
                    now = datetime.now()
                    golden = _golden_record_from_payloads(rowid_to_payload, members)
                    bulk_node_rows.append({
                        "entity_id": entity_id,
                        "status": IdentityStatus.ACTIVE.value,
                        "merged_into": None,
                        "golden_record": json.dumps(golden, default=str) if golden else None,
                        "confidence": cluster_conf_check,
                        "dataset": dataset,
                        "created_at": now,
                        "updated_at": now,
                    })
                    for member in members:
                        rid = rowid_to_recid.get(member)
                        if rid is None:
                            continue
                        bulk_record_rows.append({
                            "record_id": rid,
                            "source": rowid_to_source[member],
                            "source_pk": rowid_to_pk[member],
                            "record_hash": rowid_to_hash[member],
                            "entity_id": entity_id,
                            "dataset": dataset,
                            "first_seen_at": now,
                            "last_seen_at": now,
                        })
                        summary.records_upserted += 1
                    pair_scores = (
                        pair_score_view.for_cluster(cluster_id)
                        if pair_score_view is not None
                        else (info.get("pair_scores") or {})
                    )
                    for pair_key, score in pair_scores.items():
                        if isinstance(pair_key, tuple) and len(pair_key) == 2:
                            a, b = pair_key
                        else:
                            continue
                        ra = rowid_to_recid.get(int(a))
                        rb = rowid_to_recid.get(int(b))
                        if not ra or not rb:
                            continue
                        bulk_edge_rows.append({
                            "entity_id": entity_id,
                            "record_a_id": ra,
                            "record_b_id": rb,
                            "kind": EdgeKind.SAME_AS.value,
                            "score": float(score),
                            "matchkey_name": matchkey_name,
                            "run_name": run_name,
                            "dataset": dataset,
                            "recorded_at": now,
                        })
                        summary.edges_added += 1
                    bulk_event_rows.append({
                        "entity_id": entity_id,
                        "kind": EventKind.CREATED.value,
                        "run_name": run_name,
                        "dataset": dataset,
                        "recorded_at": now,
                    })
                    summary.events_emitted += 1
                    summary.created += 1
                    bulk_cluster_ids.add(cluster_id)
                    continue


                if not unique_entities:
                    # Brand-new identity.
                    entity_id = new_entity_id()
                    now = datetime.now()
                    store.upsert_identity(IdentityNode(
                        entity_id=entity_id,
                        status=IdentityStatus.ACTIVE.value,
                        golden_record=_golden_record_from_payloads(rowid_to_payload, members),
                        confidence=_cluster_confidence(info),
                        dataset=dataset,
                        created_at=now,
                        updated_at=now,
                    ))
                    if not store.has_run_event(entity_id, run_name, EventKind.CREATED.value):
                        _emit(IdentityEvent(
                            entity_id=entity_id,
                            kind=EventKind.CREATED.value,
                            payload={
                                "cluster_id": cluster_id,
                                "member_count": size,
                                "record_ids": record_ids,
                            },
                            run_name=run_name, dataset=dataset, recorded_at=now,
                            actor=actor, trust=_cluster_confidence(info),
                        ))
                        summary.events_emitted += 1
                    summary.created += 1
                elif len(unique_entities) == 1:
                    # Absorb new records into existing identity.
                    entity_id = unique_entities[0]
                    existing_node = preflight_nodes.get(entity_id)
                    now = datetime.now()
                    store.upsert_identity(IdentityNode(
                        entity_id=entity_id,
                        status=existing_node.status if existing_node else IdentityStatus.ACTIVE.value,
                        merged_into=existing_node.merged_into if existing_node else None,
                        golden_record=_golden_record_from_payloads(rowid_to_payload, members),
                        confidence=_cluster_confidence(info),
                        dataset=dataset,
                        created_at=existing_node.created_at if existing_node else now,
                        updated_at=now,
                    ))
                    newly_added = [rid for rid in record_ids if rid not in existing]
                    for rid in newly_added:
                        _emit(IdentityEvent(
                            entity_id=entity_id,
                            kind=EventKind.ABSORBED_RECORD.value,
                            payload={"record_id": rid, "cluster_id": cluster_id},
                            run_name=run_name, dataset=dataset, recorded_at=now,
                            actor=actor, trust=_cluster_confidence(info),
                        ))
                        summary.events_emitted += 1
                        summary.absorbed_records += 1
                else:
                    # Multi-entity overlap -> merge into the one with most members
                    # (tie-break: oldest created_at).
                    counts = Counter(existing.values())
                    ranked = sorted(
                        counts.items(),
                        key=lambda kv: (-kv[1], _node_age(store, kv[0], preflight_nodes)),
                    )
                    winner = ranked[0][0]
                    losers = [eid for eid, _ in ranked[1:]]
                    now = datetime.now()
                    winner_node = preflight_nodes.get(winner)
                    store.upsert_identity(IdentityNode(
                        entity_id=winner,
                        status=IdentityStatus.ACTIVE.value,
                        merged_into=None,
                        golden_record=_golden_record_from_payloads(rowid_to_payload, members),
                        confidence=_cluster_confidence(info),
                        dataset=dataset,
                        created_at=winner_node.created_at if winner_node else now,
                        updated_at=now,
                    ))
                    _emit(IdentityEvent(
                        entity_id=winner,
                        kind=EventKind.MERGED_WITH.value,
                        payload={
                            "absorbed": losers,
                            "cluster_id": cluster_id,
                            "member_count": size,
                        },
                        run_name=run_name, dataset=dataset, recorded_at=now,
                        actor=actor, trust=_cluster_confidence(info),
                    ))
                    summary.events_emitted += 1
                    for loser in losers:
                        store.retire_identity(loser, merged_into=winner)
                        _emit(IdentityEvent(
                            entity_id=loser,
                            kind=EventKind.MERGED_WITH.value,
                            payload={"merged_into": winner},
                            run_name=run_name, dataset=dataset, recorded_at=now,
                            actor=actor, trust=_cluster_confidence(info),
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
                pair_scores = (
                    pair_score_view.for_cluster(cluster_id)
                    if pair_score_view is not None
                    else (info.get("pair_scores") or {})
                )
                for pair_key, score in pair_scores.items():
                    if isinstance(pair_key, tuple) and len(pair_key) == 2:
                        a, b = pair_key
                    else:
                        continue
                    ra = rowid_to_recid.get(int(a))
                    rb = rowid_to_recid.get(int(b))
                    if not ra or not rb:
                        continue
                    _add_edge(EvidenceEdge(
                        entity_id=entity_id,
                        record_a_id=ra,
                        record_b_id=rb,
                        kind=EdgeKind.SAME_AS.value,
                        score=float(score),
                        matchkey_name=matchkey_name,
                        controller_snapshot=controller_snapshot,
                        run_name=run_name,
                        dataset=dataset,
                        actor=actor, trust=float(score),
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
                        pair_score_view.score_for(cluster_id, int(ba), int(bb))
                        if pair_score_view is not None
                        else info.get("pair_scores", {}).get((min(int(ba), int(bb)), max(int(ba), int(bb))))
                    )
                    if ra and rb:
                        _add_edge(EvidenceEdge(
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
                            actor=actor,
                            trust=float(bottleneck_score) if bottleneck_score is not None else None,
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
                            _add_edge(EvidenceEdge(
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
                                actor=actor,
                                trust=prior_edge.score,
                            ))
                            summary.conflicts_flagged += 1

        # 3z. Flush bulk-fast-path accumulators in one COPY each. Order
        # matters: identities first (so the source_records FK is valid),
        # then records, then edges, then events.
        if use_bulk_fast_path and bulk_node_rows:
            from goldenmatch.core.frame import frame_from_rows

            # W4b: seam constructor (datetime_us == bare pl.Datetime). The store
            # COPY contract stays polars-typed until W5.
            nodes_df = frame_from_rows(
                bulk_node_rows,
                {
                    "entity_id": "utf8",
                    "status": "utf8",
                    "merged_into": "utf8",
                    "golden_record": "utf8",
                    "confidence": "float64",
                    "dataset": "utf8",
                    "created_at": "datetime_us",
                    "updated_at": "datetime_us",
                },
                backend="polars",
            ).native
            store.bulk_upsert_identities(nodes_df)
            if bulk_record_rows:
                records_df = frame_from_rows(
                    bulk_record_rows,
                    {
                        "record_id": "utf8",
                        "source": "utf8",
                        "source_pk": "utf8",
                        "record_hash": "utf8",
                        "entity_id": "utf8",
                        "dataset": "utf8",
                        "first_seen_at": "datetime_us",
                        "last_seen_at": "datetime_us",
                    },
                    backend="polars",
                ).native
                store.bulk_upsert_records(records_df)
            if bulk_edge_rows:
                edges_df = frame_from_rows(
                    bulk_edge_rows,
                    {
                        "entity_id": "utf8",
                        "record_a_id": "utf8",
                        "record_b_id": "utf8",
                        "kind": "utf8",
                        "score": "float64",
                        "matchkey_name": "utf8",
                        "run_name": "utf8",
                        "dataset": "utf8",
                        "recorded_at": "datetime_us",
                    },
                    backend="polars",
                ).native
                store.bulk_add_edges(edges_df)
            if bulk_event_rows:
                events_df = frame_from_rows(
                    bulk_event_rows,
                    {
                        "entity_id": "utf8",
                        "kind": "utf8",
                        "run_name": "utf8",
                        "dataset": "utf8",
                        "recorded_at": "datetime_us",
                    },
                    backend="polars",
                ).native
                store.bulk_emit_events(events_df)
            log.info(
                "resolve_clusters bulk fast-path: %d clusters / %d nodes / "
                "%d records / %d edges / %d events flushed in 4 COPY batches",
                len(bulk_cluster_ids), len(bulk_node_rows), len(bulk_record_rows),
                len(bulk_edge_rows), len(bulk_event_rows),
            )

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


def _node_age(
    store: IdentityStore,
    entity_id: str,
    nodes: dict[str, Any] | None = None,
):
    if nodes is not None and entity_id in nodes:
        return nodes[entity_id].created_at
    node = store.get_identity(entity_id)
    return node.created_at if node else datetime.now()


# ── Streaming / micro-batch incremental resolution (#1109) ───────────────────
#
# resolve_clusters runs after a batch dedupe. The two helpers below resolve ONE
# new record at a time -- the streaming primitive -- without re-running the
# pipeline. resolve_record_incremental matches the record against the existing
# frame (match_one) and then DELEGATES the create / absorb / merge decision to
# resolve_clusters over a mini frame (the new record + only its matched rows),
# so edges, golden records, events, idempotency, and multi-entity merge behave
# identically to a batch run -- none of that logic is re-implemented here.


def _match_record_rows(
    record: dict[str, Any],
    df: pl.DataFrame,
    matchkeys: list[MatchkeyConfig],
    *,
    ann_blocker: Any = None,
    embedder: Any = None,
    ann_column: str | None = None,
    top_k: int = 20,
    base_store: Any = None,
) -> dict[int, float]:
    """Best score per existing ``__row_id__`` the record matches, across all
    matchkeys.

    Uses ``match_one`` per matchkey, so it covers the threshold-bearing matchkey
    types (weighted / probabilistic / fuzzy). Exact matchkeys (``threshold is
    None``) contribute nothing -- ``match_one`` returns ``[]`` for them; an
    exact-only incremental path is a follow-up. A failing matchkey is skipped
    (logged), never fatal.
    """
    from goldenmatch.core.match_one import match_one

    best: dict[int, float] = {}
    for mk in matchkeys or []:
        try:
            hits = match_one(
                record, df, mk,
                ann_blocker=ann_blocker, embedder=embedder,
                ann_column=ann_column, top_k=top_k, store=base_store,
            )
        except Exception:
            log.warning(
                "match_one failed for matchkey %r; skipping",
                getattr(mk, "name", "?"),
            )
            continue
        for row_id, score in hits:
            irid = int(row_id)
            if score > best.get(irid, float("-inf")):
                best[irid] = float(score)
    return best


def match_record_to_entity(
    record: dict[str, Any],
    df: pl.DataFrame,
    matchkeys: list[MatchkeyConfig],
    store: IdentityStore,
    *,
    source_pk_col: str | None = None,
    ann_blocker: Any = None,
    embedder: Any = None,
    ann_column: str | None = None,
    top_k: int = 20,
    base_store: Any = None,
) -> dict[str, float]:
    """Return ``{entity_id: best_score}`` for existing identities a single record
    matches -- READ-ONLY (never writes to the store).

    Runs ``match_one`` for each matchkey to find similar existing rows, maps the
    matched rows to their durable ``record_id``s (the SAME scheme
    ``resolve_clusters`` uses), and looks those up in the store. The score per
    entity is the best matching-row score. Returns ``{}`` when nothing matches
    (or no matched row is yet tracked in the identity store).
    """
    matches = _match_record_rows(
        record, df, matchkeys,
        ann_blocker=ann_blocker, embedder=embedder,
        ann_column=ann_column, top_k=top_k, base_store=base_store,
    )
    if not matches:
        return {}
    from goldenmatch.core.frame import to_frame

    _mframe = to_frame(df).filter_in("__row_id__", list(matches.keys()))
    rowid_to_candidates: dict[int, list[str]] = {}
    for row in _mframe.select_dicts(list(_mframe.columns)):
        rid = row.get("__row_id__")
        if rid is None:
            continue
        src = str(row.get("__source__", "dataframe"))
        _, candidates = _record_id_candidates(row, src, source_pk_col)
        rowid_to_candidates[int(rid)] = candidates
    all_candidates = sorted(
        {c for cs in rowid_to_candidates.values() for c in cs}
    )
    existing_by_id = (
        store.lookup_entity_ids(all_candidates) if all_candidates else {}
    )
    out: dict[str, float] = {}
    for rid, score in matches.items():
        candidates = rowid_to_candidates.get(rid, [])
        chosen = next((c for c in candidates if c in existing_by_id), None)
        if chosen is None:
            continue
        eid = existing_by_id[chosen]
        if score > out.get(eid, float("-inf")):
            out[eid] = score
    return out


def resolve_record_incremental(
    record: dict[str, Any],
    df: pl.DataFrame,
    matchkeys: list[MatchkeyConfig],
    store: IdentityStore,
    run_name: str = "",
    *,
    source: str | None = None,
    source_pk_col: str | None = None,
    dataset: str | None = None,
    ann_blocker: Any = None,
    embedder: Any = None,
    ann_column: str | None = None,
    top_k: int = 20,
    base_store: Any = None,
) -> str | None:
    """Resolve a single new record to an existing entity or create one.

    The streaming counterpart of :func:`resolve_clusters`: it matches ``record``
    against ``df`` via ``match_one``, then delegates the create / absorb / merge
    decision to ``resolve_clusters`` over a MINI frame (the new record + ONLY the
    matched rows). Reusing the batch resolver means evidence edges, golden
    records, events, idempotency, and multi-entity merge all behave identically
    to a batch run -- no resolution logic is re-implemented here.

    Args:
        record: the new record (field -> value); the same shape as ``df`` rows.
        df: the existing frame, with a ``__row_id__`` column.
        matchkeys: the resolved matchkeys to match on (threshold-bearing types).
        store: the ``IdentityStore`` to read/write.
        run_name: batch/run name for event idempotency.
        source: source label for the new record's ``record_id``
            (default: the record's ``__source__`` or ``"dataframe"``).
        source_pk_col: natural-PK column name (else a content hash id is used).
        dataset: optional dataset tag.
        ann_blocker / embedder / ann_column / top_k / base_store: forwarded to
            ``match_one`` for ANN-accelerated candidate retrieval.

    Returns:
        The ``entity_id`` the record resolved to (existing or newly created), or
        ``None`` if it could not be read back. Never raises on a valid input.
    """
    source = source or str(record.get("__source__", "dataframe"))
    matches = _match_record_rows(
        record, df, matchkeys,
        ann_blocker=ann_blocker, embedder=embedder,
        ann_column=ann_column, top_k=top_k, base_store=base_store,
    )

    matched_ids = list(matches.keys())
    if matched_ids:
        from goldenmatch.core.frame import to_frame

        mini = to_frame(df).filter_in("__row_id__", matched_ids).native
        existing_rowids = [int(r) for r in mini["__row_id__"].to_list()]
    else:
        mini = None
        existing_rowids = []

    # New record row id: unique within the mini frame (the matched rows keep
    # their original ids so their record_ids -- and thus their entities -- are
    # rederived identically).
    new_rid = (max(existing_rowids) + 1) if existing_rowids else 0

    # Build the new row aligned to df's columns AND dtypes, so the vertical
    # concat below never up-casts a matched column's dtype -- a dtype shift would
    # change a no-PK row's payload hash and break its record_id lookup.
    new_row: dict[str, Any] = {}
    for col in df.columns:
        if col == "__row_id__":
            new_row[col] = new_rid
        elif col == "__source__":
            new_row[col] = source
        elif col.startswith("__"):
            new_row[col] = None
        else:
            new_row[col] = record.get(col)
    schema = {c: df.schema[c] for c in df.columns}
    new_row_df = pl.DataFrame([new_row], schema=schema)
    mini_plus = (
        pl.concat([mini, new_row_df])
        if mini is not None and not mini.is_empty()
        else new_row_df
    )

    members = [new_rid, *existing_rowids]
    pair_scores = {
        (min(new_rid, m), max(new_rid, m)): float(s) for m, s in matches.items()
    }
    confidence = min(matches.values()) if matches else None
    clusters = {
        0: {
            "members": members,
            "size": len(members),
            "pair_scores": pair_scores,
            "confidence": confidence,
        }
    }
    scored_pairs = [(new_rid, m, float(s)) for m, s in matches.items()]
    mk_name = getattr(matchkeys[0], "name", None) if matchkeys else None

    resolve_clusters(
        clusters=clusters,
        df=mini_plus,
        scored_pairs=scored_pairs,
        matchkey_name=mk_name,
        store=store,
        run_name=run_name,
        dataset=dataset,
        source_pk_col=source_pk_col,
        emit_singletons=True,
        # No weak-conflict edges on a single ingest (there's no bottleneck pair).
        weak_confidence_threshold=0.0,
    )

    # Derive the new record's id from the SAME df-aligned row resolve_clusters
    # saw (record fields absent from df are dropped on both sides), so the
    # lookup matches what was just written.
    primary_id, _ = _record_id_candidates(new_row, source, source_pk_col)
    return store.find_entity_by_record(primary_id)
