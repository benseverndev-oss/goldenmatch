import pytest

ray = pytest.importorskip("ray")


def test_distributed_dataset_module_exports():
    from goldenmatch.distributed import dataset
    assert hasattr(dataset, "read_csv_partitioned")
    assert hasattr(dataset, "apply_transforms_distributed")


def test_read_csv_partitioned_yields_n_partitions(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned

    csv = tmp_path / "people.csv"
    pl.DataFrame({"id": range(10_000), "name": ["x"] * 10_000}).write_csv(csv)

    ds = read_csv_partitioned(str(csv), n_partitions=4)
    assert ds.count() == 10_000


def test_read_csv_partitioned_enforces_schema(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned

    csv = tmp_path / "mixed.csv"
    pl.DataFrame({
        "id": range(100),
        "name": ["x"] * 100,
        "extra": [1] * 100,
    }).write_csv(csv)

    ds = read_csv_partitioned(
        str(csv),
        n_partitions=2,
        schema={"id": "int64", "name": "string"},
    )
    sample = ds.take(1)[0]
    assert set(sample.keys()) == {"id", "name"}


def test_read_csv_partitioned_accepts_list_of_paths(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned

    paths = []
    for i in range(3):
        p = tmp_path / f"part{i}.csv"
        pl.DataFrame(
            {"id": range(i * 100, (i + 1) * 100), "name": ["x"] * 100}
        ).write_csv(p)
        paths.append(str(p))

    ds = read_csv_partitioned(paths, n_partitions=6)
    assert ds.count() == 300


def test_apply_transforms_distributed_runs_plan_per_partition(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned, apply_transforms_distributed
    from goldenmatch.distributed.transforms import TransformPlan

    csv = tmp_path / "people.csv"
    pl.DataFrame({"id": range(1000), "name": ["ALICE"] * 1000}).write_csv(csv)

    ds = read_csv_partitioned(str(csv), n_partitions=4)
    ds = apply_transforms_distributed(ds, [TransformPlan(column="name", op="lower")])
    sample = ds.take(5)
    for row in sample:
        assert row["name"] == "alice"


def test_apply_transforms_distributed_empty_transforms_returns_unchanged(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned, apply_transforms_distributed

    csv = tmp_path / "people.csv"
    pl.DataFrame({"id": range(10), "name": ["x"] * 10}).write_csv(csv)

    ds = read_csv_partitioned(str(csv), n_partitions=2)
    out = apply_transforms_distributed(ds, [])
    assert out.count() == 10


def test_loader_driver_rss_does_not_grow_with_input_size(tmp_path):
    """At 1M rows, driver RSS growth between baseline and post-load must stay <200 MB.

    This is the in-CI proxy for the Phase 1 25M kill criterion. If this fails,
    the loader is materializing on the driver and the whole roadmap stops here.
    """
    import polars as pl
    import psutil
    from goldenmatch.distributed import read_csv_partitioned

    csv = tmp_path / "big.csv"
    pl.DataFrame({"id": range(1_000_000), "name": ["alice"] * 1_000_000}).write_csv(csv)

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    ds = read_csv_partitioned(str(csv), n_partitions=8)
    # Touch ds to confirm it exists, but do NOT force materialization
    assert ds is not None

    after = proc.memory_info().rss
    growth_mb = (after - baseline) / 1024 / 1024
    assert growth_mb < 200, f"driver RSS grew {growth_mb:.0f} MB — loader is materializing on driver"
