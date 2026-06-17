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

from itertools import combinations


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
