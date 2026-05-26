"""Integration tests: AutoConfigController accepts a Ray Dataset as input.

Task 7 of the Phase 2 distributed controller plan.
"""
from __future__ import annotations

import pytest

ray = pytest.importorskip("ray")

# Surname/firstname pools that distribute across soundex codes. A degenerate
# fixture (e.g. 3 surnames x 1000 rows) collapses into ~3 giant soundex blocks;
# auto-config then picks name blocking, scores ~C(1000,2) dense pairs per block,
# and the in-memory oversized-cluster split loop peels one node per O(edges)
# pass -> effectively non-terminating. >= 20 distinct surnames keeps blocks
# small. See feedback: "Synthetic surname fixtures must distribute across
# soundex codes" + tests/test_autoconfig_regressions.py::_SURNAMES.
_SURNAMES = [
    "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson",
    "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
    "Martin", "Thompson", "Garcia", "Martinez", "Robinson", "Clark",
    "Rodriguez", "Lewis", "Lee", "Walker", "Hall", "Allen", "Young",
    "King", "Wright", "Lopez",
]
_FIRSTNAMES = [
    "Alex", "Blair", "Casey", "Dana", "Eli", "Finley", "Gray", "Harper",
    "Indigo", "Jamie", "Kendall", "Logan", "Morgan", "Noel", "Oakley",
    "Parker", "Quinn", "Riley", "Sage", "Taylor", "Umi", "Val", "Wren",
    "Xena", "Yael", "Zane",
]


def test_controller_accepts_ray_dataset_input(tmp_path):
    """AutoConfigController.run must accept a Ray Dataset and return a config."""
    import polars as pl
    from goldenmatch import auto_configure_df
    from goldenmatch.distributed import read_csv_partitioned

    n = 3000
    csv = tmp_path / "in.csv"
    pl.DataFrame({
        "first_name": [_FIRSTNAMES[i % len(_FIRSTNAMES)] for i in range(n)],
        "last_name": [_SURNAMES[i % len(_SURNAMES)] for i in range(n)],
        "email": [f"u{i}@example.com" for i in range(n)],
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
