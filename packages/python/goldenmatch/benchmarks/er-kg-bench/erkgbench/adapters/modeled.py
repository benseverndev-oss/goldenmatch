"""Faithful models of each framework's DOCUMENTED DEFAULT dedup rule.

Each adapter reproduces the exact matching logic and constants we read from the
frameworks' source (citations in every ``defaults`` string). These are models,
not the frameworks themselves -- chosen deliberately so the benchmark is:

  * reproducible (no 8 heavy installs, no API keys, no LLM non-determinism), and
  * fair (every system runs at its own published defaults on identical input).

Scope note on LLM-judge layers: Graphiti and mem0 add an LLM "are these the
same?" prompt on top of a thin deterministic guard (Graphiti: MinHash/Jaccard
>= 0.9 + exact; mem0: MD5-exact only). We model the DETERMINISTIC FLOOR each
ships -- the part that runs without paying per-pair LLM cost and that gives a
reproducible guarantee. The LLM layer is non-deterministic, O(n) in LLM calls
(Graphiti #1275 sends all nodes per new node -> token overflow / dropped
episodes), and ~$0.80 / 40 short chats (#467); it is out of scope here and
flagged in the results note rather than simulated.

Embedding-based terms (Neo4j builder cosine > 0.97; LlamaIndex cosine > 0.9)
are gated on an optional ``embed_fn``. With none supplied (the default), those
OR-terms are inactive and the adapter runs its STRING predicates only. This
understates recall on paraphrase-similar pairs -- but note the classes that
dominate this benchmark (abbreviation, synonym, cross-lingual) sit *below* a
0.9/0.97 cosine cutoff by construction, so an embedder barely moves them. Pass
``--embedder`` to activate the cosine terms and confirm this.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from rapidfuzz import fuzz, utils
from rapidfuzz.distance import Levenshtein

from .base import Record, cluster_by_key, cluster_by_pairwise

EmbedFn = Callable[[list[str]], list[list[float]]]


def _norm(s: str) -> str:
    """Lowercase + whitespace-collapse -- the normalisation these tools apply
    before exact-string bucketing (GraphRAG ``finalize_entities`` title set;
    LightRAG ``sanitize_and_normalize_extracted_text``)."""
    return " ".join(s.lower().split())


def _cosine(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    nu = sum(a * a for a in u) ** 0.5
    nv = sum(b * b for b in v) ** 0.5
    return dot / (nu * nv) if nu and nv else 0.0


# ── exact-match family ────────────────────────────────────────────────────


class _ExactNormalized:
    """Exact match on the normalised name -- the rule shared by Microsoft
    GraphRAG, LightRAG and Cognee (ontology off, the default)."""

    deterministic = True
    fidelity = "modeled"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        return cluster_by_key(records, lambda r: _norm(r.mention))


class GraphRAGModeled(_ExactNormalized):
    name = "MS-GraphRAG"
    defaults = (
        "exact title match; ER step removed "
        "(finalize_entities seen_titles set; discussion #778, data-loss #1718)"
    )


class LightRAGModeled(_ExactNormalized):
    name = "LightRAG"
    defaults = (
        "exact normalized-name dict key, no fuzzy/embedding at merge "
        "(operate.py _merge_nodes_then_upsert; #1323, cross-doc #485)"
    )


class CogneeModeled(_ExactNormalized):
    name = "Cognee"
    defaults = (
        "content-hash + exact name; difflib cutoff=0.8 only vs a user ontology "
        "(empty by default) (matching_strategies.py; #1831)"
    )


class Mem0Modeled:
    name = "mem0"
    defaults = (
        "MD5-exact only as hard dedup; semantic merge is one LLM ADD/UPDATE "
        "prompt (main.py md5; contradictions #4896, 37.6% near-dupes #4573)"
    )
    deterministic = True  # the modelled MD5 floor is; the LLM layer is not
    fidelity = "modeled"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        # MD5 is over the raw text -> case-sensitive exact match.
        return cluster_by_key(
            records, lambda r: hashlib.md5(r.mention.encode()).hexdigest()
        )


# ── Neo4j LLM Knowledge Graph Builder ─────────────────────────────────────


class Neo4jBuilderModeled:
    name = "Neo4j-KGBuilder"
    defaults = (
        "same-label gate AND ( substring-contains(len>2) OR Levenshtein<3(len>5) "
        "OR cosine>0.97 ); human-review-gated "
        "(graphDB_dataAccess.py; over-merge #1133, missed alias #912)"
    )
    deterministic = True
    fidelity = "modeled"

    # DUPLICATE_TEXT_DISTANCE default = 3 in code (README stale at 5); the
    # edit-distance rule only fires when len(name) > 5. DUPLICATE_SCORE_VALUE = 0.97.
    TEXT_DISTANCE = 3
    MIN_LEN_FOR_EDIT = 5
    MIN_LEN_FOR_CONTAINS = 2
    COSINE = 0.97

    def __init__(self, embed_fn: EmbedFn | None = None) -> None:
        self._embed_fn = embed_fn
        self._vecs: dict[int, list[float]] | None = None

    def _prep(self, records: list[Record]) -> None:
        if self._embed_fn is None:
            self._vecs = None
            return
        vs = self._embed_fn([r.mention for r in records])
        self._vecs = {r.index: v for r, v in zip(records, vs)}

    def _pred(self, a: Record, b: Record) -> bool:
        if a.entity_type != b.entity_type:  # labels(n) = labels(other)
            return False
        na, nb = _norm(a.mention), _norm(b.mention)
        if len(nb) > self.MIN_LEN_FOR_CONTAINS and nb in na:
            return True
        if len(na) > self.MIN_LEN_FOR_CONTAINS and na in nb:
            return True
        if min(len(na), len(nb)) > self.MIN_LEN_FOR_EDIT:
            if Levenshtein.distance(na, nb) < self.TEXT_DISTANCE:
                return True
        if self._vecs is not None:
            if _cosine(self._vecs[a.index], self._vecs[b.index]) > self.COSINE:
                return True
        return False

    def resolve(self, records: list[Record]) -> list[list[int]]:
        self._prep(records)
        return cluster_by_pairwise(records, self._pred)


# ── neo4j-graphrag-python similarity resolvers ────────────────────────────


class Neo4jGraphRAGFuzzyModeled:
    name = "neo4j-graphrag(fuzzy)"
    defaults = (
        "FuzzyMatchResolver: rapidfuzz WRatio/100 >= 0.8, all-pairs O(n^2) "
        "(resolver.py BasePropertySimilarityResolver; rapidfuzz extra #336)"
    )
    deterministic = True
    fidelity = "modeled"
    THRESHOLD = 0.8

    def _pred(self, a: Record, b: Record) -> bool:
        wr = fuzz.WRatio(a.mention, b.mention, processor=utils.default_process) / 100.0
        return wr >= self.THRESHOLD

    def resolve(self, records: list[Record]) -> list[list[int]]:
        return cluster_by_pairwise(records, self._pred)


# ── LlamaIndex PropertyGraphIndex dedup (Bratanic / Neo4j blog) ───────────


class LlamaIndexModeled:
    name = "LlamaIndex-PGI"
    defaults = (
        "same-label gate AND ( contains OR Levenshtein<5 OR cosine>0.9 ), "
        "KNN top-10 when embedded "
        "(property-graph blog; self-documented over-merges: 1963 AFL/NFL, "
        "BTC Halving 2020/2024)"
    )
    deterministic = True
    fidelity = "modeled"
    TEXT_DISTANCE = 5
    COSINE = 0.9

    def __init__(self, embed_fn: EmbedFn | None = None) -> None:
        self._embed_fn = embed_fn
        self._vecs: dict[int, list[float]] | None = None

    def _prep(self, records: list[Record]) -> None:
        if self._embed_fn is None:
            self._vecs = None
            return
        vs = self._embed_fn([r.mention for r in records])
        self._vecs = {r.index: v for r, v in zip(records, vs)}

    def _pred(self, a: Record, b: Record) -> bool:
        if a.entity_type != b.entity_type:  # labels(e) = labels(node)
            return False
        na, nb = _norm(a.mention), _norm(b.mention)
        if nb in na or na in nb:
            return True
        if Levenshtein.distance(na, nb) < self.TEXT_DISTANCE:
            return True
        if self._vecs is not None:
            if _cosine(self._vecs[a.index], self._vecs[b.index]) > self.COSINE:
                return True
        return False

    def resolve(self, records: list[Record]) -> list[list[int]]:
        self._prep(records)
        return cluster_by_pairwise(records, self._pred)


def all_modeled(embed_fn: EmbedFn | None = None) -> list:
    """Instantiate every modelled adapter (embedder applied where used)."""
    return [
        GraphRAGModeled(),
        LightRAGModeled(),
        CogneeModeled(),
        Mem0Modeled(),
        Neo4jBuilderModeled(embed_fn=embed_fn),
        Neo4jGraphRAGFuzzyModeled(),
        LlamaIndexModeled(embed_fn=embed_fn),
    ]
