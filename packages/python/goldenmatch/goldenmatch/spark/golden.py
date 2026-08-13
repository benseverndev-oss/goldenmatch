"""Golden-record survivorship on Sail (Spark Connect), distributed.

Joins the S2 ``assignments`` (cluster_id, member_id) to the source records,
filters to multi-member clusters, then for each field collects the cluster's
values (``collect_list``) and merges them with the ONE-BOX
``core.golden.merge_field`` primitive via a scalar arrow UDF -- reusing the
exact survivorship logic guarantees semantic parity; Sail distributes the
group-and-merge. Pure-relational (collect_list + scalar UDF), building on S1's
proven arrow_udf mechanism (not grouped-map applyInArrow).

S3 scope: the uniform, order-INDEPENDENT case (default ``most_complete`` over
multi-member clusters). Order-dependent strategies (most_recent/source_priority),
custom plugin strategies, oversized exclusion, and provenance are deferred
(mirrors the Ray distributed golden's in-memory fallback for those)."""
from __future__ import annotations

from typing import Any


def merge_expr(col: Any, strategy: str, survivorship_udf: str) -> Any:
    """Survivorship in the EXECUTOR JVM, over the same pyo3-free
    ``survivorship-core`` the Python path uses.

    ``col`` is one cluster's collected field values (a ``collect_list``).

    The strategy is validated on the DRIVER first. ``source_priority`` needs a
    sources list and ``most_recent`` needs dates -- neither of which this call
    site passes, so Python RAISES for them and the kernel refuses. ``custom:*``
    is arbitrary Python. Refusing at plan time with the strategy named beats
    emitting a plausible wrong survivor from every cluster, because a golden
    record chosen by a different rule raises nothing and looks right.
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark.jvm import (
        JVM_SURVIVORSHIP_STRATEGIES,
        jvm_supports_strategy,
    )

    if not jvm_supports_strategy(strategy):
        raise ValueError(
            f"the JVM survivorship path cannot run strategy {strategy!r}. "
            f"`source_priority` needs a sources list and `most_recent` needs "
            f"dates, neither of which this call site passes -- Python raises for "
            f"them too. `custom:` strategies are arbitrary Python. Available: "
            f"{sorted(JVM_SURVIVORSHIP_STRATEGIES)}. Omit survivorship_udf to "
            f"use the Python path with an executor environment."
        )
    return F.call_udf(survivorship_udf, col, F.lit(strategy))


def make_merge_udf(strategy: str) -> Any:
    """A scalar arrow UDF mapping an array-of-values column (one cluster's
    collected field values) to the survivor value via ``merge_field``."""
    from goldenmatch.spark._arrow import arrow_udf, from_pylist, to_pylist

    @arrow_udf("string")
    def _udf(col):  # col: pa.Array; each element is the collected list
        from goldenmatch.config.schemas import GoldenFieldRule
        from goldenmatch.core.golden import merge_field

        rule = GoldenFieldRule(strategy=strategy)
        out = []
        for vals in to_pylist(col):
            # ``vals`` arrives as a python list OR a numpy ndarray (Spark array
            # column); list(...) handles both -- do NOT assume a python list.
            values = list(vals) if vals is not None else []
            merged, _conf, _src = merge_field(values, rule)
            out.append(None if merged is None else str(merged))
        return from_pylist(out, "string")

    return _udf


def build_golden(
    assignments_df: Any,
    source_df: Any,
    *,
    value_cols: list[str],
    source_id_col: str = "__row_id__",
    strategy: str = "most_complete",
    rules: Any | None = None,
) -> Any:
    """Build one golden record per multi-member cluster, distributed.

    Args:
        assignments_df: Spark DataFrame ``(cluster_id, member_id)`` (from S2).
        source_df: Spark DataFrame with ``source_id_col`` + the ``value_cols``.
        value_cols: the fields to survivor-merge.
        source_id_col: the id column in ``source_df`` (joined to ``member_id``).
        strategy: survivorship strategy (S3: order-independent, default
            ``most_complete``).
        rules: optional ``GoldenRulesConfig`` forwarded by the pipeline. When
            correlated survivorship is active (field_groups / conditional /
            validate), this function refuses rather than silently mis-merging
            (spec 4.4).

    Returns:
        Spark DataFrame ``(cluster_id, *value_cols)`` -- one golden row per
        multi-member cluster, each field survivor-merged.
    """
    # Spec 4.4: refuse correlated survivorship on the Sail distributed backend.
    # The in-memory builder runs a staged per-cluster pass on the driver that
    # this Spark-side groupby + scalar-UDF path cannot replicate. Fail-fast
    # before any Spark work so callers get a clear error rather than a silently
    # wrong golden record.
    from goldenmatch.core.golden import assert_in_memory_survivorship
    assert_in_memory_survivorship(rules, "Sail distributed backend")

    from pyspark.sql import functions as F

    # Join on a SHARED name (rename source's id col -> member_id); no df["col"]
    # cross-handle refs (the S2 AMBIGUOUS_REFERENCE lesson).
    src = source_df.withColumnRenamed(source_id_col, "member_id")
    joined = assignments_df.join(src, on="member_id", how="inner")

    # Multi-member clusters only (golden is the multi-member rollup; singletons
    # are "unique", not golden).
    multi = (
        assignments_df.groupBy("cluster_id")
        .count()
        .where(F.col("count") > 1)
        .select("cluster_id")
    )
    joined = joined.join(multi, on="cluster_id", how="inner")

    # Collect each field's values per cluster, then merge via the UDF.
    agg = joined.groupBy("cluster_id").agg(
        *[F.collect_list(c).alias(c) for c in value_cols]
    )
    merge_udf = make_merge_udf(strategy)
    for c in value_cols:
        agg = agg.withColumn(c, merge_udf(F.col(c)))
    return agg
