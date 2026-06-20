"""Base-free goldenmatch resolution helper for the Graphiti shim.

This module imports ONLY goldenmatch_kg.core (no graphiti_core), so it
can be imported and tested locally without the framework extra installed.

Public API:
    propose_merges(items: list[tuple[str, str]]) -> list[list[str]]

items is a list of (uuid, name) pairs extracted from Graphiti EntityNodes.
The function calls core.resolve_entities (name-only ER -- Graphiti entity
nodes have no reliable type/label at re-resolution time) and returns ONLY
the multi-member groups (lists of uuids that should be merged). Singletons
are omitted because every singleton is a no-op for the caller.
"""
from __future__ import annotations

from goldenmatch_kg.core import Entity, resolve_entities


def propose_merges(items: list[tuple[str, str]]) -> list[list[str]]:
    """Identify duplicate Graphiti entity nodes using goldenmatch.

    Runs zero-config goldenmatch ER over the entity names and returns the
    groups that should be merged (multi-member groups only). Singletons are
    omitted: they require no action by the caller.

    Name-only ER is used here because Graphiti EntityNodes do not carry a
    reliable per-node type/label at re-resolution time (the label is on the
    edge relationship, not the entity node itself).

    Args:
        items: list of (uuid, name) pairs from Graphiti EntityNodes.

    Returns:
        list of merge groups; each group is a list of uuids. Only groups
        with two or more members are included. The caller merges each group
        down to one canonical entity (e.g. by deleting all but the
        canonical node and re-pointing its edges).
    """
    if not items:
        return []

    entities = [Entity(id=uuid, name=name) for uuid, name in items]
    resolution = resolve_entities(entities, fields=("name",))

    return [
        list(group)
        for group in resolution.groups
        if len(group) > 1
    ]
