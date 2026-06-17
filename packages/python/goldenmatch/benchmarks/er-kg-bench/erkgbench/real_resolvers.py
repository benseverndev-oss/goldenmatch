"""Run neo4j-graphrag-python's REAL resolver decision code, no live Neo4j.

The real FuzzyMatchResolver needs Neo4j+APOC end-to-end, but the CLUSTERING is
decided purely by the library's own `compute_similarity` (rapidfuzz WRatio/100) and
`_consolidate_sets`, grouped by node label. Neo4j only persists the merge; it cannot
change which nodes merge. So we call those real methods over the corpus (grouped by
entity_type = label). neo4j-graphrag is imported lazily so this module stays
importable (and unit-testable) without the dependency.

NOTE on `_consolidate_sets`: it is a SINGLE-PASS consolidation -- a pair bridging two
already-separate sets merges into only the first, leaving the two OVERLAPPING (sharing
a record). The real resolver feeds those sets to sequential Neo4j merges that
transitively collapse the shared record into one entity; we reproduce that disjoint
end-state with `_merge_overlapping` so the result is a valid PARTITION (a record can't
belong to two entities). It is a no-op when no overlap exists (the sparse fuzzy graph),
so it leaves the fuzzy number essentially unchanged while making the denser spaCy graph
well-formed.

SinglePropertyExactMatchResolver: groups by entity label then merges records whose
`name` property (= mention in our corpus) is exactly equal AND non-null. The Cypher
query in the real resolver reads:
    WITH entity, entity.name as prop
    WITH entity, prop WHERE prop IS NOT NULL
    UNWIND labels(entity) as lab
    WITH lab, prop, entity WHERE NOT lab IN ['__Entity__', '__KGBuilder__']
    WITH prop, lab, collect(entity) AS entities
i.e. exact string equality on the raw `name` value, per-label, skipping null/missing.
No normalization is applied — the stored `name` is compared as-is.
"""
from __future__ import annotations

import html
import re
from itertools import combinations
from uuid import NAMESPACE_OID, uuid5


