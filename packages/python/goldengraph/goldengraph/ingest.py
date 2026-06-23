"""The end-to-end path: text -> extract -> resolve -> durable store.

`ingest` wires the pipeline into SP4a's `PyStore` over the JSON `append`
boundary. `resolver` is injectable (defaults to goldenmatch-backed `resolve`) so
tests can supply a deterministic resolution without goldenmatch installed.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable

import numpy as np

from .extract import Extraction, Mention
from .extract import extract as _extract
from .llm import LLMClient
from .resolve import ResolvedEntity, _record_key
from .resolve import resolve as _resolve

Resolver = Callable[[list[Mention]], list[ResolvedEntity]]
#: Cross-document matcher: given a list of compound feature rows (dicts with
#: name/type/surfaces/rel/nbr), return the member-index lists of multi-member
#: clusters. Default picks embedding-threshold linking when an embedder is
#: available (invariant across a bridge's appearances), else goldenmatch dedupe;
#: injectable so the linking logic is testable without either.
ClusterFn = Callable[[list[dict]], list[list[int]]]
#: Feature columns of the compound match key fed to goldenmatch. name+type alone
#: is near-unique (goldenmatch commits a low-precision RED config and over-merges,
#: measured); the GRAPH-NEIGHBORHOOD columns (incident predicates + neighbor names)
#: give it disambiguation signal -- but a bridge entity's neighborhood DIVERGES
#: across paragraphs (it connects different things), which suppresses the very
#: merge we want, so the embedding path (name-invariant) is preferred when present.
_FEATURE_COLS = ("name", "type", "surfaces", "rel", "nbr")
#: Cosine cutoff for embedding-threshold cross-doc linking. High enough to merge
#: surface variants ("Thomas Nabbes" ~ "Nabbes") without conflating distinct
#: entities; env-tunable for the A/B sweep on the localize trace.
_LINK_THRESHOLD = float(os.environ.get("GOLDENGRAPH_LINK_THRESHOLD", "0.82"))


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


def _cross_doc_link_enabled() -> bool:
    return os.environ.get("GOLDENGRAPH_CROSS_DOC_LINK", "0") not in ("0", "false", "")


def _gm_cluster(rows: list[dict]) -> list[list[int]]:
    """Cluster compound feature rows with goldenmatch's zero-config dedupe -- the
    SAME calibrated resolver `resolve()` uses within a document, but over the
    multi-column compound key (`_FEATURE_COLS`) so the controller has real signal
    instead of a near-unique name. Returns multi-member clusters' member indices.
    goldenmatch + polars import lazily so the package (and injected-matcher tests)
    need neither."""
    import goldenmatch as gm
    import polars as pl

    if not rows:
        return []
    df = pl.DataFrame({c: [r.get(c, "") for r in rows] for c in _FEATURE_COLS})
    result = gm.dedupe_df(df)
    out: list[list[int]] = []
    for info in result.clusters.values():
        members = [int(x) for x in info["members"]]
        if len(members) > 1:
            out.append(members)
    return out


def _embed_cluster(rows: list[dict], embedder, threshold: float = _LINK_THRESHOLD) -> list[list[int]]:
    """Cluster compound rows by EMBEDDING cosine -- name-invariant signal, so a
    bridge entity's two appearances ("Thomas Nabbes" / "Nabbes") match regardless
    of their divergent neighborhoods (the failure of the neighborhood-key path).
    Same-type pairs at cosine >= `threshold` are unioned. Embeds the alias blob
    (canonical + surface forms). Returns multi-member clusters' member indices."""
    n = len(rows)
    if n < 2:
        return []
    texts = [r.get("surfaces") or r.get("name", "") for r in rows]
    vecs = np.asarray(embedder.embed(texts), dtype=float)
    if vecs.ndim != 2 or vecs.shape[0] != n:
        return []
    unit = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12)
    sim = unit @ unit.T
    types = [r.get("type", "") for r in rows]
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if types[i] == types[j] and sim[i, j] >= threshold:
                parent[_find(i)] = _find(j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[_find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def _surface_blob(canonical: str, surfaces) -> str:
    return " | ".join(sorted({s for s in [canonical, *surfaces] if s}))


def _existing_features(slice_graph):
    """(entities, feature-rows, record-key-sets) for every stored entity, with the
    entity's 1-hop neighborhood (incident predicates + neighbor canonical names)
    folded into the compound key. One `query(all_ids, 1)` gets the full edge set."""
    ents = list(slice_graph.entities())
    id_to_name = {e["entity_id"]: e.get("canonical_name", "") for e in ents}
    ids = list(id_to_name)
    edges = slice_graph.query(ids, 1).get("edges", []) if ids else []
    rel: dict[int, set[str]] = defaultdict(set)
    nbr: dict[int, set[str]] = defaultdict(set)
    for ed in edges:
        s, p, o = ed.get("subj"), ed.get("predicate", ""), ed.get("obj")
        if s in id_to_name and o in id_to_name:
            rel[s].add(p)
            rel[o].add(p)
            nbr[s].add(id_to_name[o])
            nbr[o].add(id_to_name[s])
    feats: list[dict] = []
    keys: list[set[str]] = []
    for e in ents:
        eid = e["entity_id"]
        typ = e.get("typ", "")
        surfaces = e.get("surface_names", ())
        feats.append({
            "name": e.get("canonical_name", ""),
            "type": typ,
            "surfaces": _surface_blob(e.get("canonical_name", ""), surfaces),
            "rel": " | ".join(sorted(rel[eid])),
            "nbr": " | ".join(sorted(nbr[eid])),
        })
        keys.append({_record_key(s, typ) for s in [e.get("canonical_name", ""), *surfaces] if s})
    return ents, feats, keys


def _new_features(batch: dict):
    """(entities, feature-rows) for this batch, neighborhood from the batch edges
    (which reference entity `local_id`s)."""
    new_ents = batch.get("entities", [])
    lid_to_name = {be["local_id"]: be.get("canonical_name", "") for be in new_ents}
    rel: dict[int, set[str]] = defaultdict(set)
    nbr: dict[int, set[str]] = defaultdict(set)
    for ed in batch.get("edges", []):
        s, p, o = ed.get("subj_local"), ed.get("predicate", ""), ed.get("obj_local")
        if s in lid_to_name and o in lid_to_name:
            rel[s].add(p)
            rel[o].add(p)
            nbr[s].add(lid_to_name[o])
            nbr[o].add(lid_to_name[s])
    feats: list[dict] = []
    for be in new_ents:
        lid = be["local_id"]
        feats.append({
            "name": be.get("canonical_name", ""),
            "type": be.get("typ", ""),
            "surfaces": _surface_blob(be.get("canonical_name", ""), be.get("surface_names", ())),
            "rel": " | ".join(sorted(rel[lid])),
            "nbr": " | ".join(sorted(nbr[lid])),
        })
    return new_ents, feats


def _cross_doc_link(
    store, batch: dict, at: int, *, embedder=None, cluster_fn: ClusterFn | None = None
) -> int:
    """Merge this batch's entities into EXISTING store entities judged the same, by
    injecting the existing entity's `record_keys` into the batch entity so the
    store's record_key overlap-merge (store.rs::append) unions them across documents.

    Closes the cross-document fragmentation gap: `resolve()` is per-document, but
    the durable store reconciles ACROSS documents only on exact `record_key`, so a
    bridge entity under a varied surface form ("Thomas Nabbes" vs "Nabbes") stays a
    separate node and severs the multi-hop chain. Matcher precedence: explicit
    `cluster_fn` (tests) > embedding-threshold linking when an `embedder` is given
    (name-invariant, so bridges with divergent neighborhoods still match) >
    goldenmatch dedupe over the compound key. Same-type guard on injection. Returns
    the count of batch entities that gained a cross-document key. Opt-in
    (`GOLDENGRAPH_CROSS_DOC_LINK=1`)."""
    if cluster_fn is None:
        cluster_fn = (
            (lambda rows: _embed_cluster(rows, embedder))
            if embedder is not None
            else _gm_cluster
        )
    if not hasattr(store, "as_of"):
        return 0
    try:
        slice_graph = store.as_of(at, at)
        existing, ex_feats, ex_keys = _existing_features(slice_graph)
    except Exception:
        return 0
    new_ents, new_feats = _new_features(batch)
    if not existing or not new_ents:
        return 0

    # Combined rows: existing first, then batch. origin maps a row back to its side.
    rows: list[dict] = list(ex_feats) + list(new_feats)
    origin: list[tuple[str, int]] = (
        [("E", i) for i in range(len(ex_feats))] + [("N", j) for j in range(len(new_feats))]
    )

    linked = 0
    for members in cluster_fn(rows):
        exist_idx = [origin[m][1] for m in members if origin[m][0] == "E"]
        new_idx = [origin[m][1] for m in members if origin[m][0] == "N"]
        if not exist_idx or not new_idx:
            continue
        for nj in new_idx:
            be = new_ents[nj]
            be_typ = be.get("typ", "")
            shared: set[str] = set()
            for ei in exist_idx:
                if existing[ei].get("typ", "") == be_typ:  # same-type guard
                    shared |= ex_keys[ei]
            own = set(be.get("record_keys", []))
            add = shared - own
            if add:
                be["record_keys"] = sorted(own | add)
                linked += 1
    return linked


def ingest(
    text: str,
    store,
    *,
    at: int,
    llm: LLMClient,
    valid_from: int | None = None,
    resolver: Resolver | None = None,
    embedder=None,
) -> None:
    """Extract a KG from `text` and append it to `store` (a `PyStore`).

    When `GOLDENGRAPH_CROSS_DOC_LINK=1`, links this batch's entities to existing
    store entities before append. An `embedder` (if supplied) selects the
    name-invariant embedding-threshold matcher; otherwise goldenmatch dedupe."""
    extraction = _extract(text, llm)
    entities = (resolver or _resolve)(extraction.mentions)
    batch = build_batch(extraction, entities, at=at, valid_from=valid_from)
    if _cross_doc_link_enabled():
        _cross_doc_link(store, batch, at, embedder=embedder)
    store.append(json.dumps(batch))
