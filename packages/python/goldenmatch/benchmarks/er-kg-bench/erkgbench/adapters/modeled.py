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
    """Exact match on the normalised name -- the exact-string-bucketing family
    (Microsoft GraphRAG, LightRAG, Cognee). ``_norm`` = lowercase +
    whitespace-collapse. Base stays ``modeled``: the Phase-1 audit found ALL
    THREE frameworks diverge from ``_norm`` in their REAL normalization (see
    adapters/FIDELITY.md), each in a different way, so no per-subclass promotion
    to ``validated`` was warranted. The clustering is still exact-bucket in all
    three, but the bucket KEY differs (case direction, quote/apostrophe strip,
    internal-whitespace handling, CJK fold)."""

    deterministic = True
    fidelity = "modeled"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        return cluster_by_key(records, lambda r: _norm(r.mention))


class GraphRAGModeled(_ExactNormalized):
    name = "MS-GraphRAG"
    # AUDIT (FIDELITY.md): real key = clean_str(name.UPPER()) -> uppercases (vs
    # our lower; clustering-equivalent) BUT does NOT collapse internal whitespace
    # (our _norm does). Divergent -> stays modeled.
    defaults = (
        "exact title match; ER step removed "
        "(finalize_entities seen_titles set, title=clean_str(name.upper()); "
        "discussion #778, data-loss #1718)"
    )


class LightRAGModeled(_ExactNormalized):
    name = "LightRAG"
    # AUDIT (FIDELITY.md): real key is CASE-SENSITIVE (no .upper()/.lower() on
    # entity_name) and strips OUTER quotes + CJK-folds (normalize_extracted_info).
    # Our _norm lowercases + collapses whitespace. Divergent -> stays modeled.
    defaults = (
        "exact case-SENSITIVE name dict key, outer-quote strip + CJK fold, no "
        "fuzzy/embedding at merge "
        "(operate.py _merge_nodes_then_upsert + normalize_extracted_info; "
        "#1323, cross-doc #485)"
    )


class CogneeModeled(_ExactNormalized):
    name = "Cognee"
    # AUDIT (FIDELITY.md): real key = generate_node_name(name) = name.lower()
    # .replace("'", "") -> lowercases (matches ours) AND strips apostrophes (ours
    # does not), does NOT collapse whitespace (ours does). Default ontology is
    # empty (RDFLibOntologyResolver(ontology_file=None)) so the difflib cutoff=0.8
    # never fires. Divergent on the key -> stays modeled.
    defaults = (
        "exact generate_node_name key = name.lower().replace(\"'\",\"\"); "
        "difflib cutoff=0.8 only vs a user ontology (empty by default) "
        "(generate_node_name.py / matching_strategies.py; #1831)"
    )


