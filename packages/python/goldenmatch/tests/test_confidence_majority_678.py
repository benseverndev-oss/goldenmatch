"""Regression test for #678: confidence_majority silently degraded to
count-majority because the pipeline never fed per-cluster pair_scores into
``build_golden_records_batch``.

These are UNIT tests on ``build_golden_records_batch``: they prove that when
a cluster's pair_scores reach the builder, ``confidence_majority`` consumes
the edge confidences (and diverges from count-majority), and that without
them the builder falls back to count-majority (the documented fallback).
"""

from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch


def _multi_df_diverge() -> pl.DataFrame:
    """One cluster (id 0), four members 10/11/12/13.

    Field ``name``: rows 10, 11 -> "A" (2 votes); rows 12, 13 -> "B" (2 votes).
    Count-majority is a 2-2 tie broken by first-occurrence -> "A".

    Edges:
      (10,11) "A"-"A" weak  -> 0.55  (A's only agreeing edge)
      (12,13) "B"-"B" strong-> 0.99  (B's only agreeing edge)
      cross edges 0.50 (disagree, contribute to neither value)

    confidence_majority: A=0.55, B=0.99 -> "B" wins.
    count-majority: 2-2 tie -> first occurrence "A".
    """
    return pl.DataFrame(
        {
            "__cluster_id__": [0, 0, 0, 0],
            "__row_id__": [10, 11, 12, 13],
            "name": ["A", "A", "B", "B"],
        }
    )


_PAIR_SCORES_DIVERGE = {
    (10, 11): 0.55,
    (12, 13): 0.99,
    (10, 12): 0.50,
    (10, 13): 0.50,
    (11, 12): 0.50,
    (11, 13): 0.50,
}


def test_confidence_majority_consumes_pair_scores() -> None:
    """With cluster_pair_scores, confidence_majority picks the high-confidence
    value "B" even though count-majority would pick "A"."""
    rules = GoldenRulesConfig(default_strategy="confidence_majority")
    records = build_golden_records_batch(
        _multi_df_diverge(),
        rules,
        cluster_pair_scores={0: dict(_PAIR_SCORES_DIVERGE)},
    )
    assert len(records) == 1
    assert records[0]["name"]["value"] == "B"


def test_confidence_majority_falls_back_without_pair_scores() -> None:
    """Without cluster_pair_scores (None), the builder falls back to
    count-majority -> "A" (first occurrence of the tied value)."""
    rules = GoldenRulesConfig(default_strategy="confidence_majority")
    records = build_golden_records_batch(
        _multi_df_diverge(),
        rules,
        cluster_pair_scores=None,
    )
    assert len(records) == 1
    assert records[0]["name"]["value"] == "A"
