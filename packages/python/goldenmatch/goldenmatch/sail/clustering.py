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


def connected_components_scale(
    pairs_df: Any,
    ids_df: Any,
    *,
    id_col: str = "__row_id__",
    max_rounds: int = 40,
) -> Any:
    """Chain-robust O(log n) weakly-connected components via min-label
    propagation with POINTER-JUMPING (Shiloach-Vishkin shortcutting). Pure
    Spark Connect. The scale algorithm for the 100M bench (label-prop is
    O(diameter) on long chains; the pointer-jump halves the distance to the
    root each round -> O(log n)).

    Same output as ``connected_components``: ``(cluster_id, member_id)`` where
    cluster_id is the component's min member id. Isolated nodes (singletons)
    seeded from the DISTRIBUTED ``ids_df`` (the rehydration-OOM trap).

    Each round: (1) PROPAGATE -- each node adopts min(own label, min neighbor
    label); (2) SHORTCUT -- ``label[v] = label[label[v]]`` (jump to the label's
    label). Early-exit when labels stop changing. NOTE for the real run:
    cache/checkpoint ``labels`` each round (Spark Connect lineage grows) + a
    cheaper change-counter; the gate runs tiny fixtures.

    HAND TRACE (2-node, edges=[(0,1)], seed {0:0,1:1}):
      r1 propagate: node0 min(0,lbl[1]=1)=0; node1 min(1,lbl[0]=0)=0 -> {0:0,1:0}
         shortcut: lbl[0]=lbl[0]=0; lbl[1]=lbl[lbl[1]=0]=0 -> {0:0,1:0}
      r2: no change -> CONVERGED {0:0,1:0}.  ONE component. CORRECT.
    HAND TRACE (3-chain, edges=[(0,1),(1,2)], seed {0:0,1:1,2:2}):
      r1 propagate: 0->min(0,1)=0; 1->min(1,min(0,2)=0)=0; 2->min(2,1)=1 -> {0:0,1:0,2:1}
         shortcut: 0->lbl[0]=0; 1->lbl[0]=0; 2->lbl[1]=0 -> {0:0,1:0,2:0}
      r2: no change -> CONVERGED all 0.  ONE component. CORRECT.
    """
    from pyspark.sql import functions as F

    # Symmetric edges (node, nbr) so labels flow both ways.
    fwd = pairs_df.select(F.col("a").alias("node"), F.col("b").alias("nbr"))
    rev = pairs_df.select(F.col("b").alias("node"), F.col("a").alias("nbr"))
    edges = fwd.unionByName(rev)

    # Labels seeded from the DISTRIBUTED universe (singletons -> own label).
    labels = ids_df.select(
        F.col(id_col).cast("long").alias("node")
    ).withColumn("label", F.col("node"))

    # Spark Connect discipline: join on a SHARED NAME, other side renamed; no
    # df["col"] cross-handle refs (the S2 AMBIGUOUS_REFERENCE lesson).
    for _ in range(max_rounds):
        # (1) PROPAGATE: each node adopts min(own, min neighbor label).
        lab_for_nbr = labels.select(
            F.col("node").alias("nbr"), F.col("label").alias("nbr_label")
        )
        nbr_min = (
            edges.join(lab_for_nbr, on="nbr", how="inner")
            .groupBy("node")
            .agg(F.min("nbr_label").alias("nbr_min"))
        )
        propagated = labels.join(nbr_min, on="node", how="left").select(
            F.col("node"),
            F.least(
                F.col("label"), F.coalesce(F.col("nbr_min"), F.col("label"))
            ).alias("label"),
        )
        # (2) SHORTCUT (pointer-jump): label[v] = label[label[v]].
        lab_target = propagated.select(
            F.col("node").alias("label"), F.col("label").alias("grandlabel")
        )
        jumped = propagated.join(lab_target, on="label", how="left").select(
            F.col("node"),
            F.coalesce(F.col("grandlabel"), F.col("label")).alias("label"),
        )
        # Convergence: any label changed vs the previous round?
        prev_r = labels.select(
            F.col("node"), F.col("label").alias("prev_label")
        )
        changed = (
            jumped.join(prev_r, on="node", how="inner")
            .where(F.col("label") != F.col("prev_label"))
            .limit(1)
            .count()
        )
        labels = jumped
        if changed == 0:
            break

    return labels.select(
        F.col("label").alias("cluster_id"), F.col("node").alias("member_id")
    )
