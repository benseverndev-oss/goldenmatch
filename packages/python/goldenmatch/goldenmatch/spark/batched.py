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

## And the batch has to be bounded

A third mistake, which DOES crash, and did: grouping by partition alone. The
group is materialised as an array in JVM heap, so its size is a memory
commitment rather than a throughput knob. 1.9M candidate pairs over a handful of
partitions gave `java.lang.OutOfMemoryError` (bench run 31625487603). See
`batch_key`.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Column holding the collected pairs of one batch.
_ROWS = "__batch_rows__"
#: Column holding the scores for that batch, positionally aligned with _ROWS.
_SCORES = "__batch_scores__"


#: Pairs per UDF call. Bounded, because the batch is materialised as an array
#: in JVM heap -- see `batch_key`. 10,000 is what the Connect probe carried
#: comfortably in one call (run 31611464914), and it is large enough that the
#: per-call cost the batching exists to amortise is spread over ten thousand
#: pairs.
DEFAULT_BATCH_SIZE = 10_000


def batch_key(strategy: str = "partition", batch_size: int = DEFAULT_BATCH_SIZE) -> Any:
    """The expression pairs are grouped by before scoring.

    **The batch must be BOUNDED.** ``groupBy`` + ``collect_list`` materialises
    each group as an array in JVM heap, so the group size is a memory
    commitment, not a throughput knob. Grouping by ``spark_partition_id()``
    alone -- one call per partition, "as large a batch as can be formed without
    a shuffle" -- means the array is however many pairs the partition holds.
    Measured: 1.9M candidate pairs over a handful of partitions gives
    ``java.lang.OutOfMemoryError: Java heap space`` (bench run 31625487603).
    That was not a slow path; it was no path.

    So the key is ``(partition, chunk)``. ``monotonically_increasing_id()``
    encodes the partition in its high bits and a row counter in its low bits, so
    dividing it by ``batch_size`` yields chunks that are already
    partition-scoped: rows of different partitions can never share a chunk id,
    and within a partition consecutive rows fall into chunks of at most
    ``batch_size``. No window, no shuffle, no second pass to number the rows.

    The id is not contiguous across partitions, so chunk ids are sparse. That is
    irrelevant -- they are group keys, never counters.
    """
    # Validate BEFORE importing pyspark: rejecting bad arguments is not a Spark
    # question, and requiring Spark to say so would make these errors
    # unreachable anywhere the tier is not installed.
    if strategy != "partition":
        raise ValueError(
            f"unknown batch strategy {strategy!r}; only 'partition' exists."
        )
    if batch_size <= 0:
        raise ValueError(
            f"batch_size must be positive; got {batch_size}. An unbounded batch "
            f"is what OOM'd the executor heap -- the group is materialised as an "
            f"array, so its size is a memory commitment."
        )
    from pyspark.sql import functions as F

    return F.floor(F.monotonically_increasing_id() / F.lit(int(batch_size)))


def _above(threshold: float):
    """``arrays_zip`` element -> does its score clear ``threshold``?

    A module-level factory, not an inline lambda, for the reason pinned in
    ``test_spark_config_pipeline_unit``: **PySpark reads a higher-order lambda's
    meaning from its PARAMETER COUNT.** One parameter is ``(element)``; two is
    ``(element, index)``. So the obvious `lambda z, t=threshold:` is read as an
    indexed callback and ``t`` silently receives the ELEMENT INDEX -- no error,
    just a filter comparing a score against a row number. Closing over the value
    keeps the arity at one and lets a test assert it.
    """
    def f(z):
        from pyspark.sql import functions as F

        return z[_SCORES] >= F.lit(float(threshold))

    return f


