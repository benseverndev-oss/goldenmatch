"""Base-free goldenmatch resolution helper for the LlamaIndex shim.

This module imports ONLY goldenmatch_kg.core (no llama_index), so it
can be imported and tested locally without the framework extra installed.

Public API:
    canonical_names(items: list[tuple[str, str, str]]) -> dict[str, str]

items is a list of (id, name, label) triples. The function groups records by
label first (type), calls core.resolve_entities within each label group, and
returns a dict mapping each id -> its group's canonical NAME (the longest
name in the group, tie-break by lowest input position). This is the canonical
name that the LlamaIndex shim rewrites each EntityNode's .name to, so that
the downstream exact-name upsert collapses all variants into one graph node.
"""
from __future__ import annotations

from goldenmatch_kg.core import Entity, resolve_entities


def canonical_names(items: list[tuple[str, str, str]]) -> dict[str, str]:
    """Map each entity id to its group's canonical NAME using goldenmatch.

    Records are grouped by label first (type), replicating per-entity-type
    grouping so goldenmatch only compares entities of the same type. Within
    each label group, resolve_entities finds duplicate mentions and selects
    the canonical representative (longest name, tie-break by lowest input
    position). The canonical NAME of the representative is propagated to all
    group members so the downstream exact-name upsert sees one surface form.

    This is NAME-canonicalization only. Node ids and relationships are NOT
    rewritten here -- that is left to the caller to decide (rewriting ids
    can orphan edges).

    Args:
        items: list of (id, name, label) triples.

    Returns:
        dict mapping every input id -> its group's canonical name (the
        longest name in the group; variants share the same canonical name,
        singletons map to their own name unchanged).
    """
    # Group by label (type) so we only compare same-type entities.
    by_label: dict[str, list[tuple[str, str]]] = {}
    for rid, name, label in items:
        by_label.setdefault(label, []).append((rid, name))

    result: dict[str, str] = {}
    for label, members in by_label.items():
        entities = [
            Entity(id=rid, name=name, type=label)
            for rid, name in members
        ]
        resolution = resolve_entities(entities)
        for rid, _name, _label in items:
            if _label == label:
                result[rid] = resolution.canonical_name[rid]

    return result
