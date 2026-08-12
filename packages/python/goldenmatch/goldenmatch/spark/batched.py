"""J1: reshape scoring from one call per PAIR to one call per BATCH.

Spark Connect only permits row-shaped UDFs -- the batch entry points
(`ColumnarBatch`, `ArrowColumnVector`) sit behind Catalyst, which Connect does
not expose. Catalyst calls a registered UDF once per row, so a native downcall
per row would be dominated by call overhead and the kernel would never get to do
any work. Grouping pairs into arrays amortises that: one call covers thousands
of pairs.

**This module contains no native code and no scoring.** It is the plan surgery
alone, so that when JNI arrives in J2 a misaligned score cannot be blamed on the
kernel. That separation is the entire point of doing J1 first.

## The hazard, and why the construction looks the way it does

A score must come back attached to the pair it belongs to. Two ways of building
this look natural and are both wrong:

1. **Several `collect_list`s in one aggregation.** ``collect_list(a)``,
   ``collect_list(b)``, ``collect_list(val)`` are separate aggregate expressions
   and Spark does not promise they observe rows in the same order. They usually
   agree, which is worse than never agreeing: it passes in test and skews under
   a different plan. So exactly ONE ``collect_list`` is taken here -- of a
   struct -- and the per-field arrays are derived from it with ``transform``.
   One list, one order, by construction.

2. **Exploding the ids and the scores separately.** Two `explode`s over two
   arrays are two independent generators; nothing pairs element *i* of one with
   element *i* of the other. ``arrays_zip`` pairs them positionally first, and
   the explode then walks a single array of already-paired structs.

Neither mistake crashes. Both silently attach pair *i*'s score to pair *j*,
which is the failure mode this project keeps finding -- so the safe construction
is used even where the unsafe one would probably work.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Column holding the collected pairs of one batch.
_ROWS = "__batch_rows__"
#: Column holding the scores for that batch, positionally aligned with _ROWS.
_SCORES = "__batch_scores__"


def batch_key(strategy: str = "partition") -> Any:
    """The expression pairs are grouped by before scoring.

    ``partition`` groups by ``spark_partition_id()``: one UDF call per Spark
    partition, which is as large a batch as can be formed without a shuffle. The
    batch is therefore whatever the upstream plan already co-located, and no data
    moves to make it.

    Batch SIZE is deliberately not tuned here. It is a throughput question and
    belongs with the measurement in J4; picking a number now would be picking it
    without evidence.
    """
    # Validate BEFORE importing pyspark: rejecting an unknown strategy is not a
    # Spark question, and requiring Spark to say so would make the error
    # unreachable anywhere the tier is not installed.
    if strategy != "partition":
        raise ValueError(
            f"unknown batch strategy {strategy!r}; only 'partition' exists. "
            f"Batch sizing is a J4 (measurement) question, not a J1 one."
        )
    from pyspark.sql import functions as F

    return F.spark_partition_id()


def score_pairs_batched(
    pairs_df: Any,
    source_df: Any,
    *,
    id_col: str,
    value_col: str,
    scorer_id: int,
    udf_name: str,
    batch_strategy: str = "partition",
) -> Any:
    """Score ``(a, b)`` pairs through a batched array UDF; return
    ``(a, b, score)``.

    ``udf_name`` is the SQL name of an ``(int, array<string>, array<string>) ->
    array<double>`` function -- what ``goldenmatch.spark.jvm.install`` registers.

    The result is the same shape the row-shaped ``score_and_dedup`` produces, so
    the two can be compared directly. That comparison is the J1 gate.
    """
    from pyspark.sql import functions as F

    lhs, rhs = "__lhs__", "__rhs__"
    joined = (
        pairs_df.alias("__p__")
        .join(source_df.alias(lhs), F.col(f"{lhs}.{id_col}") == F.col("__p__.a"))
        .join(source_df.alias(rhs), F.col(f"{rhs}.{id_col}") == F.col("__p__.b"))
        .select(
            F.col("__p__.a").alias("a"),
            F.col("__p__.b").alias("b"),
            F.col(f"{lhs}.{value_col}").cast("string").alias("x"),
            F.col(f"{rhs}.{value_col}").cast("string").alias("y"),
        )
    )

    # ONE collect_list, of a struct. See the module docstring: several
    # collect_lists in one aggregation are separate aggregate expressions with
    # no shared order guarantee, and they agree often enough to pass a test and
    # skew under a different plan.
    grouped = joined.groupBy(batch_key(batch_strategy).alias("__batch__")).agg(
        F.collect_list(F.struct("a", "b", "x", "y")).alias(_ROWS)
    )

    # Per-field arrays DERIVED from that single list, so they cannot disagree.
    scored = grouped.select(
        F.col(_ROWS),
        F.call_udf(
            udf_name,
            F.lit(int(scorer_id)),
            F.transform(F.col(_ROWS), lambda r: r["x"]),
            F.transform(F.col(_ROWS), lambda r: r["y"]),
        ).alias(_SCORES),
    )

    # arrays_zip pairs positionally BEFORE the explode, so one generator walks
    # already-paired structs. Two explodes would be two independent generators.
    exploded = scored.select(
        F.explode(F.arrays_zip(F.col(_ROWS), F.col(_SCORES))).alias("__z__")
    )
    return exploded.select(
        F.col(f"__z__.{_ROWS}.a").alias("a"),
        F.col(f"__z__.{_ROWS}.b").alias("b"),
        F.col(f"__z__.{_SCORES}").alias("score"),
    )


def dedup_max(scored_df: Any) -> Any:
    """``max(score)`` per canonical pair -- the tier's existing MAX contract.

    Separate from :func:`score_pairs_batched` so the batched path and the
    row-shaped path can be compared BEFORE dedup as well as after: a
    misalignment that dedup happens to mask would otherwise be invisible.
    """
    from pyspark.sql import functions as F

    return scored_df.groupBy("a", "b").agg(F.max("score").alias("score"))
