import pytest

ray = pytest.importorskip("ray")


def test_run_dedupe_pipeline_distributed_materializes_and_calls_in_memory(tmp_path):
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    csv = tmp_path / "in.csv"
    pl.DataFrame(
        {
            "id": range(100),
            "first_name": ["Alice"] * 50 + ["Bob"] * 50,
            "last_name": ["Smith"] * 50 + ["Jones"] * 50,
        }
    ).write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=4)

    result = run_dedupe_pipeline_distributed(ds, confidence_required=False)
    assert result is not None
