"""Semantic-graph: entity<->entity relationship edges (#semantic-graph).

Identity resolution collapses records that are the SAME entity. The same blocking
data also surfaces DIFFERENT entities that share a NON-identity attribute -- two
prescribers on one clinic phone, two people at one address -- which is a
relationship, not a merge, and today it is discarded. ``build_relationships``
turns those shared attributes into edges between the durable ``entity_id``s,
turning the identity graph into a semantic graph maintained in the same pass.

Edges are stored in ``identity_relationships`` (entity-level, distinct from the
record-level ``evidence_edges``) and de-duped by primary key, so a re-resolve is
idempotent. Hub values (a switchboard line shared by hundreds of entities) are
skipped via ``RelationshipRule.max_fanout`` -- they are not pairwise relationships.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from goldenmatch.config.schemas import RelationshipRule
    from goldenmatch.identity.store import IdentityStore


def build_relationships(
    store: IdentityStore,
    rules: list[RelationshipRule] | None,
    dataset: str | None = None,
) -> tuple[int, int]:
    """Derive entity<->entity relationship edges from shared non-identity
    attributes and RECONCILE them into ``store`` so the graph equals the current
    desired state: new edges inserted, stale edges deleted, merges/splits reflected
    (``desired`` is recomputed from current entity ids). Idempotent -- same data
    twice writes/deletes nothing. Returns ``(inserted, deleted)``.

    Reconciliation is per ``(dataset, kind)``, so rules that share a ``kind`` union
    into one desired set, and a kind whose rule now yields NO edges (its shared
    values disappeared) still has its stale edges cleared (reconcile-to-empty)."""
    if not rules:
        return (0, 0)
    by_kind: dict[str, list[tuple]] = {}
    for rule in rules:
        groups = store.relationship_groups(
            rule.field, dataset, rule.min_entities, rule.max_fanout,
            rule.transform,
        )
        for value, eids in groups:
            uniq = sorted({e for e in eids if e})
            bucket = by_kind.setdefault(rule.kind, [])
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    bucket.append(
                        (uniq[i], uniq[j], rule.kind, rule.field, value, dataset)
                    )
    # Kinds whose rules produced nothing this run must still reconcile to empty so
    # their prior-run edges get deleted, not left stale.
    for rule in rules:
        by_kind.setdefault(rule.kind, [])
    inserted = deleted = 0
    for kind, desired in by_kind.items():
        ins, dele, _ = store.reconcile_relationships(dataset, kind, desired)
        inserted += ins
        deleted += dele
    return (inserted, deleted)


def suggest_relationship_rules(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    sample: int = 50_000,
    min_entities: int = 2,
    max_fanout: int = 50,
    top_k: int = 8,
    exclude: list[str] | None = None,
) -> list[RelationshipRule]:
    """Profile a bounded sample of resolved records and SUGGEST relationship rules
    for the fields that look like good edge sources -- fields with many values
    shared by ``min_entities..max_fanout`` distinct entities (real pairwise links)
    and few hub values (a value held by everyone, like ``country``, makes no useful
    edge). This is the "the program identifies good fields" half of the feature;
    the user-defined half is writing ``RelationshipRule``s by hand.

    ADVISORY, not authoritative: the sample is ``LIMIT``-bounded (cheap at 14M) so
    it can undercount a value's true fan-out -- treat the ranked list as a starting
    point a human (or an ``auto`` config) reviews, not ground truth. Fields whose
    names are not SQL-safe are skipped (they would fail ``relationship_groups``).
    Returns up to ``top_k`` rules, highest edge-yield first."""
    from collections import defaultdict

    from goldenmatch.config.schemas import RelationshipRule
    from goldenmatch.identity.store import _SAFE_FIELD

    skip = {c.lower() for c in (exclude or ())}
    field_vals: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for eid, payload in store.sample_records(dataset, sample):
        for key, val in payload.items():
            if key.lower() in skip or not _SAFE_FIELD.fullmatch(key) or val is None:
                continue
            sval = str(val).strip()
            if sval:
                field_vals[key][sval].add(eid)

    scored: list[tuple[float, int, str]] = []
    for field, vals in field_vals.items():
        sweet = sum(1 for e in vals.values() if min_entities <= len(e) <= max_fanout)
        if not sweet:
            continue
        hubs = sum(1 for e in vals.values() if len(e) > max_fanout)
        # fraction of distinct values that yield real (non-hub) edges: rewards
        # fields dominated by sweet-spot values, penalizes near-unique + hub fields.
        score = sweet / (len(vals) + hubs + 1)
        scored.append((score, sweet, field))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [
        RelationshipRule(
            field=field, kind=f"shares_{field}",
            min_entities=min_entities, max_fanout=max_fanout,
        )
        for _score, _sweet, field in scored[:top_k]
    ]


def _entity_name(node: object, name_field: str | None) -> str | None:
    """A display name for an entity: the configured golden-record field, else the
    first non-empty string value of the golden record."""
    golden = getattr(node, "golden_record", None)
    if not isinstance(golden, dict):
        return None
    if name_field is not None:
        v = golden.get(name_field)
        return str(v) if v not in (None, "") else None
    for v in golden.values():
        if isinstance(v, str) and v.strip():
            return v
    return None


def to_graph_batch(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    name_field: str | None = None,
    valid_from: int = 0,
) -> dict:
    """Export the identity relationship graph as a GoldenGraph ``StoreBatch`` dict
    -- the ``{entities, edges}`` JSON shape that ``goldengraph``'s ``PyStore.append``
    consumes -- so the durable identity graph feeds GoldenGraph's path-retrieval /
    answer (GraphRAG) layer.

    Each entity carries the stable ``entity_id`` in ``record_keys``, which is
    exactly how GoldenGraph's store reconciles the same node across appends
    (``store.rs::append``): re-exporting after a later resolve updates the same
    node instead of minting a new one. Edges become ``(subj_local, predicate=kind,
    obj_local)`` triples. Pure data -- no ``goldengraph`` import required. Append
    with ``pystore.append(json.dumps(to_graph_batch(store)))``.
    """
    rels = store.list_relationships(dataset)
    eids = sorted({e for r in rels for e in (r["entity_a_id"], r["entity_b_id"])})
    local = {eid: i for i, eid in enumerate(eids)}
    nodes = store.get_identities(set(eids)) if eids else {}
    refs = [dataset] if dataset else []

    entities = []
    for eid in eids:
        name = _entity_name(nodes.get(eid), name_field) or eid
        entities.append({
            "local_id": local[eid],
            "canonical_name": name,
            "typ": "entity",
            "surface_names": [name],
            "record_keys": [eid],          # stable cross-append reconciliation key
            "source_refs": list(refs),
        })

    edges = []
    for r in rels:
        edges.append({
            "subj_local": local[r["entity_a_id"]],
            "predicate": r["kind"],
            "obj_local": local[r["entity_b_id"]],
            "valid_from": valid_from,
            "valid_to": None,
            "source_refs": [r["shared_value"]] if r.get("shared_value") else list(refs),
        })

    return {"entities": entities, "edges": edges}
