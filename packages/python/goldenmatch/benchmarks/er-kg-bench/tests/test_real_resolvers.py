"""Goldenmatch-free unit test for the real neo4j-graphrag resolver helper."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench import metrics                              # pyright: ignore[reportMissingImports]
from erkgbench.real_resolvers import neo4j_graphrag_fuzzy_clusters  # pyright: ignore[reportMissingImports]

DATASET = _BENCH_ROOT / "dataset" / "records.csv"


def _load():
    items, entity_ids, classes = [], [], []
    with DATASET.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            items.append((int(row["record_id"]), row["mention"], row["entity_type"]))
            entity_ids.append(row["entity_id"])
            classes.append(row["failure_class"])
    return items, entity_ids, classes


def test_reproduces_poc_f1():
    items, entity_ids, classes = _load()
    clustering = neo4j_graphrag_fuzzy_clusters(items)
    f1 = metrics.score_by_class(entity_ids, classes, clustering)["__overall__"].f1
    assert round(f1, 3) == 0.470  # the POC's measured real number, pinned

def test_no_empty_mentions_in_corpus():
    items, _, _ = _load()
    assert all(m and m.strip() for _i, m, _t in items)

def test_empty_mention_is_skipped_faithfully():
    # the real resolver skips entities whose combined_text is empty; a blank mention
    # must become a singleton, never merged.
    items = [(0, "Acme Inc", "org"), (1, "Acme Inc", "org"), (2, "", "org")]
    clustering = neo4j_graphrag_fuzzy_clusters(items)
    assert [2] in clustering                     # empty -> singleton
    assert any(set(c) == {0, 1} for c in clustering)  # identical non-empty merge
