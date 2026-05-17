"""Unit + integration tests for Component 3 (distributed scoring).

All tests gated on `ray` being importable; the file's collection
falls through (no errors) when the [ray] extra isn't installed.

Phase 2: bucket-mode dispatch tests added. _build_small_blocks helper
updated to write buckets via materialize_bucketed_blocks (v2 API).
"""
from __future__ import annotations

import polars as pl
import pytest

ray = pytest.importorskip("ray")


# ── Shared fixtures + helpers (used by Phase 2, 3, and 4 tests) ─────


@pytest.fixture(scope="module")
def _ray_local():
    """Module-scoped Ray init so we pay startup once across all integration
    tests. ignore_reinit_error in case earlier tests already touched ray."""
    ray.init(
        local_mode=False, ignore_reinit_error=True,
        num_cpus=2, logging_level="WARNING",
    )
    yield
    ray.shutdown()


def _build_small_blocks(tmp_path):
    """Materialize a small df to bucketed Parquet (Component 2 v2).
    Returns (store_path, signature, blocks_list) for backward compat
    with existing tests. blocks_list is the in-memory BlockResult list
    df-mode uses; bucket-mode reads buckets via the store_path."""
    from goldenmatch.core.blocker import BlockResult
    from goldenmatch.distributed.record_store import (
        PreparedRecordStore,
        materialize_bucketed_blocks,
        materialize_prepared_records,
    )

    df = pl.DataFrame({
        "__row_id__":  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "name":        ["alice", "alice2", "bob", "bob2", "carol", "carol2", "dan", "dan2", "eve", "eve2"],
        "__mk_name__": ["alice", "alice",  "bob", "bob",  "carol", "carol",  "dan", "dan",  "eve", "eve"],
    })
    block_assignments = {
        0: "A", 1: "A",
        2: "B", 3: "B",
        4: "C", 5: "C",
        6: "D", 7: "D",
        8: "E", 9: "E",
    }
    store_path = tmp_path / "store.duckdb"
    with PreparedRecordStore(path=store_path, cleanup=False) as store:
        materialize_prepared_records(store, df, signature="sig-v1")
        materialize_bucketed_blocks(
            store, df,
            block_assignments=block_assignments,
            n_buckets=8,
            signature="sig-v1",
        )

    blocks = [
        BlockResult(
            block_key=k,
            df=df.filter(
                pl.col("__row_id__").is_in(
                    [r for r, v in block_assignments.items() if v == k]
                )
            ).lazy(),
            strategy="static",
        )
        for k in sorted(set(block_assignments.values()))
    ]
    return str(store_path), "sig-v1", blocks


# ── Unit tests (no real Ray runtime) ────────────────────────────────

def test_key_mode_block_shim_exposes_required_attributes():
    """_KeyModeBlock must satisfy the _score_one_block contract: .block_key
    (str) and .df (LazyFrame). Module-level dataclass so Ray pickling
    resolves it; a nested class breaks serialization."""
    from goldenmatch.backends.ray_backend import _KeyModeBlock
    df = pl.DataFrame({"__row_id__": [0], "name": ["a"]})
    block = _KeyModeBlock(block_key="key-1", df=df.lazy())
    assert block.block_key == "key-1"
    assert block.df is not None
    # Frozen dataclass: assignment must raise.
    with pytest.raises(Exception):  # noqa: B017 -- frozen dataclass error class
        block.block_key = "mutated"


def test_pair_bytes_estimate_constant_is_module_level():
    """_PAIR_BYTES_ESTIMATE must be importable + finite + > 0 (Phase 3
    OOM guard depends on it). Anchors the constant against accidental
    deletion."""
    from goldenmatch.backends.ray_backend import _PAIR_BYTES_ESTIMATE
    assert isinstance(_PAIR_BYTES_ESTIMATE, int)
    assert _PAIR_BYTES_ESTIMATE > 0


def test_score_blocks_ray_signature_accepts_new_kwargs():
    """score_blocks_ray must accept store_path + signature kwargs without
    raising TypeError, even when ray isn't actually initialized. Achieved
    by short-circuiting on empty blocks list (returns [] early)."""
    from goldenmatch.backends import ray_backend
    result = ray_backend.score_blocks_ray(
        [], mk=None, matched_pairs=set(),
        store_path="/tmp/store.duckdb",
        signature="sig-v1",
    )
    assert result == []


# ── Integration tests (require real Ray runtime) ─────────────────────


def test_bucket_mode_equivalence_with_df_mode(_ray_local, tmp_path):
    """Same input -> same canonical pair set whether df-mode or bucket-mode.
    Set comparison, not list -- ordering is non-deterministic across
    buckets (spec §Testing)."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    pairs_df = ray_backend.score_blocks_ray(blocks, mk, set())
    pairs_bucket = ray_backend.score_blocks_ray(
        blocks, mk, set(),
        store_path=store_path, signature=sig,
    )

    def canon(p):
        a, b, s = p
        return (min(a, b), max(a, b), round(s, 6))
    assert {canon(p) for p in pairs_bucket} == {canon(p) for p in pairs_df}


def test_bucket_mode_dispatches_n_tasks(_ray_local, tmp_path, caplog):
    """Driver submits one Ray task per non-empty bucket, not per block.

    Captured via the `logger.info("Submitted %d ... Ray ...")` line in
    score_blocks_ray. The actual count of futures is internal to the
    function (in-function @ray.remote task can't be monkey-patched from
    outside the function scope), so we assert via the structured log
    line that's emitted right after futures are built.
    """
    import logging
    import re

    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    with caplog.at_level(logging.INFO, logger="goldenmatch.backends.ray_backend"):
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature=sig,
        )

    # Find the "Submitted N buckets ... bucket mode" line.
    submitted_lines = [
        r for r in caplog.records
        if "Submitted" in r.getMessage() and "bucket mode" in r.getMessage()
    ]
    assert len(submitted_lines) == 1, (
        f"expected exactly one 'Submitted ... bucket mode' log line; "
        f"got {len(submitted_lines)}. Records: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    # The fixture has 5 distinct block_keys (A..E) and n_buckets=8;
    # actual bucket count is <= 5 due to empty-bucket skipping. Just
    # assert it's <= len(blocks) and > 0.
    submitted_msg = submitted_lines[0].getMessage()
    n_submitted = int(re.search(r"Submitted (\d+)", submitted_msg).group(1))
    assert 0 < n_submitted <= len(blocks), (
        f"submitted {n_submitted} tasks; expected 0 < n <= len(blocks)={len(blocks)}"
    )


def test_oom_guard_fires_at_bucket_granularity(_ray_local, tmp_path, monkeypatch):
    """Spec §Error handling #6: OOM guard still works when N futures
    are buckets instead of blocks."""
    import psutil
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    class FakeMem:
        available = 80
    monkeypatch.setattr(psutil, "virtual_memory", lambda: FakeMem)

    with pytest.raises(MemoryError, match="scored pairs"):
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature=sig,
        )
