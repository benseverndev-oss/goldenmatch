import pytest

ray = pytest.importorskip("ray")


def test_take_sample_distributed_returns_polars_with_expected_size(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned, take_sample_distributed

    csv = tmp_path / "big.csv"
    pl.DataFrame({"id": range(100_000), "name": ["x"] * 100_000}).write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=8)

    sample = take_sample_distributed(ds, sample_cap=20_000)
    assert isinstance(sample, pl.DataFrame)
    assert 5_000 < sample.height <= 20_000
    assert set(sample.columns) == {"id", "name"}


def test_take_sample_distributed_returns_all_when_dataset_smaller_than_cap(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned, take_sample_distributed

    csv = tmp_path / "small.csv"
    pl.DataFrame({"id": range(500), "name": ["x"] * 500}).write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=2)
    sample = take_sample_distributed(ds, sample_cap=20_000)
    assert sample.height == 500