class Mem0Modeled:
    name = "mem0"
    # AUDIT (FIDELITY.md): CONFIRMED line-by-line. memory/main.py computes
    #   mem_hash = hashlib.md5(text.encode()).hexdigest()        (_add_to_vector_store)
    #   new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()  (_create_memory)
    # over the RAW memory text, case-sensitive, no normalization -- byte-identical
    # to our model's md5(r.mention.encode()).hexdigest(). The deterministic MD5
    # FLOOR is `validated`; the LLM ADD/UPDATE semantic-merge layer is OUT OF
    # SCOPE (Phase 3) and not modeled here.
    defaults = (
        "MD5-exact only as hard dedup; semantic merge is one LLM ADD/UPDATE "
        "prompt (memory/main.py md5 over raw text; contradictions #4896, "
        "37.6% near-dupes #4573)"
    )
    deterministic = True  # the modelled MD5 floor is; the LLM layer is not
    fidelity = "validated"  # MD5 floor confirmed vs source; LLM layer out of scope

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
        "(graphDB_dataAccess.py get_duplicate_nodes Cypher; over-merge #1133, "
        "missed alias #912)"
    )
    deterministic = True
    # AUDIT (FIDELITY.md): string predicates + constants CONFIRMED vs the real
    # Cypher WHERE clause:
    #   labels(n)=labels(other)  (same-label gate)
    #   size(other.id)>2 AND toLower(n.id) CONTAINS toLower(other.id)  (+reverse)
    #   size(n.id)>5  AND apoc.text.distance(toLower,toLower) < $duplicate_text_distance
    #   vector.similarity.cosine(...) > $duplicate_score_value
    # Defaults: DUPLICATE_TEXT_DISTANCE=3 (README stale at 5), DUPLICATE_SCORE_VALUE=0.97.
    # apoc.text.distance == Levenshtein. BUT our run does NOT reproduce the real
    # DEFAULT rule, on two counts (see FIDELITY.md):
    #   (a) the cosine OR-term needs an embedder; the real query pre-filters
    #       `n.embedding IS NOT NULL` (the builder embeds nodes at ingest), so the
    #       cosine branch is part of the real default -- the no-embedder string-only
    #       row fires only 2 of the 3 OR-branches -> a PARTIAL rule. Phase 2 adds an
    #       embedder variant (Neo4j-KGBuilder(emb), emb_modeled()) that DOES fire all
    #       3 branches -- but it STILL stays modeled, because of (b).
    #   (b) the edit-distance length guard is IRREPRODUCIBLE by our pairwise model.
    #       Phase-2 source check (VERIFIED verbatim at
    #       github.com/neo4j-labs/llm-graph-builder@4a412f46
    #       backend/src/graphDB_dataAccess.py get_duplicate_nodes_list L417-444): the
    #       guard is `size(toString(n.id))>5` and each pair is oriented by
    #       `WHERE elementId(n) < elementId(other)`, so the guard tests the
    #       SMALLER-elementId node -- an arbitrary INSERTION-ORDER side, unrelated to
    #       string length. The effective rule is neither min(len)>5 (under-fires) nor
    #       max(len)>5 (over-fires); it is order-dependent on Neo4j-internal elementId,
    #       which no commutative pairwise predicate can reproduce and which the
    #       benchmark's record order cannot be guaranteed to match. We keep our
    #       conservative two-sided min(len)>5 and record the divergence rather than
    #       trade it for an equally-wrong max(len)>5.
    # Constants + string-predicates are source-confirmed, but (a)+(b) mean neither the
    # string-only NOR the embedder variant is a faithful reproduction -> both stay
    # `modeled` (only mem0's byte-identical, cleanly-scoped floor earns `validated`).
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
    # AUDIT (FIDELITY.md): the per-pair predicate here is byte-identical to the
    # library's real compute_similarity (fuzz.WRatio(.., processor=
    # utils.default_process)/100.0, threshold 0.8). BUT this model still DIVERGES
    # from a real in-process run on THIS corpus: real `neo4j-graphrag(fuzzy)*`
    # (real-inproc, runs compute_similarity + _consolidate_sets grouped per
    # entity-label) measures F1 0.470, while this MODEL measures F1 0.403
    # (-6.7pp). Kept as `modeled` (DIVERGENT) for the model-vs-real contrast; the
    # real run ships as the separate `neo4j-graphrag(fuzzy)*` row.
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
    # AUDIT (FIDELITY.md): COULD NOT CONFIRM against maintained source. The
    # constants (TEXT_DISTANCE=5, COSINE=0.9) and the (contains OR Levenshtein OR
    # cosine) Cypher come from a Neo4j/Bratanic *blog*, not pinnable library code.
    # run-llama/llama_index core ships NO automatic fuzzy entity-dedup default
    # (no apoc.text.distance / edit_distance in the property_graph code; library
    # default is exact name+label upsert at the graph store). So this model both
    # (a) can't pin its constants to maintained source and (b) likely OVER-states
    # the library default. Stays `modeled` for lack of confirmation.
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


def emb_modeled(embed_fn: EmbedFn) -> list:
    """The embedding-activated variants, run ALONGSIDE the string-only rows so the
    board SHOWS the embedder's effect instead of silently replacing committed numbers.

    Both stay `modeled` -- an embedder activates their cosine OR-term but fixes
    neither row's fidelity gap:
      * Neo4j-KGBuilder(emb) now fires all 3 OR-branches (contains, edit-distance,
        AND the cosine>0.97 term the real builder embeds nodes for at ingest), but its
        edit-distance length guard is IRREPRODUCIBLE (real Cypher guards the
        smaller-elementId node, an arbitrary insertion-order side; see
        Neo4jBuilderModeled audit + FIDELITY.md) -> stays modeled, NOT validated.
      * LlamaIndex-PGI(emb) activates cosine>0.9 + the KNN-style term, but its rule is
        blog-sourced/unconfirmable against maintained library code -- a provenance gap
        an embedder does not close -> stays modeled.

    Instance attrs shadow the class `name`/`fidelity` so the board carries distinct
    rows without subclassing.
    """
    kg = Neo4jBuilderModeled(embed_fn=embed_fn)
    kg.name = "Neo4j-KGBuilder(emb)"
    kg.fidelity = "modeled"
    li = LlamaIndexModeled(embed_fn=embed_fn)
    li.name = "LlamaIndex-PGI(emb)"
    li.fidelity = "modeled"
    return [kg, li]
