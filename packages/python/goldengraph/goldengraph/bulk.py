"""SP2: load a PRE-STRUCTURED knowledge base straight into the store, bypassing
extract/resolve/link. STaRK-style KBs come with canonical node ids already, so
each node's id IS its resolution: a unique ``record_key`` per node means the
store's overlap-merge mints a fresh stable id for each with zero merges (see
``goldengraph-core/src/store.rs::append``). Edges are co-batched with their
endpoints because the store panics on an edge whose endpoint is absent from the
same batch.

See docs/superpowers/specs/2026-07-02-goldengraph-stark-bulkload-design.md.
"""
from __future__ import annotations

import json
from collections.abc import Iterable


def _entity(local_id: int, stark_id: str, name: str, typ: str) -> dict:
    # record_keys=[stark_id] -> unique -> passthrough (no merge). source_refs=[stark_id]
    # -> the stark id rides through as_of so retrieval can translate view-local ids back.
    return {
        "local_id": local_id,
        "canonical_name": name,
        "typ": typ,
        "surface_names": [name],
        "record_keys": [stark_id],
        "source_refs": [stark_id],
    }


def _edge(subj_local: int, predicate: str, obj_local: int, at: int) -> dict:
    return {
        "subj_local": subj_local,
        "predicate": predicate,
        "obj_local": obj_local,
        "valid_from": at,
        "valid_to": None,
        "source_refs": [],
    }


def bulk_load(
    store,
    nodes: Iterable,
    edges: Iterable,
    *,
    at: int = 1,
    chunk_edges: int | None = None,
) -> dict:
    """Load ``(nodes, edges)`` into ``store``. ``nodes``: iterable of
    ``(stark_id, name, typ)``; ``edges``: iterable of
    ``(subj_stark_id, predicate, obj_stark_id)``. Returns
    ``{n_nodes, n_edges, n_dropped_edges, n_batches}``. See module docstring / spec."""
    node_list = list(nodes)
    id_to_local: dict[str, int] = {}
    entities: list[dict] = []
    for i, (stark_id, name, typ) in enumerate(node_list):
        id_to_local[str(stark_id)] = i
        entities.append(_entity(i, str(stark_id), name, typ))

    edge_dicts: list[dict] = []
    dropped = 0
    for subj, predicate, obj in edges:
        s = id_to_local.get(str(subj))
        o = id_to_local.get(str(obj))
        if s is None or o is None:
            dropped += 1
            continue
        edge_dicts.append(_edge(s, predicate, o, at))

    if chunk_edges is None:
        n_batches = _append_single(store, entities, edge_dicts, at)
    else:
        n_batches = _append_chunked(store, entities, edge_dicts, at, chunk_edges)

    return {
        "n_nodes": len(entities),
        "n_edges": len(edge_dicts),
        "n_dropped_edges": dropped,
        "n_batches": n_batches,
    }


def _append_single(store, entities: list[dict], edges: list[dict], at: int) -> int:
    store.append(json.dumps({"entities": entities, "edges": edges, "ingested_at": at}))
    return 1


def _append_chunked(store, entities: list[dict], edges: list[dict], at: int, chunk: int) -> int:
    """Initial nodes-ONLY batch mints every stable id; then edge batches, each
    re-listing ONLY the endpoint entities it references so the store's overlap-merge
    (record_keys=[stark_id]) re-resolves them to the already-minted id (single
    inheritor -> no merge, no new mint). Bounds peak JSON size vs one giant batch."""
    if chunk < 1:
        raise ValueError(f"chunk_edges must be >= 1, got {chunk}")
    by_local = {e["local_id"]: e for e in entities}
    store.append(json.dumps({"entities": entities, "edges": [], "ingested_at": at}))
    n_batches = 1
    for start in range(0, len(edges), chunk):
        window = edges[start : start + chunk]
        needed = {e["subj_local"] for e in window} | {e["obj_local"] for e in window}
        batch_entities = [by_local[lid] for lid in sorted(needed)]
        store.append(json.dumps({"entities": batch_entities, "edges": window, "ingested_at": at}))
        n_batches += 1
    return n_batches
