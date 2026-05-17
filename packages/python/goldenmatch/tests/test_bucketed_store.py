"""Unit tests for Component 2 v2 bucketed Parquet storage.

Spec: docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md
Replaces test_block_partitioned_store.py (deleted -- v1 API dropped).
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.distributed.record_store import (
    PreparedRecordStore,
    _sanitize_signature,
    iter_buckets,
    load_bucket,
    materialize_bucketed_blocks,
)


def _df(n_rows: int) -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": list(range(n_rows)),
        "name": [f"name_{i}" for i in range(n_rows)],
    })


def _assignments(n_rows: int, n_keys: int) -> dict[int, str]:
    """Round-robin assign rows to `n_keys` distinct block_keys."""
    return {i: f"k{i % n_keys}" for i in range(n_rows)}


def test_materialize_writes_at_most_n_files(tmp_path: Path):
    """Spec §Testing: small df + N=4 -> ≤ 4 files. Empty buckets skipped
    per §Error handling #4."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        df = _df(20)
        bucket_dir = materialize_bucketed_blocks(
            store, df,
            block_assignments=_assignments(20, n_keys=4),
            n_buckets=4,
            signature="sig-v1",
        )
        n_files = len(list(bucket_dir.glob("bucket=*/data.parquet")))
        assert 1 <= n_files <= 4


def test_load_bucket_roundtrip(tmp_path: Path):
    """Spec §Components #2: load_bucket reads a Parquet back as a Polars df."""
    p = tmp_path / "bucket=0" / "data.parquet"
    p.parent.mkdir(parents=True)
    df = pl.DataFrame({"__row_id__": [0, 1], "__block_key__": ["k0", "k0"]})
    df.write_parquet(p)
    loaded = load_bucket(p)
    assert loaded.shape == df.shape
    assert set(loaded.columns) == set(df.columns)


def test_iter_buckets_yields_sorted(tmp_path: Path):
    """Spec §Components #3: iter_buckets sorts by bucket_id."""
    for k in [3, 0, 2, 1]:
        d = tmp_path / f"bucket={k}"
        d.mkdir()
        pl.DataFrame({"__row_id__": [k]}).write_parquet(d / "data.parquet")
    ids = [bid for bid, _ in iter_buckets(tmp_path)]
    assert ids == [0, 1, 2, 3]


def test_iter_buckets_missing_directory_yields_empty(tmp_path: Path):
    """Spec §Components #3 missing-dir semantics: non-existent dir
    yields zero items, doesn't raise."""
    missing = tmp_path / "does_not_exist"
    assert list(iter_buckets(missing)) == []


def test_hash_is_deterministic_across_calls(tmp_path: Path):
    """Spec §Decisions: BUCKET_HASH_SEED pinned -> same block_key
    lands in same bucket on repeated runs.

    Also sanity-checks Polars hash API (`pl.col(x).hash(seed=u64)`) so
    a future Polars API change (e.g. seed_1..seed_4 split) fails this
    test loudly instead of silently corrupting bucket assignments at
    bench time.
    """
    # API sanity check first -- catches a Polars API regression
    # before we depend on the seed kwarg below.
    sanity = pl.DataFrame({"x": ["a", "b", "c"]})
    hashed = sanity.select(pl.col("x").hash(seed=0))
    assert hashed.dtypes[0] == pl.UInt64, (
        f"Polars hash dtype changed from UInt64 to {hashed.dtypes[0]} -- "
        f"bucket assignment will silently corrupt. Pin polars version "
        f"or update materialize_bucketed_blocks's hash expression."
    )
    df = _df(60)
    assignments = _assignments(60, n_keys=10)

    def bucket_for_key(store_path, key):
        # Find which bucket file contains rows with this block_key.
        for bid, path in iter_buckets(store_path.parent / f"buckets_{_sanitize_signature('sig-v1')}"):
            bucket_df = load_bucket(path)
            if key in bucket_df["__block_key__"].to_list():
                return bid
        raise AssertionError(f"key {key!r} not found in any bucket")

    (tmp_path / "run1").mkdir()
    with PreparedRecordStore(base_dir=tmp_path / "run1") as s1:
        materialize_bucketed_blocks(
            s1, df, block_assignments=assignments,
            n_buckets=4, signature="sig-v1",
        )
        b_first = {f"k{i}": bucket_for_key(s1.path, f"k{i}") for i in range(10)}
        _bucket_dir_1 = s1.path.parent / f"buckets_{_sanitize_signature('sig-v1')}"  # noqa: F841

    (tmp_path / "run2").mkdir()
    with PreparedRecordStore(base_dir=tmp_path / "run2") as s2:
        materialize_bucketed_blocks(
            s2, df, block_assignments=assignments,
            n_buckets=4, signature="sig-v1",
        )
        b_second = {f"k{i}": bucket_for_key(s2.path, f"k{i}") for i in range(10)}

    assert b_first == b_second


def test_n_buckets_bounds_validated():
    """Spec §Configuration: n_buckets in [1, 1024]; out-of-range raises
    at config construction."""
    GoldenMatchConfig(n_buckets=1)
    GoldenMatchConfig(n_buckets=1024)
    GoldenMatchConfig(n_buckets=None)  # heuristic default
    with pytest.raises(Exception):  # Pydantic ValidationError
        GoldenMatchConfig(n_buckets=0)
    with pytest.raises(Exception):
        GoldenMatchConfig(n_buckets=2000)


def test_empty_block_assignments_writes_zero_files(tmp_path: Path):
    """Edge case: empty assignments -> no-op materialize, no buckets,
    iter_buckets yields empty."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        bucket_dir = materialize_bucketed_blocks(
            store, _df(0),
            block_assignments={},
            n_buckets=4,
            signature="sig-empty",
        )
    assert list(iter_buckets(bucket_dir)) == []


def test_hash_distribution_skew_bounded(tmp_path: Path):
    """Spec §Testing: 10K block_keys hashed into N=32; max/min bucket
    size ratio ≤ 3. Guards against accidental seed change producing
    pathological skew."""
    n_keys = 10_000
    n_rows = n_keys  # one row per key for this test
    df = pl.DataFrame({
        "__row_id__": list(range(n_rows)),
        "name": [f"n_{i}" for i in range(n_rows)],
    })
    assignments = {i: f"key_{i}" for i in range(n_keys)}

    with PreparedRecordStore(base_dir=tmp_path) as store:
        bucket_dir = materialize_bucketed_blocks(
            store, df,
            block_assignments=assignments,
            n_buckets=32,
            signature="sig-skew",
        )
        sizes = []
        for _, path in iter_buckets(bucket_dir):
            sizes.append(load_bucket(path).height)
    assert min(sizes) > 0
    assert max(sizes) / min(sizes) <= 3.0
