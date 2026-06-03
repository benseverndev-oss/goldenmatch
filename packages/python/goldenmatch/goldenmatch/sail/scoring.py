"""S1 score+dedup on Sail (Spark Connect): a block self-join scored by a
rapidfuzz pandas UDF, threshold-filtered, then deduped via GROUP BY max.
Returns the RAW above-threshold canonical (a < b) pair set.

This is the same relational shape as the one-box spine's score+dedup, re-
expressed against PySpark/Spark Connect (Sail distributes the join + GROUP BY
across nodes)."""
from __future__ import annotations

from typing import Any


def score_and_dedup(
    df: Any,
    *,
    block_col: str,
    value_col: str,
    id_col: str,
    scorer_name: str,
    threshold: float,
) -> Any:
    """Score + dedup a single weighted field over a block self-join.

    ``df`` is a Spark DataFrame with ``id_col`` (int), ``value_col`` (the
    scored field) and ``block_col`` (the blocking key). Returns a Spark
    DataFrame of ``(a, b, score)`` with ``a < b``, ``score >= threshold``,
    deduped to ``max(score)`` per canonical pair. The self-join + scorer UDF +
    GROUP BY are all Spark ops Sail distributes.
    """
    from pyspark.sql import functions as F

    from goldenmatch.sail.scorers import make_scorer_udf

    udf = make_scorer_udf(scorer_name)
    a = df.alias("a")
    b = df.alias("b")
    pairs = (
        a.join(
            b,
            (F.col(f"a.{block_col}") == F.col(f"b.{block_col}"))
            & (F.col(f"a.{id_col}") < F.col(f"b.{id_col}")),
        )
        .select(
            F.col(f"a.{id_col}").alias("a"),
            F.col(f"b.{id_col}").alias("b"),
            udf(F.col(f"a.{value_col}"), F.col(f"b.{value_col}")).alias("score"),
        )
        .where(F.col("score") >= F.lit(threshold))
    )
    # Dedup: max(score) per canonical (a, b) -- the scale-mode MAX contract.
    return pairs.groupBy("a", "b").agg(F.max("score").alias("score"))
