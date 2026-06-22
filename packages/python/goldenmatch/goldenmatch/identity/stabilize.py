"""Cross-run entity stabilization -- Identity v3 (#1112, epic #1108).

Per-run resolution (``resolve_clusters``) decides create / absorb / merge from
the evidence in *that* run. But two entities can keep being linked, run after
run, by borderline evidence that never clears the in-run merge bar -- so the
graph re-litigates the same near-duplicate identities forever and the
``entity_id``s churn instead of settling.

This module is the stabilization pass that runs *across* runs over the durable
store:

1. **Find persistent overlaps** (``find_persistent_overlaps``) -- entity pairs
   linked by cross-entity evidence edges in at least ``min_runs`` DISTINCT runs.
   A single noisy run can't trigger consolidation; a *persistent* overlap does.
2. **Consolidate** (``stabilize_identities``) -- merge each connected component
   of persistently-overlapping entities into one survivor, picked by a
   configurable winner strategy, and emit a ``CONSOLIDATED`` event (distinct
   from a human ``MANUAL_MERGE``). ``apply=False`` is a dry run.
3. **Versioning** (``entity_version``) -- a canonical, monotonic version per
   entity derived from its structural event log, so a downstream consumer can
   tell a stabilized entity has changed shape.

It reads/writes the existing store and adds no schema migration (the new
``CONSOLIDATED`` event kind is just a value in the existing ``kind`` column).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from goldenmatch.identity.model import (
    EdgeKind,
    EventKind,
    IdentityEvent,
    IdentityStatus,
)

if TYPE_CHECKING:
    from goldenmatch.config.schemas import StabilizationConfig
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import IdentityStore

# Edge kinds that count as cross-entity OVERLAP evidence. ``same_as`` /
# ``possible_same_as`` are positive "these belong together" signals;
# ``conflicts_with`` is negative evidence and is excluded -- consolidating on a
# conflict would be exactly wrong.
DEFAULT_OVERLAP_KINDS: tuple[str, ...] = (
    EdgeKind.SAME_AS.value,
    EdgeKind.POSSIBLE_SAME_AS.value,
)

# Event kinds that advance an entity's canonical version (structural changes).
_VERSIONING_EVENTS: frozenset[str] = frozenset({
    EventKind.CREATED.value,
    EventKind.ABSORBED_RECORD.value,
    EventKind.MERGED_WITH.value,
    EventKind.MANUAL_MERGE.value,
    EventKind.SPLIT_FROM.value,
    EventKind.MANUAL_SPLIT.value,
    EventKind.CONSOLIDATED.value,
})

WINNER_STRATEGIES: tuple[str, ...] = (
    "most_records",
    "oldest",
    "newest",
    "lowest_id",
)

_PAGE = 500


# ── Result types ────────────────────────────────────────────────────────────


@dataclass
class OverlapCandidate:
    """Two currently-separate entities linked across multiple runs."""

    entity_a: str
    entity_b: str
    run_count: int            # number of DISTINCT runs with cross-entity evidence
    runs: list[str]
    edge_count: int
    max_score: float | None


@dataclass
class ConsolidationGroup:
    """A connected component of overlapping entities and its chosen survivor."""

    winner: str
    absorbed: list[str]
    run_count: int            # min binding run-support across the component
    strategy: str

    @property
    def size(self) -> int:
        return 1 + len(self.absorbed)


@dataclass
class StabilizeReport:
    candidates: list[OverlapCandidate]
    consolidations: list[ConsolidationGroup]
    applied: bool
    entities_consolidated: int = 0  # number of entities absorbed (retired)

    def as_dict(self) -> dict[str, object]:
        return {
            "n_candidates": len(self.candidates),
            "n_consolidations": len(self.consolidations),
            "entities_consolidated": self.entities_consolidated,
            "applied": self.applied,
        }


# ── Overlap detection ───────────────────────────────────────────────────────


def _all_active_entities(
    store: IdentityStore, dataset: str | None,
) -> list[IdentityNode]:
    out: list[IdentityNode] = []
    offset = 0
    while True:
        page = store.list_identities(
            dataset=dataset,
            status=IdentityStatus.ACTIVE.value,
            limit=_PAGE,
            offset=offset,
        )
        out.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return out


def _canon(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def find_persistent_overlaps(
    store: IdentityStore,
    *,
    min_runs: int = 3,
    edge_kinds: tuple[str, ...] = DEFAULT_OVERLAP_KINDS,
    dataset: str | None = None,
    min_score: float = 0.0,
) -> list[OverlapCandidate]:
    """Find pairs of active entities linked by cross-entity evidence in at least
    ``min_runs`` distinct runs.

    Builds a ``record_id -> entity_id`` map over the active entities, then scans
    every entity's evidence edges: an edge of a kind in ``edge_kinds`` whose two
    records currently live in two DIFFERENT active entities is cross-entity
    overlap. A pair qualifies once it accumulates ``min_runs`` distinct run names
    (and a ``max_score >= min_score``). Returned most-persistent first.
    """
    edge_kind_set = set(edge_kinds)
    entities = _all_active_entities(store, dataset)
    active_ids = {e.entity_id for e in entities}

    record_to_entity: dict[str, str] = {}
    for ent in entities:
        for rec in store.get_records_for_entity(ent.entity_id):
            record_to_entity[rec.record_id] = ent.entity_id

    pair_runs: dict[tuple[str, str], set[str]] = {}
    pair_edges: dict[tuple[str, str], int] = {}
    pair_score: dict[tuple[str, str], float | None] = {}

    for ent in entities:
        for edge in store.edges_for_entity(ent.entity_id):
            if edge.kind not in edge_kind_set:
                continue
            ea = record_to_entity.get(edge.record_a_id)
            eb = record_to_entity.get(edge.record_b_id)
            if ea is None or eb is None or ea == eb:
                continue
            if ea not in active_ids or eb not in active_ids:
                continue
            key = _canon(ea, eb)
            pair_runs.setdefault(key, set())
            if edge.run_name:
                pair_runs[key].add(edge.run_name)
            pair_edges[key] = pair_edges.get(key, 0) + 1
            if edge.score is not None:
                prev = pair_score.get(key)
                pair_score[key] = (
                    edge.score if prev is None else max(prev, edge.score)
                )

    candidates: list[OverlapCandidate] = []
    for key, runs in pair_runs.items():
        if len(runs) < min_runs:
            continue
        score = pair_score.get(key)
        if score is not None and score < min_score:
            continue
        candidates.append(OverlapCandidate(
            entity_a=key[0],
            entity_b=key[1],
            run_count=len(runs),
            runs=sorted(runs),
            edge_count=pair_edges.get(key, 0),
            max_score=score,
        ))
    candidates.sort(key=lambda c: (-c.run_count, c.entity_a, c.entity_b))
    return candidates


# ── Winner selection ────────────────────────────────────────────────────────


def _select_winner(
    store: IdentityStore, members: list[str], strategy: str,
) -> str:
    """Pick the survivor among ``members`` per ``strategy``. Ties always break to
    the oldest ``created_at`` then the lexicographically smallest id, so the
    choice is deterministic and stable run-to-run."""
    nodes = {m: store.get_identity(m) for m in members}

    def created(m: str) -> datetime:
        n = nodes.get(m)
        return n.created_at if n and n.created_at else datetime.max

    if strategy == "lowest_id":
        return min(members)
    if strategy == "oldest":
        return min(members, key=lambda m: (created(m), m))
    if strategy == "newest":
        # Latest created_at; tie-break to the smallest id. Pre-sort by id so
        # max() (first-max-wins) yields the smallest id among equal timestamps.
        return max(sorted(members), key=created)
    # default: most_records -> (most records, then oldest, then smallest id).
    def record_count(m: str) -> int:
        return len(store.get_records_for_entity(m))
    return min(members, key=lambda m: (-record_count(m), created(m), m))


# ── Consolidation ───────────────────────────────────────────────────────────


def stabilize_identities(
    store: IdentityStore,
    *,
    min_runs: int = 3,
    winner_strategy: str = "most_records",
    edge_kinds: tuple[str, ...] = DEFAULT_OVERLAP_KINDS,
    dataset: str | None = None,
    min_score: float = 0.0,
    run_name: str = "stabilize",
    apply: bool = False,
    config: StabilizationConfig | None = None,
) -> StabilizeReport:
    """Consolidate persistently-overlapping entities into stable survivors.

    Finds overlap candidates (:func:`find_persistent_overlaps`), unions them into
    connected components (so an A-B, B-C chain consolidates A/B/C together),
    picks each component's survivor by ``winner_strategy``, and -- when
    ``apply=True`` -- reassigns the absorbed entities' records to the survivor,
    retires them (``merged_into=winner``), and emits a ``CONSOLIDATED`` event on
    each side. ``apply=False`` (default) is a dry run: the report describes what
    *would* happen and the store is untouched.

    ``config`` (a ``StabilizationConfig``) supplies defaults; an explicit keyword
    argument always wins.
    """
    if config is not None:
        if min_runs == 3:
            min_runs = config.min_runs
        if winner_strategy == "most_records":
            winner_strategy = config.winner_strategy
        if min_score == 0.0:
            min_score = config.min_score

    if winner_strategy not in WINNER_STRATEGIES:
        raise ValueError(
            f"Invalid winner_strategy {winner_strategy!r}; "
            f"must be one of {WINNER_STRATEGIES}"
        )

    candidates = find_persistent_overlaps(
        store, min_runs=min_runs, edge_kinds=edge_kinds,
        dataset=dataset, min_score=min_score,
    )

    # Union-Find over candidate pairs -> connected components.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    # min binding run-support per eventual component (for reporting).
    pair_runs = {(_canon(c.entity_a, c.entity_b)): c.run_count for c in candidates}
    for c in candidates:
        union(c.entity_a, c.entity_b)

    components: dict[str, list[str]] = {}
    for c in candidates:
        for e in (c.entity_a, c.entity_b):
            root = find(e)
            members = components.setdefault(root, [])
            if e not in members:
                members.append(e)

    consolidations: list[ConsolidationGroup] = []
    absorbed_total = 0
    for members in components.values():
        if len(members) < 2:
            continue
        winner = _select_winner(store, members, winner_strategy)
        absorbed = sorted(m for m in members if m != winner)
        comp_run_counts = [
            rc for pair, rc in pair_runs.items()
            if pair[0] in members and pair[1] in members
        ]
        group = ConsolidationGroup(
            winner=winner,
            absorbed=absorbed,
            run_count=min(comp_run_counts) if comp_run_counts else min_runs,
            strategy=winner_strategy,
        )
        consolidations.append(group)
        if apply:
            absorbed_total += _apply_consolidation(store, group, run_name)

    consolidations.sort(key=lambda g: (-g.size, g.winner))
    return StabilizeReport(
        candidates=candidates,
        consolidations=consolidations,
        applied=apply,
        entities_consolidated=absorbed_total,
    )


def _apply_consolidation(
    store: IdentityStore, group: ConsolidationGroup, run_name: str,
) -> int:
    """Reassign + retire the absorbed entities into the winner; emit events.
    Idempotent: an already-retired loser is skipped. Returns # absorbed."""
    winner_node = store.get_identity(group.winner)
    if winner_node is None or winner_node.status != IdentityStatus.ACTIVE.value:
        return 0
    done = 0
    for loser in group.absorbed:
        loser_node = store.get_identity(loser)
        if loser_node is None or loser_node.status != IdentityStatus.ACTIVE.value:
            continue
        for rec in store.get_records_for_entity(loser):
            rec.entity_id = group.winner
            rec.last_seen_at = datetime.now()
            store.upsert_record(rec)
        store.retire_identity(loser, merged_into=group.winner)
        now = datetime.now()
        store.emit_event(IdentityEvent(
            entity_id=group.winner,
            kind=EventKind.CONSOLIDATED.value,
            payload={
                "absorbed": loser,
                "reason": "persistent_cross_run_overlap",
                "run_support": group.run_count,
                "strategy": group.strategy,
            },
            run_name=run_name, recorded_at=now,
        ))
        store.emit_event(IdentityEvent(
            entity_id=loser,
            kind=EventKind.CONSOLIDATED.value,
            payload={"merged_into": group.winner},
            run_name=run_name, recorded_at=now,
        ))
        done += 1
    return done


# ── Versioning ──────────────────────────────────────────────────────────────


def entity_version(store: IdentityStore, entity_id: str) -> int:
    """Canonical, monotonic version of an entity: the count of structural events
    in its history (create / absorb / merge / consolidate / split). A freshly
    created entity is version 1; each structural change increments it, so a
    consumer can detect that a stabilized entity changed shape."""
    return sum(
        1 for ev in store.history(entity_id) if ev.kind in _VERSIONING_EVENTS
    )
