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


def test_controller_distributed_path_does_not_materialize_full_df(tmp_path, monkeypatch):
    """The controller must sample the Ray Dataset, never collect the full df to
    the driver.

    Asserts directly on the max rows pulled to the driver via the bounded-sample
    helper -- not on process RSS. The old RSS-growth proxy was meaningless and
    flaky: a materialized 100K-row frame is only ~30 MB, but the threshold was
    400 MB, so the check only ever measured Ray's variable runtime overhead
    (it grew 703 MB on one shared-runner run, 18 MB on another). A non-degenerate
    surname pool also avoids the soundex-collapse pair explosion."""
    import polars as pl
    from goldenmatch import auto_configure_df
    from goldenmatch.distributed import read_csv_partitioned
    import goldenmatch.distributed.sample as sample_mod

    n = 100_000
    csv = tmp_path / "big.csv"
    pl.DataFrame({
        "first_name": [_FIRSTNAMES[i % len(_FIRSTNAMES)] for i in range(n)],
        "last_name": [_SURNAMES[i % len(_SURNAMES)] for i in range(n)],
        "email": [f"u{i}@example.com" for i in range(n)],
    }).write_csv(csv)

    # Spy on the controller's only driver-side collection point. controller.run()
    # does a late `from goldenmatch.distributed.sample import take_sample_distributed`,
    # so patching the module attribute binds the spy at call time.
    collected_heights: list[int] = []
    real_tsd = sample_mod.take_sample_distributed

    def _spy(ds, sample_cap=20_000):
        out = real_tsd(ds, sample_cap=sample_cap)
        collected_heights.append(out.height)
        return out

    monkeypatch.setattr(sample_mod, "take_sample_distributed", _spy)

    ds = read_csv_partitioned(str(csv), n_partitions=8)
    config = auto_configure_df(ds, confidence_required=False)
    assert config is not None

    assert collected_heights, "controller pulled no distributed sample"
    # Every driver-side sample is bounded by the cap, far below the full df.
    assert max(collected_heights) <= 20_000, collected_heights
    assert max(collected_heights) < n
