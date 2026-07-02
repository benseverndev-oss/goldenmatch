"""The end-to-end path: text -> extract -> resolve -> durable store.

`ingest` wires the pipeline into SP4a's `PyStore` over the JSON `append`
boundary. `resolver` is injectable (defaults to goldenmatch-backed `resolve`) so
tests can supply a deterministic resolution without goldenmatch installed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable

import numpy as np

from .chunk_extract import chunk_extract, chunk_extract_enabled
from .extract import Extraction, Mention
from .extract import extract as _extract
from .llm import LLMClient
from .relation_reprompt import relation_reprompt, relation_reprompt_enabled
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
#: Merge threshold for the goldenprofile anti-shatter matcher (overrides the
#: engine's ScoreConfig.merge_threshold default of 0.72). The engine's hard
#: name+category GATE is what guards over-merge, so this knob mostly trades a
#: little recall for precision; env-tunable for the localize-trace sweep.
_PROFILE_MERGE_THRESHOLD = float(
    os.environ.get("GOLDENGRAPH_PROFILE_MERGE_THRESHOLD", "0.72")
)


def build_batch(
    extraction: Extraction,
    entities: list[ResolvedEntity],
    *,
    at: int,
    valid_from: int | None = None,
    source_ref: str | None = None,
) -> dict:
    """Build a `StoreBatch` dict (SP4a JSON shape) from a resolved extraction.

    Remaps each relationship's mention indices to the owning entity `local_id`;
    drops self-loops (endpoints in the same entity after dedup) and orphans.

    `source_ref` (the owning document's id) is stamped onto every edge's
    `source_refs`; the store unions+dedups these across documents (store.rs::append),
    and `query()` returns them, so a caller can recover which document each retrieved
    edge came from -- the provenance that makes supporting-fact recall measurable.
    `None` -> empty `source_refs` (back-compat: no provenance).
    """
    refs = [source_ref] if source_ref else []
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
                "source_refs": list(refs),
            }
        )

    out_entities = [
        {
            "local_id": e.local_id,
            "canonical_name": e.canonical_name,
            "typ": e.typ,
            "surface_names": e.surface_names,
            "record_keys": e.record_keys,
        }
        for e in entities
    ]

    # Literal attributes -> typed leaf nodes + edges (entity -[predicate]-> literal).
    # Represented as ordinary nodes (no store schema change) typed 'literal:<kind>';
    # the same-type + name gate in the cross-doc matcher isolates them (a literal
    # only ever clusters with an identical-value literal, which is harmless), and
    # they carry NO record_keys so they never anchor a cross-doc merge. Deduped by
    # (kind, value) within the doc so one date mentioned twice is one node.
    next_local = max((e.local_id for e in entities), default=-1) + 1
    lit_ids: dict[tuple[str, str], int] = {}
    for a in getattr(extraction, "attributes", ()):  # back-compat: absent -> no-op
        subj_local = mention_to_local.get(a.subj)
        val = (a.value or "").strip()
        if subj_local is None or not val:
            continue
        key = (a.typ, val)
        lid = lit_ids.get(key)
        if lid is None:
            lid = next_local
            next_local += 1
            lit_ids[key] = lid
            out_entities.append(
                {
                    "local_id": lid,
                    "canonical_name": val,
                    "typ": f"literal:{a.typ}",
                    "surface_names": [val],
                    "record_keys": [],
                }
            )
        edges.append(
            {
                "subj_local": subj_local,
                "predicate": a.predicate,
                "obj_local": lid,
                "valid_from": vf,
                "valid_to": None,
                "source_refs": list(refs),
            }
        )

    return {
        "entities": out_entities,
        "edges": edges,
        "ingested_at": at,
    }


def _cross_doc_link_enabled() -> bool:
    return os.environ.get("GOLDENGRAPH_CROSS_DOC_LINK", "0") not in ("0", "false", "")


def _profile_link_enabled() -> bool:
    """Use the goldenprofile Semantic Signature engine as the cross-doc matcher
    (`GOLDENGRAPH_PROFILE_LINK=1`). It replaces the ad-hoc embedding-cosine matcher
    with the anti-shatter fusion scorer: a hard name+category gate (kills the Row-4
    over-merge) plus a defining-attribute term that can only ADD confidence, never
    veto (kills the Row-3 under-merge from divergent bridge neighborhoods)."""
    return os.environ.get("GOLDENGRAPH_PROFILE_LINK", "0") not in ("0", "false", "")


def _profile_cluster(
    rows: list[dict], embedder, fp_texts: list[str | None] | None = None
) -> list[list[int]]:
    """Cluster compound feature rows with the goldenprofile engine (PR #1217).

    Each row becomes a rigid Virtual Fingerprint -- `name | category | anchor |
    attribute`. When `fp_texts[i]` is supplied (the LLM-synthesized node
    fingerprint, threaded from `ingest`), it is used verbatim -- a real
    temporal/spatial anchor + defining attribute, the full PR #1217 design.
    Otherwise the fingerprint is derived deterministically from the row: `name`/
    `type` -> the stable identity (the hard gate), graph neighborhood (`rel` +
    `nbr`) -> the defining *attribute*. That attribute placement is the point: a
    bridge entity's neighborhood DIVERGES across documents, and in the engine a
    divergent attribute is a positive-only bonus -- it cannot veto the merge the
    way the neighborhood-key matcher did. The embedder supplies the semantic
    signature (SimHash-band blocking + the category embedding escape hatch).
    Returns multi-member clusters' member indices (positional into `rows`)."""
    from .profile import UNKNOWN, Fingerprint, resolve_profiles

    if len(rows) < 2:
        return []
    fps: list[Fingerprint] = []
    for i, r in enumerate(rows):
        given = fp_texts[i] if fp_texts is not None and i < len(fp_texts) else None
        if given and given.count("|") >= 1:
            fps.append(Fingerprint("node", i, given))
            continue
        name = (r.get("name") or "").strip() or UNKNOWN
        cat = (r.get("type") or "").strip() or UNKNOWN
        attr_bits = [b for b in (r.get("rel", ""), r.get("nbr", "")) if b]
        attr = " ; ".join(attr_bits).strip() or UNKNOWN
        fps.append(Fingerprint("node", i, f"{name} | {cat} | {UNKNOWN} | {attr}"))
    config = {"scoring": {"merge_threshold": _PROFILE_MERGE_THRESHOLD}}
    try:
        res = resolve_profiles(fps, embedder=embedder, config=config)
    except Exception:
        return []
    return [list(c) for c in res.clusters if len(c) > 1]


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
    # Literal-attribute leaf nodes (typ "literal:<kind>") are values, not entities to
    # resolve -- exclude them as cross-doc link candidates so they are never embedded
    # as fingerprints (a raw value can be an empty/over-long input that 400s the
    # provider batch during the profile-link build) and never spuriously cluster.
    out_ents: list[dict] = []
    feats: list[dict] = []
    keys: list[set[str]] = []
    for e in ents:
        if str(e.get("typ", "")).startswith("literal:"):
            continue
        eid = e["entity_id"]
        typ = e.get("typ", "")
        surfaces = e.get("surface_names", ())
        out_ents.append(e)
        feats.append({
            "name": e.get("canonical_name", ""),
            "type": typ,
            "surfaces": _surface_blob(e.get("canonical_name", ""), surfaces),
            "rel": " | ".join(sorted(rel[eid])),
            "nbr": " | ".join(sorted(nbr[eid])),
        })
        keys.append({_record_key(s, typ) for s in [e.get("canonical_name", ""), *surfaces] if s})
    return out_ents, feats, keys


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
    # Exclude literal leaf nodes (typ "literal:<kind>") from the link candidate set --
    # values, not entities (mirror of `_existing_features`); keeps them out of the
    # profile-link fingerprint embedding that would otherwise 400 on a bad value.
    out_ents: list[dict] = []
    feats: list[dict] = []
    for be in new_ents:
        if str(be.get("typ", "")).startswith("literal:"):
            continue
        lid = be["local_id"]
        out_ents.append(be)
        feats.append({
            "name": be.get("canonical_name", ""),
            "type": be.get("typ", ""),
            "surfaces": _surface_blob(be.get("canonical_name", ""), be.get("surface_names", ())),
            "rel": " | ".join(sorted(rel[lid])),
            "nbr": " | ".join(sorted(nbr[lid])),
        })
    return out_ents, feats


def _assemble_fp_texts(existing, ex_keys, new_ents, new_fps, fp_index):
    """Per-row LLM fingerprint texts aligned to the combined (existing ++ new) row
    order, or None where unavailable. Existing entities are recovered from the
    persistent `record_key -> fingerprint` index (the store keeps record_keys but
    not fingerprints, so the index is how a bridge's earlier appearance carries its
    LLM fingerprint forward); new entities use this batch's freshly synthesized
    `new_fps` (keyed by `local_id`)."""
    fp_index = fp_index or {}
    new_fps = new_fps or {}
    out: list[str | None] = []
    for keys in ex_keys:
        out.append(next((fp_index[k] for k in keys if k in fp_index), None))
    for be in new_ents:
        out.append(new_fps.get(be.get("local_id")))
    return out


def _cross_doc_link(
    store, batch: dict, at: int, *, embedder=None, cluster_fn: ClusterFn | None = None,
    new_fps: dict[int, str] | None = None, fp_index: dict[str, str] | None = None,
) -> int:
    """Merge this batch's entities into EXISTING store entities judged the same, by
    injecting the existing entity's `record_keys` into the batch entity so the
    store's record_key overlap-merge (store.rs::append) unions them across documents.

    Closes the cross-document fragmentation gap: `resolve()` is per-document, but
    the durable store reconciles ACROSS documents only on exact `record_key`, so a
    bridge entity under a varied surface form ("Thomas Nabbes" vs "Nabbes") stays a
    separate node and severs the multi-hop chain. Matcher precedence: explicit
    `cluster_fn` (tests) > the goldenprofile anti-shatter engine when
    `GOLDENGRAPH_PROFILE_LINK=1` (hard name+category gate + non-vetoing attribute;
    fed the LLM-synthesized fingerprints in `new_fps`/`fp_index` when present) >
    embedding-threshold linking when an `embedder` is given (name-invariant, so
    bridges with divergent neighborhoods still match) > goldenmatch dedupe over the
    compound key. Same-type guard on injection. Returns
    the count of batch entities that gained a cross-document key. Opt-in
    (`GOLDENGRAPH_CROSS_DOC_LINK=1`)."""
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

    if cluster_fn is None:
        if _profile_link_enabled():
            fp_texts = _assemble_fp_texts(existing, ex_keys, new_ents, new_fps, fp_index)
            cluster_fn = lambda rows: _profile_cluster(rows, embedder, fp_texts)  # noqa: E731
        elif embedder is not None:
            cluster_fn = lambda rows: _embed_cluster(rows, embedder)  # noqa: E731
        else:
            cluster_fn = _gm_cluster

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


def _entity_fps(extraction: Extraction, entities, llm) -> dict[int, str]:
    """One LLM-synthesized fingerprint per RESOLVED entity, keyed by `local_id`.

    `synthesize_node_fingerprints` produces a fingerprint per MENTION; resolve
    collapses mentions into entities, so each entity takes the fingerprint of its
    representative member (the mention whose name is the entity's canonical name,
    else its first member). One LLM call per document."""
    from .profile import synthesize_node_fingerprints

    mention_fps = synthesize_node_fingerprints(extraction, llm)
    n_m = len(extraction.mentions)
    out: dict[int, str] = {}
    for e in entities:
        members = list(getattr(e, "member_idx", ()) or ())
        if not members:
            continue
        rep = next(
            (mi for mi in members
             if 0 <= mi < n_m and extraction.mentions[mi].name == e.canonical_name),
            members[0],
        )
        if 0 <= rep < len(mention_fps):
            out[e.local_id] = mention_fps[rep]
    return out


def _name_tokens(row: dict) -> set[str]:
    """Lowercased name/alias tokens of a feature row -- the blocking key for the
    incremental linker. A bridge's variant surface forms share at least one token
    ("Nabbes" in both "Nabbes" and "Thomas Nabbes"), so token-overlap is a
    recall-safe candidate filter."""
    text = row.get("surfaces") or row.get("name") or ""
    return {t for t in text.lower().replace("|", " ").split() if len(t) > 1}


class _LinkIndex:
    """Incremental, blocked index of already-committed entities for cross-document
    linking. The store-based `_cross_doc_link` rebuilds features for EVERY existing
    entity each document and hands them all to `resolve_profiles`, which JSON-
    serializes every existing fingerprint AND its embedding vector per call -- the
    O(N^2 * dim) marshaling wall that made large-N builds time out. This indexes
    committed entities by name token, so a document matches its new entities only
    against the candidate set that shares a token (where bridges live), bounding
    per-doc work to O(new + candidates) instead of O(all-existing)."""

    def __init__(self):
        self._rows: list[dict] = []
        self._keys: list[set[str]] = []
        self._fps: list[str | None] = []
        self._types: list[str] = []
        self._by_token: dict[str, list[int]] = defaultdict(list)

    def candidates(self, new_rows: list[dict]) -> list[int]:
        idxs: set[int] = set()
        for r in new_rows:
            for tok in _name_tokens(r):
                idxs.update(self._by_token.get(tok, ()))
        return sorted(idxs)

    def add(self, row: dict, keys: set[str], fp: str | None) -> None:
        i = len(self._rows)
        self._rows.append(row)
        self._keys.append(set(keys))
        self._fps.append(fp)
        self._types.append(row.get("type", ""))
        for tok in _name_tokens(row):
            self._by_token[tok].append(i)


def _cross_doc_link_incremental(batch: dict, *, embedder, new_fps, index: _LinkIndex) -> int:
    """Profile-link a batch against the incremental `index` (not the whole store):
    gather candidate existing entities sharing a name token with some new entity,
    run the goldenprofile matcher over just (candidates + new), inject the matched
    existing entity's record_keys (same-type guard) so the store's overlap-merge
    unions them, then add this batch's entities to the index under their
    (link-augmented) keys. Same merge semantics as `_cross_doc_link`, O(N) total."""
    new_ents, new_feats = _new_features(batch)
    if not new_ents:
        return 0
    cand = index.candidates(new_feats)
    linked = 0
    if cand:
        ex_feats = [index._rows[i] for i in cand]
        ex_keys = [index._keys[i] for i in cand]
        ex_types = [index._types[i] for i in cand]
        rows = list(ex_feats) + list(new_feats)
        fp_texts = [index._fps[i] for i in cand] + [
            (new_fps or {}).get(be.get("local_id")) for be in new_ents
        ]
        origin = [("E", i) for i in range(len(ex_feats))] + [
            ("N", j) for j in range(len(new_feats))
        ]
        for members in _profile_cluster(rows, embedder, fp_texts):
            exist_idx = [origin[m][1] for m in members if origin[m][0] == "E"]
            new_idx = [origin[m][1] for m in members if origin[m][0] == "N"]
            if not exist_idx or not new_idx:
                continue
            for nj in new_idx:
                be = new_ents[nj]
                be_typ = be.get("typ", "")
                shared: set[str] = set()
                for ei in exist_idx:
                    if ex_types[ei] == be_typ:  # same-type guard
                        shared |= ex_keys[ei]
                own = set(be.get("record_keys", []))
                add = shared - own
                if add:
                    be["record_keys"] = sorted(own | add)
                    linked += 1
    for j, be in enumerate(new_ents):
        index.add(new_feats[j], set(be.get("record_keys", [])),
                  (new_fps or {}).get(be.get("local_id")))
    return linked


class _BuildTimers:
    """Thread-safe accumulator of SUMMED seconds per build sub-step (extract,
    resolve, fingerprint, link, append). The summed time per step ranks the
    hotspots; wall ~ summed/concurrency for the parallel prepare steps, serial for
    commit. Opt-in via `GOLDENGRAPH_BUILD_DEBUG` -- the foundation for measured
    perf iteration instead of inferring from log-gap timestamps."""

    def __init__(self):
        self._lock = threading.Lock()
        self.secs: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)

    def add(self, key: str, secs: float) -> None:
        with self._lock:
            self.secs[key] += secs
            self.calls[key] += 1

    def report(self, *, wall: float, n_docs: int) -> str:
        with self._lock:
            rows = sorted(self.secs.items(), key=lambda kv: -kv[1])
        lines = [f"[build-debug] {n_docs} docs, wall={wall:.1f}s -- summed time per step:"]
        for k, s in rows:
            c = self.calls[k]
            lines.append(f"  {k:12s} sum={s:8.1f}s  calls={c:6d}  mean={s / max(c, 1) * 1000:7.1f}ms")
        return "\n".join(lines)


class _DistillLogger:
    """Append-only JSONL capture of (text -> extraction) and (entity -> fingerprint)
    pairs from a build -- the training data for distilling an in-house extractor /
    fingerprinter. Opt-in via `GOLDENGRAPH_DISTILL_LOG=<path>`; thread-safe append
    (the prepare phase is parallel). We already PRODUCE these pairs every build, so
    capturing them is free."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()

    def log(self, text, extraction, new_fps) -> None:
        rec = {
            "text": text,
            "entities": [
                {"name": m.name, "type": m.typ, "context": getattr(m, "context", "")}
                for m in extraction.mentions
            ],
            "relationships": [
                {"subj": r.subj, "predicate": r.predicate, "obj": r.obj}
                for r in extraction.relationships
            ],
            # Literal attributes (entity -[predicate]-> typed value). Absent from the
            # capture before this -- which made the literal/phrase-span extraction
            # (the very channel the distillation is meant to train) invisible in the
            # log. getattr keeps it back-compat for an attribute-less Extraction.
            "attributes": [
                {"subj": a.subj, "predicate": a.predicate, "value": a.value,
                 "type": a.typ}
                for a in getattr(extraction, "attributes", ())
            ],
            "fingerprints": {str(k): v for k, v in (new_fps or {}).items()},
        }
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def _maybe_canonicalize(extraction: Extraction) -> Extraction:
    """Schema-constrain + direction-canonicalize the extraction when
    `GOLDENGRAPH_SCHEMA_CANON=1` and a relation vocab is set. Snaps predicates to the
    closed schema, flips reverse-phrased edges, drops out-of-schema edges -- the
    source-side fix for the under-merge/direction defects the walk repairs stand in
    for. Fail-soft: any error returns the extraction unchanged."""
    from .schema import default_schema, schema_canon_enabled

    if not schema_canon_enabled():
        return extraction
    raw = os.environ.get("GOLDENGRAPH_RELATION_VOCAB", "")
    vocab = [v.strip() for v in raw.split(",") if v.strip()]
    if not vocab:
        return extraction
    try:
        from .schema import canonicalize_extraction

        return canonicalize_extraction(extraction, default_schema(vocab))
    except Exception:
        return extraction


def _schema_discover_enabled() -> bool:
    """`GOLDENGRAPH_SCHEMA_DISCOVER` gate. When on, `ingest_corpus` discovers the schema over the
    whole corpus (one pass) instead of reading `GOLDENGRAPH_RELATION_VOCAB`, and per-doc
    canonicalization is deferred until the discovered schema exists."""
    return os.environ.get("GOLDENGRAPH_SCHEMA_DISCOVER", "0") not in ("0", "false", "")


def _resolve_extractor():
    """Select the document extractor from `GOLDENGRAPH_EXTRACTOR` (default `api` ->
    the injected LLM extractor). `rebel`/`gliner` load a LOCAL, network-free model
    once -- the prototype-eval for replacing the per-doc LLM extraction call."""
    name = os.environ.get("GOLDENGRAPH_EXTRACTOR", "api").strip().lower()
    if name in ("", "api", "llm"):
        return None
    from . import extract_local

    if name == "rebel":
        return extract_local.rebel_extractor()
    if name == "gliner":
        return extract_local.gliner_extractor()
    raise ValueError(f"unknown GOLDENGRAPH_EXTRACTOR={name!r} (api|rebel|gliner)")


def _prepare_doc(
    text: str, llm: LLMClient, resolver: Resolver | None, *, profile_fps: bool,
    timers: _BuildTimers | None = None, embedder=None, extractor=None,
    distill: _DistillLogger | None = None,
):
    """The LLM-bound, store-INDEPENDENT half of ingest: extract + resolve (+ the
    fingerprint-synthesis LLM call when profile-linking). Pure w.r.t. the store, so
    a corpus's docs can run this concurrently before the serial commit phase --
    where the per-doc network latency (the build's dominant cost) actually lives.
    Fail-soft: a doc whose extraction errors yields an empty extraction rather than
    sinking the whole build.

    When an `embedder` is given, this also WARMS the embedding cache for the new
    fingerprints here (in the parallel phase) -- profiling showed the serial link
    phase was dominated by embedding each doc's not-yet-cached fingerprints on the
    critical path; pre-embedding moves that network cost off the serial path."""
    try:
        t0 = time.perf_counter()
        # `_extract` honors GOLDENGRAPH_LITERAL_ATTRS internally, so this call stays
        # 2-arg (custom rebel/gliner extractors and test stubs keep that shape).
        _extractor = extractor or _extract
        extraction = (
            chunk_extract(text, llm, _extractor)
            if chunk_extract_enabled()
            else _extractor(text, llm)
        )
        # Relation re-prompt (2nd pass): add relations among the already-extracted entities. Runs
        # BEFORE canonicalization so re-prompt edges get the same direction/schema snapping as
        # first-pass edges. Own try/except: a 2nd-pass failure must never discard the first-pass
        # extraction (the outer except returns an EMPTY extraction).
        if relation_reprompt_enabled():
            try:
                extraction.relationships += relation_reprompt(text, extraction.mentions, llm)
            except Exception:
                pass
        # In discovery mode the schema isn't known until the whole corpus is extracted, so defer
        # canonicalization to the post-discovery pass in `ingest_corpus`.
        if not _schema_discover_enabled():
            extraction = _maybe_canonicalize(extraction)
        if timers:
            timers.add("extract", time.perf_counter() - t0)
        t1 = time.perf_counter()
        entities = (resolver or _resolve)(extraction.mentions)
        if timers:
            timers.add("resolve", time.perf_counter() - t1)
    except Exception:
        return Extraction(mentions=[], relationships=[]), [], None
    new_fps: dict[int, str] | None = None
    if profile_fps:
        try:
            t2 = time.perf_counter()
            new_fps = _entity_fps(extraction, entities, llm)
            if timers:
                timers.add("fingerprint", time.perf_counter() - t2)
        except Exception:
            new_fps = None
        if embedder is not None and new_fps:
            try:
                t3 = time.perf_counter()
                embedder.embed(list(new_fps.values()))  # warm the cache in parallel
                if timers:
                    timers.add("pre_embed", time.perf_counter() - t3)
            except Exception:
                pass
    if distill is not None:
        try:
            distill.log(text, extraction, new_fps)
        except Exception:
            pass
    return extraction, entities, new_fps


def _commit_doc(
    store, extraction, entities, new_fps, *, at, valid_from, embedder, fp_index,
    link_index: _LinkIndex | None = None, timers: _BuildTimers | None = None,
    source_ref: str | None = None,
) -> None:
    """The store-BOUND half of ingest (must run serially, in document order):
    build the batch, cross-document-link, append, and persist this batch's
    fingerprints under their (link-augmented) record_keys for later docs. With a
    `link_index` (profile-link path) the cross-doc match runs against the O(N)
    incremental blocked index instead of re-reading the whole store each doc.

    `source_ref` (this document's id) is stamped onto every edge for provenance."""
    batch = build_batch(extraction, entities, at=at, valid_from=valid_from, source_ref=source_ref)
    if _cross_doc_link_enabled():
        tl = time.perf_counter()
        if link_index is not None and _profile_link_enabled():
            _cross_doc_link_incremental(
                batch, embedder=embedder, new_fps=new_fps, index=link_index
            )
        else:
            _cross_doc_link(
                store, batch, at, embedder=embedder, new_fps=new_fps, fp_index=fp_index
            )
        if timers:
            timers.add("link", time.perf_counter() - tl)
    ta = time.perf_counter()
    store.append(json.dumps(batch))
    if timers:
        timers.add("append", time.perf_counter() - ta)
    if fp_index is not None and new_fps:
        for be in batch.get("entities", []):
            fp = new_fps.get(be.get("local_id"))
            if not fp:
                continue
            for k in be.get("record_keys", []):
                fp_index.setdefault(k, fp)


def ingest(
    text: str,
    store,
    *,
    at: int,
    llm: LLMClient,
    valid_from: int | None = None,
    resolver: Resolver | None = None,
    embedder=None,
    fp_index: dict[str, str] | None = None,
) -> None:
    """Extract a KG from `text` and append it to `store` (a `PyStore`).

    When `GOLDENGRAPH_CROSS_DOC_LINK=1`, links this batch's entities to existing
    store entities before append. An `embedder` (if supplied) selects the
    name-invariant embedding-threshold matcher; otherwise goldenmatch dedupe. When
    `GOLDENGRAPH_PROFILE_LINK=1` the goldenprofile engine matches instead, and --
    given an `llm` and a caller-owned `fp_index` (a persistent `record_key ->
    fingerprint` map threaded across documents) -- this batch's entities are
    fingerprinted by the LLM and those fingerprints drive the merge AND are
    persisted into `fp_index` for later documents to recover."""
    profile_fps = _cross_doc_link_enabled() and _profile_link_enabled() and llm is not None
    extraction, entities, new_fps = _prepare_doc(
        text, llm, resolver, profile_fps=profile_fps
    )
    _commit_doc(
        store, extraction, entities, new_fps,
        at=at, valid_from=valid_from, embedder=embedder, fp_index=fp_index,
    )


def ingest_corpus(
    docs,
    store,
    *,
    llm: LLMClient,
    resolver: Resolver | None = None,
    embedder=None,
    fp_index: dict[str, str] | None = None,
    max_workers: int | None = None,
    doc_ids=None,
):
    """Build the KG from an ordered list of document texts. Returns the discovered `RelationSchema`
    when `GOLDENGRAPH_SCHEMA_DISCOVER=1` (so the caller can canonicalize QUERY relations through the
    SAME schema the edges were canonicalized with -- the query-side alignment), else None.

    The per-doc LLM work (extraction + fingerprint synthesis) is the build's
    dominant cost and is store-independent, so it runs CONCURRENTLY across docs
    (network-latency bound -- threads give real overlap); the store mutation
    (link + append, order-dependent) stays serial in document order, so the result
    is identical to a sequential build. `max_workers` defaults to
    `GOLDENGRAPH_BUILD_WORKERS` (8)."""
    docs = list(docs)
    # Per-document provenance ids (stamped onto every edge's source_refs). Default to the
    # positional index as a string so provenance is always present; the bench passes the real
    # corpus document ids so retrieved edges map back to gold supporting-fact ids.
    doc_ids = list(doc_ids) if doc_ids is not None else [str(i) for i in range(len(docs))]
    if max_workers is None:
        max_workers = int(os.environ.get("GOLDENGRAPH_BUILD_WORKERS", "8"))
    profile_link = _cross_doc_link_enabled() and _profile_link_enabled()
    profile_fps = profile_link and llm is not None
    # Profile-link uses the O(N) incremental blocked index; other matchers keep the
    # store-based path. One index per build, mutated only in the serial commit.
    link_index = _LinkIndex() if profile_link else None

    _dbg = os.environ.get("GOLDENGRAPH_BUILD_DEBUG", "") not in ("", "0", "false")
    timers = _BuildTimers() if _dbg else None
    t_wall = time.perf_counter()

    # Optional in-house local extractor (prototype-eval) + distillation-pair capture.
    extractor = _resolve_extractor()
    _distill_path = os.environ.get("GOLDENGRAPH_DISTILL_LOG", "")
    distill = _DistillLogger(_distill_path) if _distill_path else None

    # Pre-warm the embedding cache in the parallel prepare only when profile-linking
    # actually runs (the serial link is what consumes those embeddings).
    _prep_embedder = embedder if (profile_link and link_index is not None) else None

    def _prep(text):
        return _prepare_doc(text, llm, resolver, profile_fps=profile_fps,
                            timers=timers, embedder=_prep_embedder,
                            extractor=extractor, distill=distill)

    def _commit(i, prepared):
        extraction, entities, new_fps = prepared
        _commit_doc(store, extraction, entities, new_fps, at=i + 1, valid_from=None,
                    embedder=embedder, fp_index=fp_index, link_index=link_index,
                    timers=timers, source_ref=doc_ids[i] if i < len(doc_ids) else None)

    def _prep_all():
        if max_workers <= 1 or len(docs) <= 1:
            return [_prep(t) for t in docs]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            return list(ex.map(_prep, docs))  # map preserves input order

    discovered_schema = None
    if _schema_discover_enabled():
        # One-pass schema discovery: prepare all docs (open extraction, per-doc canonicalize already
        # skipped in `_prepare_doc`), discover the schema over the whole corpus, canonicalize each
        # extraction with it, THEN commit in document order. Resolution ran on the open mentions,
        # which canonicalization does not change. Fail-soft: discovery error -> commit the open
        # extractions (today's behavior).
        prepared = _prep_all()
        try:
            from .schema import canonicalize_extraction
            from .schema_discovery import discover_schema

            # The LLM consolidation over-merges on a weak model (measured: a 7B lumps 'acquired' and
            # 'authored' as one relation), so it is opt-IN via GOLDENGRAPH_DISCOVER_LLM=1; the
            # deterministic backbone is the default.
            _disc_llm = llm if os.environ.get("GOLDENGRAPH_DISCOVER_LLM", "0") not in ("0", "false", "") else None
            schema = discover_schema([p[0] for p in prepared], docs, embedder, _disc_llm)
            if os.environ.get("GOLDENGRAPH_SCHEMA_DISCOVER", "") not in ("", "0", "false"):
                print(f"[schema-discover] relations={list(schema.relations)}", flush=True)
                for _r in schema.relations:
                    print(f"[schema-discover]   {_r}: fwd={sorted(schema.forward[_r])[:6]} "
                          f"rev={sorted(schema.reverse[_r])[:6]}", flush=True)
            prepared = [(canonicalize_extraction(p[0], schema), p[1], p[2]) for p in prepared]
            discovered_schema = schema  # return it so the query side can canonicalize too
        except Exception as e:  # noqa: BLE001 -- discovery is best-effort
            print(f"[schema-discover] failed ({e!r}); committing open extractions", flush=True)
        for i, p in enumerate(prepared):
            _commit(i, p)
    elif max_workers <= 1 or len(docs) <= 1:
        for i, t in enumerate(docs):
            _commit(i, _prep(t))
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            # map preserves input order, so the serial commit stays in document order.
            for i, prepared in enumerate(ex.map(_prep, docs)):
                _commit(i, prepared)

    if timers is not None:
        print(timers.report(wall=time.perf_counter() - t_wall, n_docs=len(docs)), flush=True)
    return discovered_schema
