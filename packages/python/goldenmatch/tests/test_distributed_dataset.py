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
