"""Discover a RelationSchema (vocabulary + direction) from open extractions, so the
schema-constrained ingest win generalizes to corpora where the schema is unknown.

Produces the SAME RelationSchema the hand-coded `schema.default_schema` does; the
canonicalizer (`schema.canonicalize_extraction`) is consumed unchanged. Deterministic
backbone (predicate clustering + source-order/passive direction) plus one optional,
bounded, fail-open LLM consolidation call.

Design: docs/superpowers/specs/2026-06-29-goldengraph-schema-discovery-design.md
"""
from __future__ import annotations

import json
import os

from .schema import RelationSchema, _norm


def _collect_edges(extractions, sources):
    """Flatten (extraction, source_text) pairs into edge tuples
    (subj_surface, predicate, obj_surface, source_text). Out-of-range endpoints are
    dropped defensively (extraction validates already, but discovery must not crash)."""
    edges = []
    for ext, src in zip(extractions, sources):
        n = len(ext.mentions)
        for r in ext.relationships:
            if 0 <= r.subj < n and 0 <= r.obj < n:
                edges.append(
                    (ext.mentions[r.subj].name, r.predicate, ext.mentions[r.obj].name, src or "")
                )
    return edges


# ── predicate clustering (deterministic vocabulary) ──────────────────────────


def _passive_strip(p: str) -> str:
    """Normalized predicate with a leading copula and/or a trailing 'by' removed -- so
    'was acquired by' and 'acquired' share a stem for string-clustering."""
    toks = _norm(p).split()
    while toks and toks[0] in ("was", "were", "is", "are", "been", "being"):
        toks = toks[1:]
    if toks and toks[-1] == "by":
        toks = toks[:-1]
    return " ".join(toks)


def _string_close(a: str, b: str) -> bool:
    """Same relation by STRING: equal after passive-strip, or one normalized form is a
    token-substring of the other."""
    sa, sb = _passive_strip(a), _passive_strip(b)
    if sa and sa == sb:
        return True
    na, nb = _norm(a), _norm(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def _cluster_predicates(predicates, embedder, cosine_threshold: float = 0.82):
    """Union-find clustering of distinct raw predicates. Edge when `_string_close` OR
    embedding cosine >= threshold. Deterministic: predicates processed in sorted order."""
    import numpy as np

    uniq = sorted({p for p in predicates if _norm(p)})
    n = len(uniq)
    if n == 0:
        return []
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    vecs = np.asarray(embedder.embed(uniq), dtype=float)
    ok = vecs.ndim == 2 and vecs.shape[0] == n
    if ok:
        unit = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12)
        sim = unit @ unit.T
    for i in range(n):
        for j in range(i + 1, n):
            if _string_close(uniq[i], uniq[j]) or (ok and sim[i, j] >= cosine_threshold):
                union(i, j)
    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(uniq[i])
    return [sorted(g) for g in groups.values()]


# ── direction detection (source word-order + passive) ────────────────────────


def _is_passive(predicate: str) -> bool:
    toks = _norm(predicate).split()
    return bool(toks) and (
        toks[-1] == "by" or toks[0] in ("was", "were", "is", "are", "been", "being")
    )


def _source_says_reversed(subj_surface, obj_surface, source):
    """True if, in the source text, the extracted OBJECT appears before the extracted
    SUBJECT (i.e. the extraction is opposite to subject-first source order). None if a
    surface can't be located. Case-insensitive substring positions."""
    s = (source or "").lower()
    pi, oi = s.find(subj_surface.lower()), s.find(obj_surface.lower())
    if pi < 0 or oi < 0:
        return None
    return oi < pi


def _phrase_is_reverse(phrase: str, edges) -> bool:
    """A surface phrase is a REVERSE alias if it is passive, OR the majority of its edges
    are source-reversed (extracted object precedes subject in the source). Passive wins
    outright. Ties / no source signal -> forward (the conservative default)."""
    if _is_passive(phrase):
        return True
    votes = [_source_says_reversed(s, o, src) for (s, _p, o, src) in edges]
    seen = [v for v in votes if v is not None]
    if not seen:
        return False
    return sum(seen) > len(seen) / 2


