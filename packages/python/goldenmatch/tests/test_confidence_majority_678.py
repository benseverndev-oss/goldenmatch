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
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
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


def test_pipeline_threads_pair_scores_into_confidence_majority() -> None:
    """End-to-end pipeline-seam regression for #678.

    The unit tests above prove ``build_golden_records_batch`` consumes
    pair_scores *when handed them*. This test guards the wiring seam the
    original bug lived in: ``_run_dedupe_pipeline`` building
    ``cluster_pair_scores`` from the legacy ``clusters`` dict and passing it
    into the builder. Reverting the ``cluster_pair_scores=`` kwarg in
    ``pipeline.py`` makes this test fail (verified manually: golden ``name``
    flips ``Bravo`` -> ``Alpha``), so the unit-only coverage that the fix
    shipped with would NOT have caught a regression at the call site.

    Fixture: one 4-member cluster. All four rows share the ``block`` value so
    blocking lands them in a single block; the weighted ``link`` matchkey
    (jaro_winkler, rerank disabled so no cross-encoder model loads) scores
    every intra-block pair above the 0.80 threshold, so they cluster
    transitively into one component with REAL, graded per-pair edge weights:

        (Alpha-rows)   (0,1) = 0.868   <- the only Alpha-agreeing edge (weak)
        (Bravo-rows)   (2,3) = 1.000   <- the only Bravo-agreeing edge (strong)
        cross edges    0.858 .. 0.974  <- disagree on ``name``, ignored

    Survivorship field ``name`` is independent of the link/scoring field and
    is split 2-2 (Alpha, Alpha, Bravo, Bravo):

      - count-majority: a 2-2 tie broken by first occurrence -> "Alpha".
      - confidence_majority: Alpha edge-sum 0.868 vs Bravo edge-sum 1.000
        -> "Bravo".

    So a golden ``name`` of "Bravo" can ONLY come from confidence-weighted
    survivorship actually receiving this cluster's pair_scores through the
    pipeline. "Alpha" means the pipeline silently degraded to count-majority
    (the #678 bug).

    Uses an EXPLICIT GoldenMatchConfig (not zero-config ``dedupe_df(df)``)
    with rerank=False so no Hugging Face cross-encoder loads -- per
    CLAUDE.md, auto-config / rerank segfaults torch in this env.
    """
    df = pl.DataFrame(
        {
            # Constant block -> all four rows in one block.
            "block": ["B", "B", "B", "B"],
            # Scoring field: graded jaro_winkler similarity. Rows 0,1 (Alpha)
            # link weakly (0.868); rows 2,3 (Bravo) link identically (1.0).
            "link": [
                "globex international",
                "globex intl group",
                "globex international co",
                "globex international co",
            ],
            # Survivorship field: 2-2 split that makes count- and
            # confidence-majority diverge.
            "name": ["Alpha", "Alpha", "Bravo", "Bravo"],
        }
    )
    matchkey = MatchkeyConfig(
        name="link",
        type="weighted",
        fields=[MatchkeyField(field="link", scorer="jaro_winkler", weight=1.0)],
        threshold=0.80,
        rerank=False,  # do NOT load a cross-encoder (segfaults torch here).
    )
    config = GoldenMatchConfig(
        matchkeys=[matchkey],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["block"], transforms=[])],
            strategy="static",
        ),
        golden_rules=GoldenRulesConfig(default_strategy="confidence_majority"),
    )

    result = dedupe_df(df, config=config, confidence_required=False)

    # Sanity: the four rows collapsed into exactly one multi-member cluster
    # carrying real per-pair scores (proves we're on the dict slow path that
    # threads pair_scores, not the fast/frames/columnar paths).
    assert result.clusters is not None
    multi = [c for c in result.clusters.values() if len(c["members"]) > 1]
    assert len(multi) == 1, f"expected one multi-member cluster, got {result.clusters}"
    assert len(multi[0]["members"]) == 4
    assert multi[0].get("pair_scores"), "cluster carried no per-pair scores"

    # The seam assertion: golden ``name`` is the confidence-majority winner
    # "Bravo", NOT the count-majority winner "Alpha". This is byte-for-byte
    # the value that flips if the pipeline stops passing cluster_pair_scores.
    assert result.golden is not None
    assert result.golden.height == 1
    assert result.golden["name"][0] == "Bravo"
