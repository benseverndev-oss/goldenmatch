"""Weakly-connected components on Sail (Spark Connect) -- the Union-Find
holdout, computed distributed via min-label propagation.

S2 uses min-label propagation (pure Spark DataFrame joins to a fixpoint):
each node starts labeled with its own id (seeded from a DISTRIBUTED ids
frame -- NEVER a driver list[int], the WCC-rehydration OOM trap), then
adopts the min label among itself + its neighbors until nothing changes.
Each component converges to label = its min member id. Correct + genuinely
Sail-native; the chain-SCALE-robust large-star/small-star is an S4
prerequisite (label-prop is O(diameter) iterations on long chains -- fine
at S2's correctness-gate scale, not at 100M)."""
from __future__ import annotations

from typing import Any


def connected_components(
    pairs_df: Any,
    ids_df: Any,
    *,
    id_col: str = "__row_id__",
) -> Any:
    """Distributed weakly-connected components.

    Args:
        pairs_df: Spark DataFrame of edges with ``a`` and ``b`` (int) columns
            (canonical ``a < b`` from ``score_and_dedup``; not required).
        ids_df: Spark DataFrame of the FULL node universe with ``id_col``
            (every record id, singletons included). DISTRIBUTED -- never a
            driver list. Singletons surface as their own component. By
            contract every id in ``pairs_df`` is also in ``ids_df``.
        id_col: the id column name in ``ids_df``.

    Returns:
        Spark DataFrame ``(cluster_id, member_id)`` -- one row per node;
        ``cluster_id`` is the component's min member id (a stable, label-
        independent partition). Matches ``build_cluster_frames.assignments``.
    """
    from pyspark.sql import functions as F

    # Symmetric edges so labels flow both ways: (src, dst) for both
    # orientations. Self-loops are harmless (a < b avoids them anyway).
    fwd = pairs_df.select(F.col("a").alias("src"), F.col("b").alias("dst"))
    rev = pairs_df.select(F.col("b").alias("src"), F.col("a").alias("dst"))
    edges = fwd.unionByName(rev)

    # Seed: every node labeled with itself (from the DISTRIBUTED universe).
    labels = ids_df.select(F.col(id_col).cast("long").alias("node")).withColumn(
        "label", F.col("node")
    )

    # Iterate to fixpoint. Bounded by component diameter; at S2 fixture scale
    # this is 2-3 rounds. The convergence count is a driver scalar (cheap).
    # NOTE for S4: cache/checkpoint `labels` each round + swap in large-star/
    # small-star -- label-prop's O(diameter) won't bind at 100M chains.
    #
    # Spark Connect discipline: every join is on a SHARED COLUMN NAME (auto-
    # coalesced, no duplicate-column ambiguity), and the other side is RENAMED
    # before the join so no two inputs share a non-key name. We NEVER reference
    # a column via the ``df["col"]`` handle across a self-similar join (the
    # AMBIGUOUS_REFERENCE / CANNOT_RESOLVE footgun across iterations).
    max_rounds = 100
    for _ in range(max_rounds):
        # Each node's neighbor-min label. Join edges.dst == labels.node by
        # renaming labels -> (dst, dst_label) and joining on the shared "dst".
        lab_for_nbr = labels.select(
            F.col("node").alias("dst"), F.col("label").alias("dst_label")
        )
        nbr_min = (
            edges.join(lab_for_nbr, on="dst", how="inner")
            .groupBy("src")
            .agg(F.min("dst_label").alias("nbr_min"))
            .select(F.col("src").alias("node"), F.col("nbr_min"))
        )
        # Update: node adopts min(own label, neighbor-min). Left join on the
        # shared "node"; nodes with no neighbor keep their label (coalesce).
        new_labels = labels.join(nbr_min, on="node", how="left").select(
            F.col("node"),
            F.least(
                F.col("label"), F.coalesce(F.col("nbr_min"), F.col("label"))
            ).alias("label"),
        )
        # Convergence: compare new vs old on the shared "node"; old renamed.
        old_for_cmp = labels.select(
            F.col("node"), F.col("label").alias("old_label")
        )
        changed = (
            new_labels.join(old_for_cmp, on="node", how="inner")
            .where(F.col("label") != F.col("old_label"))
            .limit(1)
            .count()
        )
        labels = new_labels
        if changed == 0:
            break

    return labels.select(
        F.col("label").alias("cluster_id"), F.col("node").alias("member_id")
    )
