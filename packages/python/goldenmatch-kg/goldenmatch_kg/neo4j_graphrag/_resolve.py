"""Base-free goldenmatch resolution helper for the neo4j-graphrag shim.

This module imports ONLY goldenmatch_kg.core (no neo4j_graphrag), so it
can be imported and tested locally without the framework extra installed.

Public API:
    resolve_records(items) -> list[list[str]]

items is a list of (id, name, label) triples. The function groups records by
label, calls core.resolve_entities within each label group, and returns the
multi-member groups as lists of ids (singletons are omitted by default so the
caller can decide how to surface them in the library's merge shape).
"""
from __future__ import annotations

from goldenmatch_kg.core import Entity, resolve_entities


def resolve_records(items: list[tuple[str, str, str]]) -> list[list[str]]:
    """Resolve a flat list of (id, name, label) triples with goldenmatch.

    Records are grouped by label first (replicating the per-entity-type grouping
    that BasePropertySimilarityResolver.run() performs), then resolve_entities is
    called within each group. Only multi-member groups are returned -- callers that
    need a full partition can reconstruct singletons from the unseen ids.

    Args:
        items: list of (id, name, label) triples.

    Returns:
        list of groups (each a list of ids); only multi-member groups are included.
    """
    # Group record ids by label (mirrors BasePropertySimilarityResolver grouping).
    by_label: dict[str, list[tuple[str, str]]] = {}
    for rid, name, label in items:
        by_label.setdefault(label, []).append((rid, name))

    groups: list[list[str]] = []
    for label, members in by_label.items():
        entities = [
            Entity(id=rid, name=name, type=label)
            for rid, name in members
        ]
        resolution = resolve_entities(entities)
        for group in resolution.groups:
            if len(group) > 1:
                groups.append(list(group))

    return groups
