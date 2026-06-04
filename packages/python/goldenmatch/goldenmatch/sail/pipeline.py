"""End-to-end Sail pipeline: load -> block -> score -> dedup -> WCC -> golden,
all distributed on Sail (Spark Connect). The bench entrypoint (S4). Blocking is
a single pre-existing column (S1 scope); the scorer is the rapidfuzz pandas UDF;
WCC defaults to the chain-robust pointer-jumping algorithm (scale)."""
from __future__ import annotations

from typing import Any


def run_sail_pipeline(
    source_df: Any,
    *,
    id_col: str,
    block_col: str,
    value_col: str,
    golden_cols: list[str],
    scorer_name: str = "jaro_winkler",
    threshold: float = 0.85,
    strategy: str = "most_complete",
    wcc: str = "scale",
) -> Any:
    """Run the full Sail pipeline. Returns the golden DataFrame
    ``(cluster_id, *golden_cols)`` (one per multi-member cluster). ``wcc``:
    ``"scale"`` (pointer-jumping, chain-robust O(log n)) or ``"label_prop"``.

    ``source_df`` must carry ``id_col`` (int), ``block_col``, ``value_col``,
    and the ``golden_cols``.
    """
    from goldenmatch.sail.clustering import (
        connected_components,
        connected_components_scale,
    )
    from goldenmatch.sail.golden import build_golden
    from goldenmatch.sail.scoring import score_and_dedup

    pairs = score_and_dedup(
        source_df,
        block_col=block_col,
        value_col=value_col,
        id_col=id_col,
        scorer_name=scorer_name,
        threshold=threshold,
    )
    ids_df = source_df.select(id_col)
    wcc_fn = (
        connected_components_scale if wcc == "scale" else connected_components
    )
    assignments = wcc_fn(pairs, ids_df, id_col=id_col)
    return build_golden(
        assignments,
        source_df,
        value_cols=golden_cols,
        source_id_col=id_col,
        strategy=strategy,
    )
