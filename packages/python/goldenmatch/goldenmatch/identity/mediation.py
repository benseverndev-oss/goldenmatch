"""Conflict mediation workflow -- Identity v3 (#1113, epic #1108).

Resolution flags weak links as ``conflicts_with`` evidence edges (a cluster's
weak bottleneck pair, and conflicts carried forward through a merge). These edges
are *intra-entity*: both records sit inside ONE entity, and the conflict asks
"is this entity an over-merge?". Today they pile up in the conflicts feed with no
way for a steward to actually adjudicate them -- so the same conflict resurfaces
every run.

This module is the mediation workflow on top of that feed:

1. **Queue** (``open_conflicts``) -- the ``conflicts_with`` edges a steward still
   has to act on: every conflict pair MINUS the ones already given a terminal
   verdict (same / distinct). Deferred conflicts stay in the queue.
2. **Adjudicate** (``mediate_conflict``) -- record a steward's verdict and act on
   it:
     * ``same``     -> confirmed one person: keep the entity intact (dismiss).
     * ``distinct`` -> confirmed over-merge: split the conflicting record out
       into its own identity (``manual_split``).
     * ``defer``    -> needs more info: stays open, but the decision is logged.
   Every verdict is persisted as a durable ``mediation_verdict`` evidence edge
   (so it survives, suppresses re-surfacing, and is auditable) plus a
   ``CONFLICT_MEDIATED`` event.
3. **Audit** (``pair_verdict`` / ``mediation_summary``) -- the current verdict on
   a pair and a roll-up of the queue's state.

Reuses the existing store + ``manual_split``; adds no schema migration (the new
edge / event kinds are values in existing ``kind`` columns).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from goldenmatch.identity.model import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
    IdentityEvent,
    canon_record_pair,
)

if TYPE_CHECKING:
    from goldenmatch.config.schemas import MediationConfig
    from goldenmatch.identity.store import IdentityStore


class ConflictResolution(StrEnum):
    SAME = "same"          # confirmed one person -> keep entity intact
    DISTINCT = "distinct"  # confirmed over-merge -> split the record out
    DEFER = "defer"        # needs more info -> stays open


# Verdicts that CLOSE a conflict (remove it from the open queue).
_TERMINAL = frozenset({ConflictResolution.SAME.value, ConflictResolution.DISTINCT.value})


@dataclass
class ConflictItem:
    """One open conflict awaiting a steward verdict."""

    record_a_id: str
    record_b_id: str
    entity_id: str | None      # the entity both records currently sit in
    score: float | None
    matchkey_name: str | None
    run_name: str | None
    reason: str | None         # why it was flagged (from the edge's negative_evidence)
    recorded_at: datetime
    deferred: bool = False     # a prior ``defer`` verdict is in effect

    @property
    def pair(self) -> tuple[str, str]:
        return canon_record_pair(self.record_a_id, self.record_b_id)


@dataclass
class MediationVerdict:
    record_a_id: str
    record_b_id: str
    resolution: str
    steward: str | None
    reason: str | None
    resolved_at: datetime

    @property
    def pair(self) -> tuple[str, str]:
        return canon_record_pair(self.record_a_id, self.record_b_id)


# ── Verdict storage / lookup ────────────────────────────────────────────────


def _latest_verdicts(
    store: IdentityStore, dataset: str | None,
) -> dict[tuple[str, str], MediationVerdict]:
    """Most-recent verdict per canonical record pair (verdict edges are stored
    newest-first; the first one we see for a pair wins)."""
    out: dict[tuple[str, str], MediationVerdict] = {}
    for e in store.edges_by_kind(EdgeKind.MEDIATION_VERDICT.value, dataset=dataset):
        pair = canon_record_pair(e.record_a_id, e.record_b_id)
        if pair in out:
            continue  # edges_by_kind is recorded_at DESC -> first = latest
        ne = e.negative_evidence or {}
        out[pair] = MediationVerdict(
            record_a_id=e.record_a_id,
            record_b_id=e.record_b_id,
            resolution=str(ne.get("resolution", "")),
            steward=ne.get("steward"),
            reason=ne.get("reason"),
            resolved_at=e.recorded_at,
        )
    return out


def pair_verdict(
    store: IdentityStore,
    record_a_id: str,
    record_b_id: str,
    *,
    dataset: str | None = None,
) -> ConflictResolution | None:
    """The current verdict on a record pair, or ``None`` if never mediated."""
    v = _latest_verdicts(store, dataset).get(
        canon_record_pair(record_a_id, record_b_id)
    )
    if v is None or not v.resolution:
        return None
    try:
        return ConflictResolution(v.resolution)
    except ValueError:
        return None


# ── Queue ───────────────────────────────────────────────────────────────────


def open_conflicts(
    store: IdentityStore,
    *,
    dataset: str | None = None,
    include_deferred: bool = True,
) -> list[ConflictItem]:
    """The conflicts a steward still has to act on.

    Every ``conflicts_with`` edge MINUS pairs with a terminal verdict
    (same / distinct). One item per canonical record pair (the most recent edge
    wins if a pair was flagged in several runs). ``include_deferred=False`` also
    hides pairs already marked ``defer``.
    """
    verdicts = _latest_verdicts(store, dataset)
    seen: set[tuple[str, str]] = set()
    items: list[ConflictItem] = []
    for e in store.find_conflicts(dataset=dataset):  # recorded_at DESC
        pair = canon_record_pair(e.record_a_id, e.record_b_id)
        if pair in seen:
            continue
        seen.add(pair)
        v = verdicts.get(pair)
        deferred = False
        if v is not None and v.resolution in _TERMINAL:
            continue  # already adjudicated -> closed
        if v is not None and v.resolution == ConflictResolution.DEFER.value:
            deferred = True
            if not include_deferred:
                continue
        ne = e.negative_evidence or {}
        items.append(ConflictItem(
            record_a_id=e.record_a_id,
            record_b_id=e.record_b_id,
            entity_id=store.find_entity_by_record(e.record_a_id),
            score=e.score,
            matchkey_name=e.matchkey_name,
            run_name=e.run_name,
            reason=ne.get("reason"),
            recorded_at=e.recorded_at,
            deferred=deferred,
        ))
    return items


# ── Adjudicate ──────────────────────────────────────────────────────────────


def mediate_conflict(
    store: IdentityStore,
    record_a_id: str,
    record_b_id: str,
    resolution: str | ConflictResolution,
    *,
    steward: str | None = None,
    reason: str | None = None,
    dataset: str | None = None,
    apply: bool = True,
    config: MediationConfig | None = None,
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Record a steward's verdict on a conflict pair and act on it.

    ``same`` keeps the entity intact; ``distinct`` splits ``record_b_id`` out of
    its entity into a new identity (when ``apply``); ``defer`` only logs. The
    verdict is always persisted as a ``mediation_verdict`` edge + a
    ``CONFLICT_MEDIATED`` event, so it is durable and auditable and stops the
    conflict re-surfacing in :func:`open_conflicts`.

    ``config`` (a ``MediationConfig``) supplies the ``apply`` default; an explicit
    ``apply`` keyword always wins.
    """
    try:
        res = ConflictResolution(str(resolution))
    except ValueError as exc:
        raise ValueError(
            f"Invalid resolution {resolution!r}; must be one of "
            f"{[r.value for r in ConflictResolution]}"
        ) from exc

    if config is not None and apply is True:
        apply = config.auto_apply

    entity_id = (
        store.find_entity_by_record(record_a_id)
        or store.find_entity_by_record(record_b_id)
    )
    now = datetime.now()
    # Provenance (#1075/#1078): attribute the verdict. Falls back to the steward
    # id when no explicit actor is given.
    actor = actor or (f"steward:{steward}" if steward else None)
    # Unique run_name per call so re-mediating a pair appends a new verdict
    # (the UNIQUE(entity, a, b, kind, run_name) constraint won't no-op it) and
    # the latest wins in _latest_verdicts.
    verdict_run = f"mediation:{now.isoformat()}"

    # 1. Persist the durable verdict edge.
    store.add_edge(EvidenceEdge(
        entity_id=entity_id or "",
        record_a_id=record_a_id,
        record_b_id=record_b_id,
        kind=EdgeKind.MEDIATION_VERDICT.value,
        score=None,
        negative_evidence={
            "resolution": res.value,
            "steward": steward,
            "reason": reason,
        },
        run_name=verdict_run,
        dataset=dataset,
        actor=actor,
        trust=trust,
        recorded_at=now,
    ))

    # 2. Act on it.
    action: dict[str, Any] = {"type": "none"}
    if res == ConflictResolution.DISTINCT and apply and entity_id is not None:
        from goldenmatch.identity.query import manual_split

        # Only split record_b if it's actually in this entity (idempotent).
        rec_b = store.get_record(record_b_id)
        if rec_b is not None and rec_b.entity_id == entity_id:
            split = manual_split(
                store, entity_id, [record_b_id],
                reason=reason or "conflict_mediation:distinct",
                run_name=verdict_run,
                actor=actor, trust=trust,
            )
            action = {"type": "split", **split}

    # 3. Audit event on the (origin) entity.
    if entity_id is not None:
        store.emit_event(IdentityEvent(
            entity_id=entity_id,
            kind=EventKind.CONFLICT_MEDIATED.value,
            payload={
                "record_a_id": record_a_id,
                "record_b_id": record_b_id,
                "resolution": res.value,
                "steward": steward,
                "reason": reason,
                "action": action.get("type"),
            },
            run_name=verdict_run,
            dataset=dataset,
            actor=actor,
            trust=trust,
            recorded_at=now,
        ))

    return {
        "record_a_id": record_a_id,
        "record_b_id": record_b_id,
        "resolution": res.value,
        "entity_id": entity_id,
        "applied": apply,
        "action": action,
        "at": now.isoformat(),
    }


# ── Audit / summary ─────────────────────────────────────────────────────────


def mediation_summary(
    store: IdentityStore, *, dataset: str | None = None,
) -> dict[str, int]:
    """Roll-up of the conflict feed: how many are open, deferred, and resolved
    each way. ``open`` excludes terminally-resolved pairs; ``deferred`` is the
    subset of open with a standing ``defer`` verdict."""
    verdicts = _latest_verdicts(store, dataset)
    conflict_pairs = {
        canon_record_pair(e.record_a_id, e.record_b_id)
        for e in store.find_conflicts(dataset=dataset)
    }
    same = distinct = deferred = open_count = 0
    for pair in conflict_pairs:
        v = verdicts.get(pair)
        if v is None or not v.resolution:
            open_count += 1
        elif v.resolution == ConflictResolution.SAME.value:
            same += 1
        elif v.resolution == ConflictResolution.DISTINCT.value:
            distinct += 1
        elif v.resolution == ConflictResolution.DEFER.value:
            deferred += 1
            open_count += 1
    return {
        "total": len(conflict_pairs),
        "open": open_count,
        "deferred": deferred,
        "resolved_same": same,
        "resolved_distinct": distinct,
    }
