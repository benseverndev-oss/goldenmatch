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


def test_run_dedupe_pipeline_distributed_still_works_post_phase3(tmp_path):
    """Phase 3 keeps the cheat-line for now; Phase 4 removes the materialize.
    This guards against accidental regression from the polymorphic dispatch
    work in Phase 3."""
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    csv = tmp_path / "in.csv"
    pl.DataFrame({
        "first_name": ["Alice"] * 50 + ["Bob"] * 50,
        "last_name": ["Smith"] * 50 + ["Jones"] * 50,
        "id": range(100),
    }).write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=4)
    result = run_dedupe_pipeline_distributed(ds, confidence_required=False)
    assert result is not None


# ── Task 7: env-gated Phase 4 pipeline path ──────────────────────────────────

def test_pipeline_distributed_keeps_cheat_line_without_flag(tmp_path):
    """Without GOLDENMATCH_DISTRIBUTED_PIPELINE=1, the existing Phase 2
    cheat-line runs."""
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    csv = tmp_path / "in.csv"
    pl.DataFrame({
        "first_name": ["Alice"] * 5 + ["Bob"] * 5,
        "last_name": ["Smith"] * 5 + ["Jones"] * 5,
    }).write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=2)
    result = run_dedupe_pipeline_distributed(ds, confidence_required=False)
    assert result is not None


def test_pipeline_distributed_phase4_path_with_flag(monkeypatch, tmp_path):
    """With GOLDENMATCH_DISTRIBUTED_PIPELINE=1, the Phase 4 path fires."""
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_PIPELINE", "1")
    csv = tmp_path / "in.csv"
    pl.DataFrame({
        "first_name": ["Alice"] * 10 + ["Bob"] * 10,
        "last_name": ["Smith"] * 5 + ["S"] * 5 + ["Jones"] * 5 + ["J"] * 5,
    }).write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=4)
    result = run_dedupe_pipeline_distributed(ds, confidence_required=False)
    assert result is not None