def _merge_overlapping(sets: list[set[int]]) -> list[set[int]]:
    """Union overlapping sets into disjoint sets (union-find over set memberships).

    `_consolidate_sets` is single-pass and can leave overlapping output sets (see the
    module docstring); merging them here yields the disjoint partition the real
    resolver's sequential graph-merges produce. No-op when the input is already
    disjoint.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in sets:
        members = list(s)
        for m in members[1:]:
            ra, rb = find(members[0]), find(m)
            if ra != rb:
                parent[rb] = ra

    groups: dict[int, set[int]] = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    return list(groups.values())


def neo4j_graphrag_fuzzy_clusters(items: list[tuple[int, str, str]]) -> list[list[int]]:
    """items: (record_id, mention, entity_type). Returns clusters of record_ids
    (full partition incl. singletons), via the real FuzzyMatchResolver methods."""
    from unittest.mock import MagicMock

    from neo4j_graphrag.experimental.components.resolver import (  # pyright: ignore[reportMissingImports]
        FuzzyMatchResolver,
    )

    resolver = FuzzyMatchResolver(driver=MagicMock())  # driver is I/O only, unused for clustering
    threshold = resolver.similarity_threshold  # library default 0.8

    mention = {rid: m for rid, m, _t in items}
    groups: dict[str, list[int]] = {}
    for rid, _m, t in items:
        groups.setdefault(t, []).append(rid)

    clusters: list[list[int]] = []
    for ids in groups.values():
        # Faithful to BasePropertySimilarityResolver.run: skip empty combined_text.
        usable = [i for i in ids if mention[i] and str(mention[i]).strip()]
        pairs: list[set[int]] = []
        for i, j in combinations(usable, 2):
            if resolver.compute_similarity(mention[i], mention[j]) >= threshold:
                pairs.append({i, j})
        # Library's REAL consolidation, then merge its single-pass overlaps into a
        # valid partition (the real resolver's graph-merges collapse them; see module
        # docstring + _merge_overlapping).
        merged = _merge_overlapping(resolver._consolidate_sets(pairs))
        seen: set[int] = set()
        for s in merged:
            clusters.append(sorted(s))
            seen |= s
        clusters.extend([i] for i in ids if i not in seen)  # singletons (incl skipped-empty)
    return clusters


def neo4j_graphrag_exact_clusters(items: list[tuple[int, str, str]]) -> list[list[int]]:
    """In-process model of SinglePropertyExactMatchResolver.

    items: (record_id, mention, entity_type).

    The real resolver groups entities by label, then merges those with the same
    `name` property value (exact equality, no normalization). Entities with a
    null/empty `name` are skipped (WHERE prop IS NOT NULL in the Cypher query).
    Returns a full partition (multi-member clusters + singletons).
    """
    mention = {rid: m for rid, m, _t in items}
    groups: dict[str, list[int]] = {}
    for rid, _m, t in items:
        groups.setdefault(t, []).append(rid)

    clusters: list[list[int]] = []
    for ids in groups.values():
        # Faithful to the real resolver: skip records where name is null/empty.
        usable = [i for i in ids if mention[i] and str(mention[i]).strip()]
        # Group by exact name value (no normalization -- the real resolver stores
        # and compares the `name` property as-is).
        by_name: dict[str, list[int]] = {}
        for rid in usable:
            by_name.setdefault(mention[rid], []).append(rid)
        seen: set[int] = set()
        for name_ids in by_name.values():
            clusters.append(sorted(name_ids))
            seen.update(name_ids)
        # Singletons: usable records that had a unique name + skipped-empty records.
        clusters.extend([i] for i in ids if i not in seen)
    return clusters


# The library default for SpaCySemanticMatchResolver.__init__(spacy_model=...)
# (verified v1.17.0: en_core_web_lg, similarity_threshold=0.8). Faithful = the
# library default; CI provisions it via `python -m spacy download en_core_web_lg`.
SPACY_MODEL = "en_core_web_lg"


def neo4j_graphrag_spacy_clusters(items: list[tuple[int, str, str]]) -> list[list[int]]:
    """Run neo4j-graphrag's REAL SpaCySemanticMatchResolver decision code.

    items: (record_id, mention, entity_type). Like FuzzyMatchResolver, the spaCy
    resolver subclasses BasePropertySimilarityResolver and exposes a callable
    `compute_similarity` (spaCy doc-vector cosine) + `_consolidate_sets`; the
    clustering is decided by those methods, grouped per entity-label (Neo4j+APOC
    only persists the merge). spaCy + its vector model are imported lazily so this
    module stays importable without the dependency.

    auto_download_spacy_model=False: a MISSING model must RAISE here (the registry
    then degrades the row to "skipped"), never trigger an implicit ~560MB download
    mid-run. CI installs the model explicitly.
    """
    from unittest.mock import MagicMock

    from neo4j_graphrag.experimental.components.resolver import (  # pyright: ignore[reportMissingImports]
        SpaCySemanticMatchResolver,
    )

    resolver = SpaCySemanticMatchResolver(
        driver=MagicMock(),  # driver is I/O only, unused for clustering
        spacy_model=SPACY_MODEL,
        auto_download_spacy_model=False,
    )
    threshold = resolver.similarity_threshold  # library default 0.8

    mention = {rid: m for rid, m, _t in items}
    groups: dict[str, list[int]] = {}
    for rid, _m, t in items:
        groups.setdefault(t, []).append(rid)

    clusters: list[list[int]] = []
    for ids in groups.values():
        # Faithful to BasePropertySimilarityResolver.run: skip empty combined_text.
        usable = [i for i in ids if mention[i] and str(mention[i]).strip()]
        pairs: list[set[int]] = []
        for i, j in combinations(usable, 2):
            if resolver.compute_similarity(mention[i], mention[j]) >= threshold:
                pairs.append({i, j})
        # Library's REAL consolidation, then merge its single-pass overlaps into a
        # valid partition (the real resolver's graph-merges collapse them; see module
        # docstring + _merge_overlapping).
        merged = _merge_overlapping(resolver._consolidate_sets(pairs))
        seen: set[int] = set()
        for s in merged:
            clusters.append(sorted(s))
            seen |= s
        clusters.extend([i] for i in ids if i not in seen)  # singletons (incl skipped-empty)
    return clusters


# ── Microsoft GraphRAG (validated reproduction; no separable resolver exists) ──
#
# GraphRAG's default `Standard` pipeline has NO entity-resolution step (graph
# pruning is `Fast`-pipeline-only). Its dedup is exact-title-equality, realized as
# a `seen_titles: set[str]` in `finalize_entities.py` + a `df.merge(on="title")`
# in `extract_graph.py` -- there is no callable resolver decision object to run
# (unlike neo4j-graphrag's `FuzzyMatchResolver`), so the faithful tier is
# `validated`: we reproduce the title KEY verbatim and cite source.
#
# The title is built in extraction as `clean_str(record_attributes[1].upper())`
# (graph_extractor.py), where `clean_str(x) = re.sub(r"[\x00-\x1f\x7f-\x9f]", "",
# html.unescape(x.strip()))` (index/utils/string.py). So the merge key is:
#   upper -> strip(edges only) -> html.unescape -> control-char strip.
# It does NOT collapse internal whitespace and does NOT strip quotes. The merge is
# GLOBAL (a single `seen_titles` set across all entities), NOT per-label.
# Source: microsoft/graphrag (Standard pipeline), confirmed for 2.x-3.x.
_GRAPHRAG_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _graphrag_key(s: str) -> str:
    """GraphRAG `clean_str(name.upper())` title key (no internal-ws collapse)."""
    return _GRAPHRAG_CTRL.sub("", html.unescape(str(s).upper().strip()))


def graphrag_clusters(items: list[tuple[int, str, str]]) -> list[list[int]]:
    """items: (record_id, mention, entity_type). Exact-title set merge, GLOBAL
    (not per-label), reproducing GraphRAG's `finalize_entities` seen_titles dedup."""
    buckets: dict[str, list[int]] = {}
    for rid, mention, _t in items:
        buckets.setdefault(_graphrag_key(mention), []).append(rid)
    return [sorted(v) for v in buckets.values()]


