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
