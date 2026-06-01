"""Pipeline-level parity: columnar pair-stream path == list path.

This is the gate the Arrow-native columnar-pipeline wiring depends on
(design note: docs/columnar-pipeline-wiring.md). Before the default
``_run_dedupe_pipeline`` can route the cluster stage through the columnar
path (``score_blocks_columnar`` -> ``build_clusters_columnar``) instead of
the list path (``score_blocks_parallel`` -> ``build_clusters``), the two
MUST produce identical clusters on every shape.

The 1M profile-hotspots run (2026-06-01) measured the columnar path at
359s vs the list path's 575s (~38% faster) -- the win is the columnar
scorer's direct-DataFrame emit (#634/#639) over 131M pairs, NOT the
cluster build (``build_clusters_columnar`` wraps the same ``build_clusters``).
These tests lock that the speedup is free of any output divergence so the
wiring (Phase A onward) can land behind a gate with a green parity check.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.cluster import build_clusters, build_clusters_columnar
from goldenmatch.core.scorer import score_blocks_columnar, score_blocks_parallel


def _partition(clusters: dict) -> frozenset:
    """Membership partition, invariant under cluster_id relabeling."""
    return frozenset(frozenset(c["members"]) for c in clusters.values())


def _pair_scores(clusters: dict) -> dict:
    """All pair_scores flattened + canonicalized (min,max) for comparison."""
    out: dict[tuple[int, int], float] = {}
    for c in clusters.values():
        for k, v in c.get("pair_scores", {}).items():
            out[tuple(sorted(k))] = round(v, 9)
    return out


def _person_df(n: int) -> pl.DataFrame:
    """Small synthetic person frame with __row_id__/__source__ wired."""
    import random

    rng = random.Random(n)
    firsts = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank"]
    lasts = ["Smith", "Jones", "Brown", "Taylor", "Wilson", "Davies"]
    rows = []
    for _ in range(n):
        rows.append({
            "first_name": rng.choice(firsts),
            "last_name": rng.choice(lasts),
        })
    return (
        pl.DataFrame(rows)
        .with_row_index(name="__row_id__")
        .with_columns(
            pl.col("__row_id__").cast(pl.Int64),
            pl.lit("fixture").alias("__source__"),
        )
    )


def _cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="last_name_fuzzy",
                type="weighted",
                fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.85,
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
    )


@pytest.mark.parametrize("n", [500, 2000, 5000])
@pytest.mark.parametrize("auto_split", [True, False])
def test_columnar_cluster_path_matches_list_path(n: int, auto_split: bool):
    """`score_blocks_columnar -> build_clusters_columnar` produces the same
    clusters (membership partition + pair_scores) as the list path."""
    cfg = _cfg()
    mk = cfg.matchkeys[0]
    df = _person_df(n)
    blocks = build_blocks(df.lazy(), cfg.blocking)
    all_ids = df["__row_id__"].to_list()

    pairs_list = score_blocks_parallel(blocks, mk, set())
    cl_list = build_clusters(
        pairs_list, all_ids=all_ids, max_cluster_size=100,
        weak_cluster_threshold=0.3, auto_split=auto_split,
    )

    pairs_df = score_blocks_columnar(blocks, mk, set())
    cl_col = build_clusters_columnar(
        pairs_df, all_ids=all_ids, max_cluster_size=100,
        weak_cluster_threshold=0.3, auto_split=auto_split,
    )

    # Same pair stream (count first for a fast, readable failure).
    assert pairs_df.height == len(pairs_list)
    # Same clusters: membership partition and per-pair scores are identical.
    assert _partition(cl_list) == _partition(cl_col)
    assert _pair_scores(cl_list) == _pair_scores(cl_col)
    # Cluster-level fields that downstream (golden) reads must also match.
    by_set_list = {frozenset(c["members"]): c for c in cl_list.values()}
    by_set_col = {frozenset(c["members"]): c for c in cl_col.values()}
    for members, lc in by_set_list.items():
        cc = by_set_col[members]
        assert lc["size"] == cc["size"]
        assert lc["oversized"] == cc["oversized"]
        assert lc.get("confidence") == pytest.approx(cc.get("confidence"), abs=1e-9)


def test_columnar_path_empty_pairs_parity():
    """No-match config: both paths yield all-singleton clusters identically."""
    cfg = _cfg()
    mk = cfg.matchkeys[0]
    # Threshold 1.01 -> nothing scores; every record is its own cluster.
    mk.threshold = 1.01
    df = _person_df(300)
    blocks = build_blocks(df.lazy(), cfg.blocking)
    all_ids = df["__row_id__"].to_list()

    cl_list = build_clusters(
        score_blocks_parallel(blocks, mk, set()), all_ids=all_ids,
    )
    cl_col = build_clusters_columnar(
        score_blocks_columnar(blocks, mk, set()), all_ids=all_ids,
    )
    assert _partition(cl_list) == _partition(cl_col)
    assert len(cl_list) == len(all_ids)