# ── Cognee (validated reproduction of generate_node_id) ───────────────────────
#
# Cognee's default entity resolution is a deterministic uuid5 collision: two
# mentions merge iff `generate_node_id` produces the same UUID. The function is a
# pure 1-liner (only stdlib `uuid`), so reproducing it verbatim + citing source is
# `validated` (importing the heavy `cognee` package -- lancedb + LLM clients -- to
# call a uuid5 buys zero fidelity). The default ontology resolver is a no-op
# (`RDFLibOntologyResolver(ontology_file=None)`), so the difflib cutoff never fires.
#
# Source (verified verbatim @100044123338de01f72f44b9c528e9fd91fbce59):
#   cognee/infrastructure/engine/utils/generate_node_id.py
#     uuid5(NAMESPACE_OID, node_id.lower().replace(" ", "_").replace("'", ""))
# NOTE this is generate_node_ID (the MERGE key), NOT generate_node_NAME (a display
# helper, name.lower().replace("'","")) -- they differ in the `" " -> "_"` step.
# The dedup is GLOBAL (one node per UUID via deduplicate_nodes_and_edges), not
# per-label.


def _cognee_key(s: str) -> str:
    """Cognee `generate_node_id` merge key: lower -> ' '->'_' -> strip apostrophes."""
    return str(uuid5(NAMESPACE_OID, str(s).lower().replace(" ", "_").replace("'", "")))


def cognee_clusters(items: list[tuple[int, str, str]]) -> list[list[int]]:
    """items: (record_id, mention, entity_type). Exact merge on the generate_node_id
    UUID, GLOBAL (not per-label), reproducing Cognee's deduplicate_nodes_and_edges."""
    buckets: dict[str, list[int]] = {}
    for rid, mention, _t in items:
        buckets.setdefault(_cognee_key(mention), []).append(rid)
    return [sorted(v) for v in buckets.values()]