# ── assemble the RelationSchema ──────────────────────────────────────────────


def _assemble_schema(clusters, edges_by_phrase) -> RelationSchema:
    relations: list[str] = []
    forward: dict = {}
    reverse: dict = {}
    for members in clusters:
        # canonical label = most frequent member by edge count, PREFERRING a non-passive member so
        # the relation name is the active form ('acquired', not 'acquired_by'); fall back to the full
        # set only if every member is passive. (tie -> shortest, then alpha)
        def _key(m):
            return (len(edges_by_phrase.get(m, ())), -len(m), tuple(-ord(c) for c in m))

        active = [m for m in members if not _is_passive(m)]
        label_phrase = max(active or members, key=_key)
        rel = _norm(label_phrase).replace(" ", "_")
        fwd, rev = set(forward.get(rel, set())), set(reverse.get(rel, set()))
        for m in members:
            (rev if _phrase_is_reverse(m, edges_by_phrase.get(m, ())) else fwd).add(_norm(m))
        fwd.add(_norm(rel))  # the canonical label is always a forward alias
        forward[rel] = frozenset(a for a in fwd if a)
        reverse[rel] = frozenset(a for a in rev if a)
        if rel not in relations:
            relations.append(rel)
    return RelationSchema(relations=tuple(relations), forward=forward, reverse=reverse)


# ── bounded LLM consolidation (optional, fail-open) ──────────────────────────


_CONSOLIDATE_PROMPT = (
    "These are candidate relation clusters discovered from a corpus, each a list of surface "
    "phrases. Merge clusters that express the SAME relation (e.g. 'acquired' and 'purchased'). "
    'Reply with ONLY JSON: {{"merge": [[i, j, ...], ...]}} listing groups of cluster INDICES to '
    "union; omit clusters that stand alone.\nClusters:\n{clusters}"
)


def _llm_consolidate(clusters, llm):
    """Union clusters the LLM says are the same relation. Deterministic post-processing of the
    parsed output; fail-open (any error -> input unchanged)."""
    if len(clusters) < 2:
        return clusters
    try:
        listing = "\n".join(f"{i}: {c}" for i, c in enumerate(clusters))
        raw = llm.complete(_CONSOLIDATE_PROMPT.format(clusters=listing))
        s = raw[raw.index("{") : raw.rindex("}") + 1]
        groups = json.loads(s).get("merge", [])
    except Exception:
        return clusters
    parent = list(range(len(clusters)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for group in groups:
        idxs = [i for i in group if isinstance(i, int) and 0 <= i < len(clusters)]
        for k in idxs[1:]:
            parent[find(k)] = find(idxs[0])
    merged: dict[int, list] = {}
    for i in range(len(clusters)):
        merged.setdefault(find(i), []).extend(clusters[i])
    return [sorted(set(c)) for c in merged.values()]


# ── public entry points ──────────────────────────────────────────────────────


def discover_schema(extractions, sources, embedder, llm=None) -> RelationSchema:
    """Discover a RelationSchema from open extractions + their source texts. Deterministic
    backbone; `llm` (optional, pinned) consolidates ambiguous clusters."""
    edges = _collect_edges(extractions, sources)
    by_phrase: dict[str, list] = {}
    for (s, p, o, src) in edges:
        by_phrase.setdefault(p, []).append((s, p, o, src))
    clusters = _cluster_predicates(list(by_phrase), embedder)
    if llm is not None:
        clusters = _llm_consolidate(clusters, llm)
    return _assemble_schema(clusters, by_phrase)


def schema_discover_enabled() -> bool:
    """`GOLDENGRAPH_SCHEMA_DISCOVER` gate (off by default). Discovery supplies the schema
    instead of `GOLDENGRAPH_RELATION_VOCAB`."""
    return os.environ.get("GOLDENGRAPH_SCHEMA_DISCOVER", "0") not in ("0", "false", "")
