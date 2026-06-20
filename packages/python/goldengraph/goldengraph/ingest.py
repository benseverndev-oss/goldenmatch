"""The end-to-end path: text -> extract -> resolve -> durable store.

`ingest` wires the pipeline into SP4a's `PyStore` over the JSON `append`
boundary. `resolver` is injectable (defaults to goldenmatch-backed `resolve`) so
tests can supply a deterministic resolution without goldenmatch installed.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from .extract import Extraction, Mention, extract as _extract
from .llm import LLMClient
from .resolve import ResolvedEntity, resolve as _resolve

Resolver = Callable[[list[Mention]], list[ResolvedEntity]]


def build_batch(
    extraction: Extraction,
    entities: list[ResolvedEntity],
    *,
    at: int,
    valid_from: int | None = None,
) -> dict:
    """Build a `StoreBatch` dict (SP4a JSON shape) from a resolved extraction.

    Remaps each relationship's mention indices to the owning entity `local_id`;
    drops self-loops (endpoints in the same entity after dedup) and orphans.
    """
    mention_to_local: dict[int, int] = {}
    for e in entities:
        for mi in e.member_idx:
            mention_to_local[mi] = e.local_id

    vf = at if valid_from is None else valid_from
    edges = []
    for r in extraction.relationships:
        s = mention_to_local.get(r.subj)
        o = mention_to_local.get(r.obj)
        if s is None or o is None or s == o:  # orphan or self-loop -> drop
            continue
        edges.append(
            {
                "subj_local": s,
                "predicate": r.predicate,
                "obj_local": o,
                "valid_from": vf,
                "valid_to": None,
                "source_refs": [],
            }
        )

    return {
        "entities": [
            {
                "local_id": e.local_id,
                "canonical_name": e.canonical_name,
                "typ": e.typ,
                "surface_names": e.surface_names,
                "record_keys": e.record_keys,
            }
            for e in entities
        ],
        "edges": edges,
        "ingested_at": at,
    }


def ingest(
    text: str,
    store,
    *,
    at: int,
    llm: LLMClient,
    valid_from: int | None = None,
    resolver: Resolver | None = None,
) -> None:
    """Extract a KG from `text` and append it to `store` (a `PyStore`)."""
    extraction = _extract(text, llm)
    entities = (resolver or _resolve)(extraction.mentions)
    batch = build_batch(extraction, entities, at=at, valid_from=valid_from)
    store.append(json.dumps(batch))
