"""SP-moat: fragment gold entities into k alias nodes (variant names, split doc +
edges) so dense retrieval degrades and only a real resolver recovers it. Pure +
seeded (reproducible); no store, no goldenmatch here.

See docs/superpowers/specs/2026-07-02-goldengraph-stark-alias-moat-design.md.
"""
from __future__ import annotations

import random


def _variants(name: str, k: int, *, seed: int) -> list[str]:
    """k DISTINCT surface forms of `name` (abbreviation / word-order / truncation /
    punctuation-drop). Distinctness is load-bearing: if two variants collide, the
    ad-hoc exact-match baseline would merge them for free and rig the comparison.
    Falls back to a numbered suffix to guarantee k distinct forms."""
    rng = random.Random(f"{seed}:{name}")
    toks = name.split()
    cands: list[str] = []

    def _add(v: str) -> None:
        v = v.strip()
        if v and v not in cands:
            cands.append(v)

    _add(name)
    if len(toks) > 1:
        _add(" ".join(reversed(toks)))                       # word-order
        _add("".join(t[0].upper() for t in toks))            # abbreviation/initials
        _add(" ".join(toks[:-1]))                            # truncation
    _add(name.replace("-", " ").replace(",", ""))            # punctuation-drop
    _add(name.lower())
    rng.shuffle(cands)
    out = cands[:k]
    j = 0
    while len(out) < k:                                       # guarantee k distinct
        cand = f"{name} ({j})"
        if cand not in out:
            out.append(cand)
        j += 1
    return out


def _sentences(doc: str) -> list[str]:
    return [s.strip() for s in doc.split(". ") if s.strip()]


def inject_aliases(nodes, node_texts, edges, target_ids, *, k: int = 3, seed: int = 0):
    """Fragment each entity in `target_ids` into k alias nodes. Returns
    (nodes2, node_texts2, edges2, canon). See the spec / module docstring."""
    target_ids = set(target_ids)
    text_of = dict(zip([n[0] for n in nodes], node_texts))

    # alias ids + variant names per target; canon maps every id -> original
    alias_ids: dict[str, list[str]] = {}
    canon: dict[str, str] = {}
    nodes2: list[tuple] = []
    texts2: list[str] = []
    for nid, name, typ in nodes:
        if nid not in target_ids:
            nodes2.append((nid, name, typ))
            texts2.append(text_of[nid])
            canon[nid] = nid
            continue
        vs = _variants(name, k, seed=seed)
        ids = [f"{nid}#a{j}" for j in range(k)]
        alias_ids[nid] = ids
        sents = _sentences(text_of.get(nid, "")) or [name]
        buckets: list[list[str]] = [[] for _ in range(k)]
        for i, s in enumerate(sents):
            buckets[i % k].append(s)                          # round-robin sentences
        for j, aid in enumerate(ids):
            nodes2.append((aid, vs[j], typ))
            texts2.append(". ".join(buckets[j]))
            canon[aid] = nid

    # remap edges: an endpoint that is a target -> one of its aliases (round-robin by
    # a per-target counter so its incident edges spread across aliases)
    ctr: dict[str, int] = {}

    def _remap(endpoint: str) -> str:
        if endpoint not in target_ids:
            return endpoint
        ids = alias_ids[endpoint]
        c = ctr.get(endpoint, 0)
        ctr[endpoint] = c + 1
        return ids[c % len(ids)]

    edges2 = [(_remap(s), p, _remap(o)) for (s, p, o) in edges]
    return nodes2, texts2, edges2, canon
