"""Framework-agnostic entity resolution over goldenmatch.

resolve_entities() is the ONLY code in this package that imports goldenmatch.
It builds a Polars frame from each entity's name/type/description, runs
zero-config dedupe_df (auto-config picks the strategy -- the same posture the
product ships), and maps the resulting __row_id__ clusters back to entity ids.
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
    groups: tuple[tuple[str, ...], ...]      # disjoint groups of input ids (incl. singletons)
    canonical_id: dict[str, str]             # input id -> its group's canonical id
    canonical_name: dict[str, str]           # input id -> its group's canonical name


_FIELD_TO_COL = {"name": "name", "type": "entity_type", "description": "context"}


def resolve_entities(
    entities: Sequence[Entity],
    *,
    fields: Sequence[str] = ("name", "type", "description"),
) -> EntityResolution:
    ents = list(entities)
    if not ents:
        return EntityResolution((), {}, {})

    import goldenmatch as gm
    import polars as pl

    cols: dict[str, list[str]] = {_FIELD_TO_COL["name"]: [e.name for e in ents]}
    if "type" in fields and any(e.type for e in ents):
        cols[_FIELD_TO_COL["type"]] = [e.type or "" for e in ents]
    if "description" in fields and any(e.description for e in ents):
        cols[_FIELD_TO_COL["description"]] = [e.description or "" for e in ents]
    df = pl.DataFrame(cols)

    result = gm.dedupe_df(df)

    n = len(ents)
    groups_idx: list[list[int]] = []
    seen: set[int] = set()
    for info in result.clusters.values():
        members = [int(m) for m in info["members"]]
        if info.get("size", len(members)) > 1:
            groups_idx.append(members)
            seen.update(members)
    groups_idx.extend([i] for i in range(n) if i not in seen)  # singletons

    groups: list[tuple[str, ...]] = []
    canonical_id: dict[str, str] = {}
    canonical_name: dict[str, str] = {}
    for grp in groups_idx:
        rep = min(grp, key=lambda i: (-len(ents[i].name), i))  # longest name, tie -> lowest idx
        cid, cname = ents[rep].id, ents[rep].name
        ids = tuple(ents[i].id for i in grp)
        groups.append(ids)
        for i in grp:
            canonical_id[ents[i].id] = cid
            canonical_name[ents[i].id] = cname

    return EntityResolution(tuple(groups), canonical_id, canonical_name)
