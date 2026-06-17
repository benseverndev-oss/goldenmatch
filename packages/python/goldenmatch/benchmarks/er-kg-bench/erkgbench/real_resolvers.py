"""Run neo4j-graphrag-python's REAL resolver decision code, no live Neo4j.

The real FuzzyMatchResolver needs Neo4j+APOC end-to-end, but the CLUSTERING is
decided purely by the library's own `compute_similarity` (rapidfuzz WRatio/100) and
`_consolidate_sets`, grouped by node label. Neo4j only persists the merge; it cannot
change which nodes merge. So we call those real methods over the corpus (grouped by
entity_type = label). neo4j-graphrag is imported lazily so this module stays
importable (and unit-testable) without the dependency.
"""
from __future__ import annotations

from itertools import combinations


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
        merged = resolver._consolidate_sets(pairs)  # the library's REAL consolidation
        seen: set[int] = set()
        for s in merged:
            clusters.append(sorted(s))
            seen |= s
        clusters.extend([i] for i in ids if i not in seen)  # singletons (incl skipped-empty)
    return clusters
