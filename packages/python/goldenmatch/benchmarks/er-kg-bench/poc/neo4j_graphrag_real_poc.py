"""POC: run neo4j-graphrag-python's REAL FuzzyMatchResolver over the ER-KG-Bench
corpus and compare to the modeled `neo4j-graphrag(fuzzy)` adapter.

Why this is a faithful "real framework" run without a live Neo4j:
the real resolver (neo4j_graphrag.experimental.components.resolver.FuzzyMatchResolver)
needs Neo4j + APOC to run end-to-end (async, Cypher, apoc.refactor.mergeNodes). But the
CLUSTERING is decided entirely by the library's own Python methods -- `compute_similarity`
(rapidfuzz `WRatio/100`) and the `_consolidate_sets` greedy union -- grouped by node
*label*. Neo4j only persists the merge; it cannot change which nodes merge. So this POC
calls the library's REAL decision code over the corpus (grouped by entity_type = label),
which yields the exact clustering a live run would, and scores it with the bench's own
metrics. The Neo4j+APOC round-trip is storage only; a full live run is Phase-2 hardening.

This settles the circularity question: does the REAL resolver behave like our model
(`modeled.Neo4jGraphRAGFuzzyModeled`: WRatio>=0.8, ALL-pairs, union-find, NO label gate)?
Two real differences the model omits and this exercises: (1) the real resolver only
compares nodes sharing a label (entity_type gate); (2) it consolidates with a greedy,
order-dependent `_consolidate_sets`, not full union-find.

Run: python poc/neo4j_graphrag_real_poc.py   (no Neo4j, no key, no torch)
"""
from __future__ import annotations

import csv
import sys
from itertools import combinations
from pathlib import Path
from unittest.mock import MagicMock

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

import neo4j_graphrag  # pyright: ignore[reportMissingImports]  # POC dep (pip install neo4j-graphrag); declared in Phase 1
from neo4j_graphrag.experimental.components.resolver import (  # pyright: ignore[reportMissingImports]
    FuzzyMatchResolver,
)

from erkgbench import metrics

DATASET = _BENCH_ROOT / "dataset" / "records.csv"

# Modeled `neo4j-graphrag(fuzzy)` on the scaled corpus (results/RESULTS.md), for contrast.
MODELED_F1 = 0.403
MODELED_P = 0.345
MODELED_R = 0.485


def load() -> tuple[list[str], list[str], list[str], list[str]]:
    mentions, entity_ids, classes, types = [], [], [], []
    with DATASET.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mentions.append(row["mention"])
            entity_ids.append(row["entity_id"])
            classes.append(row["failure_class"])
            types.append(row["entity_type"])
    return mentions, entity_ids, classes, types


def real_resolver_clustering(mentions: list[str], types: list[str]) -> list[list[int]]:
    """Cluster record indices using the REAL FuzzyMatchResolver's own methods."""
    resolver = FuzzyMatchResolver(driver=MagicMock())  # driver is I/O only, unused for clustering
    threshold = resolver.similarity_threshold  # library default 0.8

    # The real resolver groups nodes by label; here each record's label is its entity_type.
    groups: dict[str, list[int]] = {}
    for i, t in enumerate(types):
        groups.setdefault(t, []).append(i)

    clusters: list[list[int]] = []
    for idxs in groups.values():
        pairs: list[set[int]] = []
        for i, j in combinations(idxs, 2):
            if resolver.compute_similarity(mentions[i], mentions[j]) >= threshold:
                pairs.append({i, j})
        merged = resolver._consolidate_sets(pairs)  # the library's REAL consolidation
        seen: set[int] = set()
        for s in merged:
            clusters.append(sorted(s))
            seen |= s
        clusters.extend([i] for i in idxs if i not in seen)  # singletons
    return clusters


def main() -> None:
    mentions, entity_ids, classes, types = load()
    clustering = real_resolver_clustering(mentions, types)
    by_class = metrics.score_by_class(entity_ids, classes, clustering)
    o = by_class["__overall__"]
    print(f"REAL neo4j-graphrag FuzzyMatchResolver  (neo4j-graphrag {neo4j_graphrag.__version__})")
    print(f"  records={len(mentions)} entities={len(set(entity_ids))}")
    print(f"  REAL     P={o.precision:.3f} R={o.recall:.3f} F1={o.f1:.3f}")
    print(f"  MODELED  P={MODELED_P:.3f} R={MODELED_R:.3f} F1={MODELED_F1:.3f}  (neo4j-graphrag(fuzzy), scaled corpus)")
    print(f"  delta F1 = {o.f1 - MODELED_F1:+.3f}")
    per_class = {c: round(by_class[c].f1, 3) for c in by_class if c != "__overall__"}
    print(f"  REAL per-class F1: {per_class}")


if __name__ == "__main__":
    main()
