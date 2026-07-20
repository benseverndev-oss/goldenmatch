"""Argument-context relation resolution -- the Phase-2 de-risk experiment. Local, LLM-free, Modal-free:
build the GOLD typed/co-occurring edge structure, derive per-phrasing argument-context features, and
test whether resolving by that context clusters synonyms (works at == is on staff at) while keeping
distinct relations apart (acquired != authored, and the type-colliding acquired != part_of).

Spec: docs/superpowers/specs/2026-06-29-argument-context-relation-resolution-experiment-design.md
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict

from .engineered import _REL_PHRASINGS, _load_entities

#: Coarse entity types. SMALL on purpose so signatures can collide across relations.
_TYPES = ("person", "org", "place", "work", "thing")

#: Canonical (subject_type -> object_type) per relation. NOTE the DELIBERATE collision: `acquired` and
#: `part_of` are both (org, org), so the type signature alone cannot separate them -- the pair-set
#: (co-occurrence) signal must. Without a collision the experiment is trivially solved by types.
RELATION_TYPES: dict[str, tuple[str, str]] = {
    "works_at": ("person", "org"),
    "located_in": ("thing", "place"),
    "acquired": ("org", "org"),
    "authored": ("person", "work"),
    "part_of": ("org", "org"),
}


def _type_of(entity_id: str) -> str:
    """Deterministic coarse type for an entity (stable hash of its id)."""
    h = int(hashlib.sha256(entity_id.encode()).hexdigest()[:8], 16)
    return _TYPES[h % len(_TYPES)]


def build_argctx_gold(seed: int, edges_per_rel: int = 15, cooccur_frac: float = 1.0):
    """Gold edge observations: per relation, sample `edges_per_rel` (subj,obj) pairs whose endpoint
    TYPES match the relation's signature, with pairs DISJOINT across the whole corpus (distinct
    relations never share a pair). Each edge renders its relation with all phrasings when
    `cooccur_frac`>=1 (co-occurrence), else a single random phrasing. Returns list of dicts:
    {subj, obj, rel, subj_type, obj_type, phrasings}."""
    rng = random.Random(seed)
    by_type: dict[str, list[str]] = defaultdict(list)
    for e in _load_entities():
        by_type[_type_of(e.id)].append(e.id)
    used: set[tuple[str, str]] = set()
    obs: list[dict] = []
    for rel, (st, ot) in RELATION_TYPES.items():
        subs, objs = by_type.get(st, []), by_type.get(ot, [])
        if not subs or not objs:
            continue
        made, attempts = 0, 0
        while made < edges_per_rel and attempts < edges_per_rel * 50:
            attempts += 1
            s, o = rng.choice(subs), rng.choice(objs)
            if s == o or (s, o) in used:
                continue
            used.add((s, o))
            phr = list(_REL_PHRASINGS[rel])
            if rng.random() >= cooccur_frac:
                phr = [rng.choice(phr)]
            obs.append({"subj": s, "obj": o, "rel": rel,
                        "subj_type": st, "obj_type": ot, "phrasings": phr})
            made += 1
    return obs


def argctx_features(obs):
    """Per surface phrasing -> {'pairs': set[(subj,obj)], 'types': Counter[(subj_type,obj_type)]}.
    The argument-context signature the resolvers cluster on. Derived from gold; no LLM."""
    feats: dict[str, dict] = defaultdict(lambda: {"pairs": set(), "types": Counter()})
    for e in obs:
        for p in e["phrasings"]:
            feats[p]["pairs"].add((e["subj"], e["obj"]))
            feats[p]["types"][(e["subj_type"], e["obj_type"])] += 1
    return dict(feats)


def resolve_distributional(feats, jaccard_threshold: float = 0.5, use_type_blocker: bool = True):
    """Cluster phrasings by pair-set Jaccard overlap (synonyms connect the same pairs), with the
    dominant type signature as a recall-safe blocker (only compare phrasings whose type sigs match).
    `use_type_blocker=False` compares all pairs (the ablation that isolates the pair-set signal).
    Union-find; deterministic (sorted order)."""
    phrases = sorted(feats)
    parent = {p: p for p in phrases}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def sig(p):
        return feats[p]["types"].most_common(1)[0][0] if feats[p]["types"] else None

    for i, a in enumerate(phrases):
        for b in phrases[i + 1:]:
            if use_type_blocker and sig(a) != sig(b):
                continue
            pa, pb = feats[a]["pairs"], feats[b]["pairs"]
            union_ = pa | pb
            jac = len(pa & pb) / len(union_) if union_ else 0.0
            if jac >= jaccard_threshold:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for p in phrases:
        groups[find(p)].append(p)
    return [sorted(g) for g in groups.values()]


def resolve_gm(feats):
    """Relation resolution via goldenmatch dedupe with ARGUMENT-CONTEXT features (not the bare phrase):
    type signature (exact) + connected-entity-name blob (fuzzy). Fixes the impoverished-features
    problem of the earlier gm-over-strings null. Fail-open: any error -> singletons."""
    import goldenmatch as gm
    import pyarrow as pa

    phrases = sorted(feats)
    if len(phrases) < 2:
        return [[p] for p in phrases]
    rows = []
    for p in phrases:
        ts = feats[p]["types"].most_common(1)[0][0] if feats[p]["types"] else ("?", "?")
        names = sorted({n for pair in feats[p]["pairs"] for n in pair})
        rows.append({"type_sig": f"{ts[0]}>{ts[1]}", "neighbors": " | ".join(names), "phrase": p})
    df = pa.table({c: [r[c] for r in rows] for c in ("type_sig", "neighbors", "phrase")})
    try:
        result = gm.dedupe_df(df, exact=["type_sig"], fuzzy={"neighbors": 0.5},
                              confidence_required=False)
    except Exception:
        return [[p] for p in phrases]
    clusters, seen = [], set()
    for info in getattr(result, "clusters", {}).values():
        members = [phrases[int(i)] for i in info.get("members", ()) if 0 <= int(i) < len(phrases)]
        if members:
            clusters.append(sorted(set(members)))
            seen.update(members)
    clusters.extend([p] for p in phrases if p not in seen)
    return clusters


def bcubed(clusters, gold_phrase_to_relation):
    """B-cubed precision/recall of a clustering vs the gold phrase->relation map. Per phrasing:
    precision = fraction of its cluster sharing its true relation; recall = fraction of its true
    relation captured in its cluster. Returns (mean_precision, mean_recall) over phrasings present
    in BOTH the clustering and the gold map."""
    cluster_of = {p: frozenset(c) for c in clusters for p in c}
    rel_members: dict[str, set] = defaultdict(set)
    for p, r in gold_phrase_to_relation.items():
        rel_members[r].add(p)
    items = [p for p in gold_phrase_to_relation if p in cluster_of]
    if not items:
        return (0.0, 0.0)
    precs, recs = [], []
    for p in items:
        c = cluster_of[p] & set(gold_phrase_to_relation)  # ignore phrasings not in the gold map
        same = {q for q in c if gold_phrase_to_relation.get(q) == gold_phrase_to_relation[p]}
        precs.append(len(same) / len(c) if c else 0.0)
        recs.append(len(same) / len(rel_members[gold_phrase_to_relation[p]]))
    return (round(sum(precs) / len(precs), 4), round(sum(recs) / len(recs), 4))


_MUST_MERGE = ("works at", "is on staff at")
_MUST_SPLIT = [("acquired", "authored"), ("acquired", "part of")]


def must_pass_cases(clusters):
    """The (binary, metric-independent) must-pass checks: works-at synonyms merged; distinct relations
    (incl. the type-colliding acquired/part_of) kept apart."""
    cmap = {p: i for i, c in enumerate(clusters) for p in c}
    merged = _MUST_MERGE[0] in cmap and cmap.get(_MUST_MERGE[0]) == cmap.get(_MUST_MERGE[1])
    splits = {f"{a}!={b}": (a in cmap and b in cmap and cmap[a] != cmap[b]) for a, b in _MUST_SPLIT}
    ok = merged and all(splits.values())
    return ok, {"works_at_synonyms_merged": merged, "distinct_kept_apart": splits}


def _gold_map():
    """Phrasing -> relation ground truth from `_REL_PHRASINGS`; drop any phrasing claimed by >1
    relation (ambiguous truth)."""
    counts = Counter(p for ps in _REL_PHRASINGS.values() for p in ps)
    m: dict[str, str] = {}
    for rel, phrasings in _REL_PHRASINGS.items():
        for p in phrasings:
            if counts[p] == 1:
                m[p] = rel
    return m


def main():
    obs = build_argctx_gold(seed=20260629, edges_per_rel=15, cooccur_frac=1.0)
    feats = argctx_features(obs)
    gold = _gold_map()
    print(f"[setup] {len(obs)} edges, {len(feats)} distinct phrasings, {len(gold)} gold-mapped")
    for name, fn in (("distributional", resolve_distributional), ("goldenmatch", resolve_gm)):
        clusters = fn(feats)
        p, r = bcubed(clusters, gold)
        ok, detail = must_pass_cases(clusters)
        passed = p >= 0.9 and r >= 0.9 and ok
        print(f"[{name}] B-cubed P={p} R={r} | must_pass={ok} {detail} | "
              f"VERDICT={'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
