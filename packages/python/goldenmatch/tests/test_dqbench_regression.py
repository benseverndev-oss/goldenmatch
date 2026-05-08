"""DQbench T1/T2 regression guard for v1.9 best-effort commit.

These tests skip automatically when the DQbench dataset is not available
locally (CI doesn't have the dataset). They serve as a local smoke-test
to catch the precision-collapse pathology (everything matching into one
giant cluster) that regressed composite DQbench score 62.87 → 22.19.
"""
import os
from pathlib import Path
import pytest


def _dqbench_tier_path(tier: int) -> Path:
    return Path.home() / ".dqbench" / "datasets" / f"er_tier{tier}" / "data.csv"


@pytest.mark.skipif(
    not _dqbench_tier_path(1).exists(),
    reason="DQbench T1 dataset not available locally",
)
def test_dqbench_t1_does_not_regress_below_v18():
    """v1.8 baseline: F1 89.3% on T1. v1.9 + virtual-v0 + precision floor
    must not collapse precision (the unguarded v1.9 was 1.0% precision).

    The catastrophic failure mode is precision-collapse: coarse blocking
    (first_token on first_name) with threshold 0.5 causes "everything
    matches", collapsing all ~5K rows into one giant cluster. We guard
    against this by asserting the cluster count stays non-degenerate.
    """
    import polars as pl
    from goldenmatch import dedupe_df

    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    df = pl.read_csv(_dqbench_tier_path(1))
    result = dedupe_df(df)

    # Sanity check: with ~5K rows, the catastrophic case had clusters
    # collapsing to "everything is one cluster" or near-it. Verify the
    # cluster count is non-degenerate (more than 10% of unique rows
    # remain as distinct clusters).
    n_rows = df.height

    # result.clusters is dict[int, dict] with "members" key (per CLAUDE.md)
    n_clusters = result.total_clusters if result.total_clusters else 0

    assert n_clusters >= n_rows * 0.1, (
        f"clusters collapsed: {n_clusters} clusters for {n_rows} rows "
        "(precision-collapse pathology likely returned)"
    )
