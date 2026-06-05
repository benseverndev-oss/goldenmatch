"""Golden-record survivorship on Sail (Spark Connect), distributed.

Joins the S2 ``assignments`` (cluster_id, member_id) to the source records,
filters to multi-member clusters, then for each field collects the cluster's
values (``collect_list``) and merges them with the ONE-BOX
``core.golden.merge_field`` primitive via a scalar pandas UDF -- reusing the
exact survivorship logic guarantees semantic parity; Sail distributes the
group-and-merge. Pure-relational (collect_list + scalar UDF), building on S1's
proven pandas_udf mechanism (not grouped-map applyInPandas).

S3 scope: the uniform, order-INDEPENDENT case (default ``most_complete`` over
multi-member clusters). Order-dependent strategies (most_recent/source_priority),
custom plugin strategies, oversized exclusion, and provenance are deferred
(mirrors the Ray distributed golden's in-memory fallback for those)."""
from __future__ import annotations

from typing import Any


def make_merge_udf(strategy: str) -> Any:
    """A scalar pandas UDF mapping an array-of-values column (one cluster's
    collected field values) to the survivor value via ``merge_field``."""
    from pyspark.sql.functions import pandas_udf

    @pandas_udf("string")
    def _udf(col):  # col: pandas Series; each element is the collected list
        import pandas as pd

        from goldenmatch.config.schemas import GoldenFieldRule
        from goldenmatch.core.golden import merge_field

        rule = GoldenFieldRule(strategy=strategy)
        out = []
        for vals in col:
            # ``vals`` arrives as a python list OR a numpy ndarray (Spark array
            # column); list(...) handles both -- do NOT assume a python list.
            values = list(vals) if vals is not None else []
            merged, _conf, _src = merge_field(values, rule)
            out.append(None if merged is None else str(merged))
        return pd.Series(out)

    return _udf


def build_golden(
    assignments_df: Any,
    source_df: Any,
    *,
    value_cols: list[str],
    source_id_col: str = "__row_id__",
    strategy: str = "most_complete",
) -> Any:
    """Build one golden record per multi-member cluster, distributed.

    Args:
        assignments_df: Spark DataFrame ``(cluster_id, member_id)`` (from S2).
        source_df: Spark DataFrame with ``source_id_col`` + the ``value_cols``.
        value_cols: the fields to survivor-merge.
        source_id_col: the id column in ``source_df`` (joined to ``member_id``).
        strategy: survivorship strategy (S3: order-independent, default
            ``most_complete``).

    Returns:
        Spark DataFrame ``(cluster_id, *value_cols)`` -- one golden row per
        multi-member cluster, each field survivor-merged.
    """
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