def score_pairs_batched(
    pairs_df: Any,
    source_df: Any,
    *,
    id_col: str,
    value_col: str,
    scorer_id: int,
    udf_name: str,
    batch_strategy: str = "partition",
    batch_size: int = DEFAULT_BATCH_SIZE,
    threshold: float | None = None,
) -> Any:
    """Score ``(a, b)`` pairs through a batched array UDF; return
    ``(a, b, score)``.

    ``udf_name`` is the SQL name of an ``(int, array<string>, array<string>) ->
    array<double>`` function -- what ``goldenmatch.spark.jvm.install`` registers.

    The result is the same shape the row-shaped ``score_and_dedup`` produces, so
    the two can be compared directly. That comparison is the J1 gate.

    ## ``threshold``, and why it is applied HERE

    Filtering after the explode and filtering inside the array give the same
    rows. They do not cost the same, and on this path the difference is the
    whole story.

    J4 measured the batched JVM arm at **2.4x SLOWER** than the row-shaped
    Python one, and the plan bisect put the blame precisely: scoring 1.9M pairs
    over JNI took ~0.2s, while ``arrays_zip``/``explode`` added **~1.2s** -- six
    times the scoring. An infinitely fast kernel would move 3.5s to 3.3s. The
    batched path cannot win by scoring faster, because its cost is the
    un-batching that Spark Connect forces (Connect permits only row-shaped UDFs,
    so reaching a native kernel means group -> array -> score -> zip -> explode;
    the row-shaped path never groups and so has nothing to un-batch).

    That bench ran with ``threshold=0.0``: nothing was filtered, so the explode
    emitted every candidate pair -- the worst case for batching, and the one
    case where this argument is decided. A real config cuts at a real threshold
    and keeps a small fraction of candidates, so a filter placed BEFORE the
    generator shrinks the exploded row count, and the shuffle after it, by
    roughly the reject ratio. That is the only lever the bisect leaves open.

    ``None`` (the default) adds no filter node at all, so the plan is
    byte-identical to the pre-threshold one.
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
    grouped = joined.groupBy(
        batch_key(batch_strategy, batch_size).alias("__batch__")
    ).agg(
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
    zipped = F.arrays_zip(F.col(_ROWS), F.col(_SCORES))
    if threshold is not None:
        # BEFORE the explode, on purpose -- see the docstring. `where` after the
        # generator returns the same rows and pays the generator for every
        # rejected pair, which is the cost the J4 bisect attributed the whole
        # 2.4x to.
        zipped = F.filter(zipped, _above(threshold))
    exploded = scored.select(F.explode(zipped).alias("__z__"))
    return exploded.select(
        F.col(f"__z__.{_ROWS}.a").alias("a"),
        F.col(f"__z__.{_ROWS}.b").alias("b"),
        F.col(f"__z__.{_SCORES}").alias("score"),
    )


def score_pairs_rowwise(
    pairs_df: Any,
    source_df: Any,
    *,
    id_col: str,
    value_col: str,
    scorer_id: int,
    udf_name: str,
    threshold: float | None = None,
) -> Any:
    """Score ``(a, b)`` pairs ONE PER CALL through a row-shaped Java UDF.

    Same inputs, same output shape and same kernel as
    :func:`score_pairs_batched` -- what differs is that nothing is batched, so
    the plan has no ``collect_list``, no ``arrays_zip`` and no ``explode``.

    ## Why this exists

    J1 batched because Spark Connect permits only row-shaped UDFs and it assumed
    a per-row downcall "would be dominated by call overhead". That premise was
    never measured. J4 then measured the batched path at ~3x SLOWER than the
    row-shaped PYTHON path, and the bisect attributed ~0.1s to scoring 1.9M
    pairs over JNI and **+1.4s to the un-batching** (~740ns/row, which is object
    allocation, not compute).

    The asymmetry is columnar vs object domain. ``make_scorer_udf`` is an
    ``arrow_udf`` over ``pa.Array``: Spark hands the Python worker a columnar
    Arrow batch and takes one back, and no pair is ever an object. Spark's
    arrays are ``ArrayData`` of ``InternalRow``, not vectors, so batching in the
    SQL layer materialises every pair three times to avoid one columnar
    transfer that costs less than the churn.

    This function is the control: the SAME plan the Python path builds, with the
    scorer call landing in the executor JVM instead of a forked Python worker.
    The only cost it reintroduces is one JNI downcall per pair -- exactly the
    quantity J1 asserted was fatal.

    Which shape is faster is a measurement, not a design principle, and it may
    depend on the workload: batching amortises string marshalling per call
    (better as values get longer), while this avoids the reshape entirely
    (better as pairs multiply).
    """
    from pyspark.sql import functions as F

    lhs, rhs = "__lhs__", "__rhs__"
    a_val = F.col(f"{lhs}.{value_col}").cast("string")
    b_val = F.col(f"{rhs}.{value_col}").cast("string")
    scored = (
        pairs_df.alias("__p__")
        .join(source_df.alias(lhs), F.col(f"{lhs}.{id_col}") == F.col("__p__.a"))
        .join(source_df.alias(rhs), F.col(f"{rhs}.{id_col}") == F.col("__p__.b"))
        .select(
            F.col("__p__.a").alias("a"),
            F.col("__p__.b").alias("b"),
            F.call_udf(udf_name, F.lit(int(scorer_id)), a_val, b_val).alias("score"),
        )
    )
    if threshold is not None:
        # An ordinary row filter, and correctly so: there is no generator here
        # to keep rejected pairs out of. That is the whole point of the shape.
        scored = scored.where(F.col("score") >= F.lit(float(threshold)))
    return scored


def dedup_max(scored_df: Any) -> Any:
    """``max(score)`` per canonical pair -- the tier's existing MAX contract.

    Separate from :func:`score_pairs_batched` so the batched path and the
    row-shaped path can be compared BEFORE dedup as well as after: a
    misalignment that dedup happens to mask would otherwise be invisible.
    """
    from pyspark.sql import functions as F

    return scored_df.groupBy("a", "b").agg(F.max("score").alias("score"))
