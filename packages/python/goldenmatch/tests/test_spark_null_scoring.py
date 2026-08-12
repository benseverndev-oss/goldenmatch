"""The null-vs-null false merge on the SHIPPED single-field path (P4).

`score_and_dedup` is the S1 entry `run_sail_pipeline` still calls. It scored two
records that are both missing the compared field as a PERFECT 1.0, because the
scorer kernel substitutes "" for a missing value and ""-vs-"" is an exact match.
Any threshold accepted that pair, so two records whose only shared evidence was a
shared absence were merged into one golden record.

The one-box never did this: `core.scorer.score_field` returns None when either
side is missing, and `score_pair` drops the field -- which for a single-field
matchkey leaves an empty denominator and a score of 0.0.

Runs in the Spark lanes; skips where no Spark Connect client is installed.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

_ID = "__row_id__"
_COLS = [_ID, "blk", "name"]


def _pairs(spark, rows, *, threshold=0.85):
    from goldenmatch.spark.scoring import score_and_dedup

    df = spark.createDataFrame(rows, _COLS)
    out = score_and_dedup(
        df,
        block_col="blk",
        value_col="name",
        id_col=_ID,
        scorer_name="jaro_winkler",
        threshold=threshold,
    )
    return {(int(r["a"]), int(r["b"])): float(r["score"]) for r in out.collect()}


def test_both_sides_null_is_not_a_match(spark):
    """The regression. Both rows share a block and are both missing `name`."""
    got = _pairs(spark, [(0, "b1", None), (1, "b1", None)])
    assert (0, 1) not in got, (
        f"null-vs-null merged with score {got.get((0, 1))!r}; the kernel's "
        f"\"\"-substitution leaked into the accepted pair set"
    )


def test_one_side_null_is_not_a_match(spark):
    got = _pairs(spark, [(0, "b1", "smith"), (1, "b1", None)])
    assert (0, 1) not in got


@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.85, 0.99])
def test_null_pair_is_rejected_at_every_threshold(spark, threshold):
    """The old behaviour scored 1.0, which cleared EVERY threshold -- including
    0.0, where a score-0.0 pair is also kept. At threshold 0.0 the pair may
    legitimately appear; what must never happen is a score of 1.0."""
    got = _pairs(spark, [(0, "b1", None), (1, "b1", None)], threshold=threshold)
    assert got.get((0, 1), 0.0) == 0.0, (
        f"null-vs-null scored {got.get((0, 1))!r} at threshold {threshold}"
    )


def test_real_values_still_score_and_match(spark):
    """The fix must not suppress genuine matches -- a guard that rejected
    everything would pass every test above while disabling the tier."""
    got = _pairs(spark, [(0, "b1", "smith"), (1, "b1", "smyth")])
    assert (0, 1) in got
    assert got[(0, 1)] >= 0.85


def test_null_row_does_not_block_a_real_match_in_the_same_block(spark):
    """Mixed block: the real pair survives, the null pair does not."""
    got = _pairs(
        spark,
        [(0, "b1", "smith"), (1, "b1", "smyth"), (2, "b1", None), (3, "b1", None)],
    )
    assert (0, 1) in got
    for pair in ((2, 3), (0, 2), (1, 3)):
        assert pair not in got, f"{pair} should not have matched"
