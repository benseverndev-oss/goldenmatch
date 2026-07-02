"""SP2 STaRK IR metrics + Arm-B dedup: pure, no store/HF/Modal. Box-safe."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.stark_metrics import dedup_first_seen, mean_metrics, metrics  # noqa: E402


def test_hit_at_1_gold_in_first_position():
    m = metrics(ranked_ids=[7, 3, 9], gold_ids={7})
    assert m["hit@1"] == 1.0 and m["hit@5"] == 1.0
    assert m["mrr"] == 1.0 and m["recall@20"] == 1.0


def test_hit_at_5_not_at_1():
    m = metrics(ranked_ids=[1, 2, 3, 4, 7], gold_ids={7})
    assert m["hit@1"] == 0.0 and m["hit@5"] == 1.0
    assert m["mrr"] == 1 / 5


def test_gold_absent():
    m = metrics(ranked_ids=[1, 2, 3], gold_ids={7})
    assert m == {"hit@1": 0.0, "hit@5": 0.0, "recall@20": 0.0, "mrr": 0.0}


def test_multi_gold_recall_partial():
    # 2 of 3 gold in the top-20
    m = metrics(ranked_ids=[10, 11, 99], gold_ids={10, 11, 42})
    assert m["recall@20"] == 2 / 3
    assert m["hit@1"] == 1.0 and m["mrr"] == 1.0


def test_zero_gold_recall_is_none_sentinel():
    # A query with no gold answers must NOT contribute to the recall mean.
    m = metrics(ranked_ids=[1, 2], gold_ids=set())
    assert m["recall@20"] is None  # caller skips None from the recall mean
    assert m["hit@1"] == 0.0 and m["mrr"] == 0.0


def test_empty_ranked_is_all_zero():
    m = metrics(ranked_ids=[], gold_ids={7})
    assert m == {"hit@1": 0.0, "hit@5": 0.0, "recall@20": 0.0, "mrr": 0.0}


def test_dedup_first_seen_preserves_order_and_drops_repeats():
    assert dedup_first_seen([5, 3, 5, 9, 3]) == [5, 3, 9]


def test_mean_metrics_skips_none_recall():
    per_query = [
        {"hit@1": 1.0, "hit@5": 1.0, "recall@20": 0.5, "mrr": 1.0},
        {"hit@1": 0.0, "hit@5": 0.0, "recall@20": None, "mrr": 0.0},  # zero-gold query
    ]
    agg = mean_metrics(per_query)
    assert agg["hit@1"] == 0.5  # averaged over ALL queries
    assert agg["recall@20"] == 0.5  # averaged over the ONE has-gold query only
    assert agg["n_queries"] == 2 and agg["n_with_gold"] == 1
