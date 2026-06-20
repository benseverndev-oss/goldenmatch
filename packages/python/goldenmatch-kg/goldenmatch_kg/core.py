"""Framework-agnostic entity resolution stub.

Entity and EntityResolution are the public data model for goldenmatch-kg.
resolve_entities() is the ONLY code in this package that imports goldenmatch.
It builds a Polars frame from each entity's name/type/description, runs
zero-config dedupe_df (auto-config picks the strategy -- the same posture the
product ships), and maps the resulting __row_id__ clusters back to entity ids.

Task 2 replaces ONLY the resolve_entities body + adds the import of goldenmatch.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    type: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class EntityResolution:
    groups: tuple[tuple[str, ...], ...]  # disjoint groups of input ids (incl. singletons)
    canonical_id: dict[str, str]         # input id -> its group's canonical id
    canonical_name: dict[str, str]       # input id -> its group's canonical name


def resolve_entities(
    entities: Sequence[Entity],
    *,
    fields: Sequence[str] = ("name", "type", "description"),
) -> EntityResolution:
    raise NotImplementedError
