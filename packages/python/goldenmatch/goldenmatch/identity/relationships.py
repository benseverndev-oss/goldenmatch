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
) -> int:
    """Derive entity<->entity relationship edges from shared non-identity
    attributes and write them to ``store``. Idempotent across runs. Returns the
    number of edges written (attempted; duplicates are ignored)."""
    if not rules:
        return 0
    total = 0
    for rule in rules:
        groups = store.relationship_groups(
            rule.field, dataset, rule.min_entities, rule.max_fanout,
        )
        edges: list[tuple] = []
        for value, eids in groups:
            uniq = sorted({e for e in eids if e})
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    edges.append(
                        (uniq[i], uniq[j], rule.kind, rule.field, value, dataset)
                    )
        if edges:
            total += store.add_relationships(edges)
    return total


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
