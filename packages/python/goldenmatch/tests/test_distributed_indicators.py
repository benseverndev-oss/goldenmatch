import pytest

ray = pytest.importorskip("ray")


def test_compute_column_priors_distributed_matches_in_memory(tmp_path):
    import polars as pl
    from goldenmatch.core.indicators import compute_column_priors
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.indicators import compute_column_priors_distributed

    df = pl.DataFrame({
        "email": [f"u{i}@example.com" for i in range(1000)],
        "name": ["Alice", "Bob", "ALICE", "alice"] * 250,
        "age": list(range(1000)),
    })
    csv = tmp_path / "in.csv"
    df.write_csv(csv)

    in_mem = compute_column_priors(df)
    ds = read_csv_partitioned(str(csv), n_partitions=4)
    distributed = compute_column_priors_distributed(ds)

    assert set(in_mem.keys()) == set(distributed.keys())
    for col in in_mem:
        # identity_score uses name regex heuristics -- must match exactly
        assert in_mem[col].identity_score == distributed[col].identity_score, col
        # corruption_score is sample-based; tolerance allowed
        assert abs(in_mem[col].corruption_score - distributed[col].corruption_score) < 0.2, col


def test_compute_column_priors_distributed_returns_correct_shape(tmp_path):
    import polars as pl
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.indicators import compute_column_priors_distributed

    pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}).write_csv(tmp_path / "f.csv")
    ds = read_csv_partitioned(str(tmp_path / "f.csv"), n_partitions=2)
    out = compute_column_priors_distributed(ds)
    assert isinstance(out, dict)
    for col, prior in out.items():
        assert isinstance(prior, ColumnPrior)


def test_estimate_sparse_match_signal_distributed_matches_in_memory(tmp_path):
    import polars as pl
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.indicators import estimate_sparse_match_signal_distributed

    df = pl.DataFrame({
        "email": [f"u{i}@example.com" for i in range(2000)],
        "phone": [f"555-{i:04d}" for i in range(2000)],
    })
    csv = tmp_path / "in.csv"
    df.write_csv(csv)

    in_mem = estimate_sparse_match_signal(df, exact_columns=["email"])
    ds = read_csv_partitioned(str(csv), n_partitions=4)
    distributed = estimate_sparse_match_signal_distributed(ds, exact_columns=["email"])
    assert in_mem.is_sparse == distributed.is_sparse


def test_dispatch_compute_column_priors_routes_by_type(tmp_path):
    import polars as pl
    from goldenmatch.core.indicators import dispatch_compute_column_priors
    from goldenmatch.distributed import read_csv_partitioned

    df = pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    in_mem = dispatch_compute_column_priors(df)
    assert isinstance(in_mem, dict)

    csv = tmp_path / "f.csv"
    df.write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=2)
    distributed = dispatch_compute_column_priors(ds)
    assert isinstance(distributed, dict)


def test_dispatch_estimate_sparse_match_signal_routes_by_type(tmp_path):
    import polars as pl
    from goldenmatch.core.indicators import dispatch_estimate_sparse_match_signal
    from goldenmatch.distributed import read_csv_partitioned

    df = pl.DataFrame({"email": [f"u{i}@x.com" for i in range(100)]})
    in_mem = dispatch_estimate_sparse_match_signal(df, exact_columns=["email"])
    assert in_mem is not None

    csv = tmp_path / "f.csv"
    df.write_csv(csv)
    ds = read_csv_partitioned(str(csv), n_partitions=2)
    distributed = dispatch_estimate_sparse_match_signal(ds, exact_columns=["email"])
    assert distributed is not None
