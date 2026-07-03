"""Faithful reimplementations of the documented ER mechanisms of the leading
doc->KG tools, run on identical Track-B inputs so the comparison isolates the
ENTITY-RESOLUTION algorithm (not extraction, not the LLM, not the server).

Sourced from the 2026 landscape scan (each verified against the tool's own docs
/ paper):
  neo4j_exact   Neo4j `SinglePropertyExactMatchResolver` -- exact name match. The
                DEFAULT resolver of neo4j-graphrag-python's SimpleKGPipeline.
  neo4j_fuzzy   Neo4j `FuzzyMatchResolver` -- RapidFuzz/Levenshtein on the name.
  name_cosine   iText2KG (exact-then-cosine@0.7, text-embedding-3-large) / Neo4j
                `SpaCySemanticMatchResolver` / KGGen (S-BERT clustering) -- ALL
                cluster by embedding-cosine of the NAME STRING. Modeled here with
                a torch-free char-n-gram cosine (a faithful proxy for
                embedding-on-a-short-name, and identical on the homograph case:
                two identical surfaces -> cosine 1.0 -> merge).

EVERY one of these resolves on the SURFACE STRING alone. None uses the co-mention
neighborhood. So on homographs (identical surface, different entity) they all
merge by construction -- which is exactly what Track B measures. Running the
packaged tools end-to-end (API keys, Neo4j, torch) is a later phase; this
isolates the algorithm.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from er_utils import norm


class _UnionFind:
    def __init__(self, ids):
        self.p = {i: i for i in ids}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)

    def clusters(self) -> list[list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for i in self.p:
            groups[self.find(i)].append(i)
        return list(groups.values())


def _block(surface: str) -> str:
    n = norm(surface)
    return n.split()[-1] if n else ""


def _pairwise_merge(mentions, similar) -> list[list[str]]:
    """Union-find over within-block pairs where ``similar(a, b)`` is True."""
    ids = [m["mention_id"] for m in mentions]
    uf = _UnionFind(ids)
    blocks: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        blocks[_block(m["surface"])].append(m)
    for grp in blocks.values():
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                if similar(grp[i]["surface"], grp[j]["surface"]):
                    uf.union(grp[i]["mention_id"], grp[j]["mention_id"])
    return uf.clusters()


def predict_neo4j_exact(mentions: list[dict], **_) -> list[list[str]]:
    """Neo4j default: merge mentions with identical normalized surface."""
    groups: dict[str, list[str]] = defaultdict(list)
    for m in mentions:
        groups[norm(m["surface"])].append(m["mention_id"])
    return list(groups.values())


def predict_neo4j_fuzzy(mentions: list[dict], *, ratio: float = 0.9, **_) -> list[list[str]]:
    """Neo4j FuzzyMatchResolver: RapidFuzz ratio on the name (>= 0.9 merges)."""
    from rapidfuzz import fuzz

    def similar(a, b):
        return fuzz.ratio(norm(a), norm(b)) / 100.0 >= ratio

    return _pairwise_merge(mentions, similar)


def _char_ngrams(s: str, n: int = 3) -> Counter:
    s = f"  {norm(s)}  "
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def _cosine(c1: Counter, c2: Counter) -> float:
    common = set(c1) & set(c2)
    dot = sum(c1[g] * c2[g] for g in common)
    n1 = math.sqrt(sum(v * v for v in c1.values()))
    n2 = math.sqrt(sum(v * v for v in c2.values()))
    return dot / (n1 * n2) if n1 and n2 else 0.0


def predict_name_cosine(mentions: list[dict], *, threshold: float = 0.7, **_) -> list[list[str]]:
    """iText2KG / Neo4j-semantic / KGGen family: embedding-cosine on the NAME.
    Char-n-gram cosine is a torch-free proxy (identical on homographs: same
    surface -> cosine 1.0 -> merge)."""
    vecs = {m["mention_id"]: _char_ngrams(m["surface"]) for m in mentions}

    def similar(a, b):
        # a,b are surfaces; look up by recomputing (cheap) to keep the closure simple
        return _cosine(_char_ngrams(a), _char_ngrams(b)) >= threshold

    _ = vecs  # (kept for clarity; _pairwise_merge recomputes per pair)
    return _pairwise_merge(mentions, similar)


INCUMBENT_ENGINES = {
    "neo4j_exact": predict_neo4j_exact,
    "neo4j_fuzzy": predict_neo4j_fuzzy,
    "name_cosine": predict_name_cosine,
}
