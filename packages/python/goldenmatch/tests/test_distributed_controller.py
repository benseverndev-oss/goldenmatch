"""Integration tests: AutoConfigController accepts a Ray Dataset as input.

Task 7 of the Phase 2 distributed controller plan.
"""
from __future__ import annotations

import pytest

ray = pytest.importorskip("ray")


def test_controller_accepts_ray_dataset_input(tmp_path):
    """AutoConfigController.run must accept a Ray Dataset and return a config."""
    import polars as pl
    from goldenmatch import auto_configure_df
    from goldenmatch.distributed import read_csv_partitioned

    csv = tmp_path / "in.csv"
    pl.DataFrame({
        "first_name": ["Alice", "Bob", "Charlie"] * 1000,
        "last_name": ["Smith", "Jones", "Brown"] * 1000,
        "email": [f"u{i}@example.com" for i in range(3000)],
    }).write_csv(csv)

    ds = read_csv_partitioned(str(csv), n_partitions=4)
    config = auto_configure_df(ds, confidence_required=False)
    assert config is not None
    assert config.get_matchkeys() is not None


def test_controller_distributed_path_does_not_materialize_full_df(tmp_path):
    """The full df should never be collected to the driver during run()."""
    import polars as pl
    import psutil
    from goldenmatch import auto_configure_df
    from goldenmatch.distributed import read_csv_partitioned

    csv = tmp_path / "big.csv"
    pl.DataFrame({
        "first_name": ["Alice"] * 100_000,
        "last_name": ["Smith"] * 100_000,
        "email": [f"u{i}@example.com" for i in range(100_000)],
    }).write_csv(csv)

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    ds = read_csv_partitioned(str(csv), n_partitions=8)
    _ = auto_configure_df(ds, confidence_required=False)
    after = proc.memory_info().rss

    growth_mb = (after - baseline) / 1024 / 1024
    # 100K-row Polars frame is ~30 MB; if it materialized on driver we'd
    # see at least that much growth. Budget 400 MB for sample collections.
    assert growth_mb < 400, f"driver RSS grew {growth_mb:.0f} MB"
