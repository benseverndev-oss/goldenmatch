"""Unit + integration tests for Component 3 (distributed scoring).

All tests gated on `ray` being importable; the file's collection
falls through (no errors) when the [ray] extra isn't installed.
"""
from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

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


def _build_small_blocks(tmp_path: Path):
    """Materialize a small df to a PreparedRecordStore split across 5 blocks
    of 1-2 rows each. Returns (store_path, signature, blocks list).

    Total blocks > 4 so the small-block fast path doesn't engage.
    Used by Phase 3 (OOM guard) and Phase 4 (integration) tests.
    """
    from goldenmatch.core.blocker import BlockResult
    from goldenmatch.distributed.record_store import (
        PreparedRecordStore,
        materialize_blocks,
        materialize_prepared_records,
    )
    # Every block has 2 rows that share __mk_name__, so every block
    # emits at least 1 pair. Required for the Phase 3 OOM test to be
    # deterministic: if any block returned 0 pairs, the cumulative
    # pair counter could stay at 0 long enough for the loop to drain
    # without tripping the guard. 5 multi-row blocks > 4 -> key-mode
    # engages (no small-block fallback).
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
        materialize_blocks(
            store, df, block_assignments=block_assignments, signature="sig-v1",
        )

    # Build BlockResult shells. df-mode reads .df; key-mode ignores it.
    # BlockResult requires `strategy=` per its dataclass; pass "static".
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


def test_driver_oom_guard_raises_when_budget_exceeded(_ray_local, tmp_path: Path, monkeypatch):
    """End-to-end: with psutil claiming near-zero available memory, the
    incremental gather must trip the OOM guard and raise MemoryError
    citing 'scored pairs'.

    Uses real Ray + real PreparedRecordStore so the guard's interaction
    with ray.wait + ray.cancel + ray.get is exercised, not stubbed."""
    import psutil
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    # Score on __mk_name__ with exact scorer -- within each block both rows
    # share an identical __mk_name__ value, so every block emits exactly 1
    # pair. This guarantees n_pairs > 0 regardless of psutil state.
    mk = MatchkeyConfig(
        name="mk_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    # Pretend the system has 80 bytes of available memory total ->
    # budget_pairs = 80 * 0.5 / 80 = 0. Any non-empty pair list trips.
    class FakeMem:
        available = 80
    monkeypatch.setattr(psutil, "virtual_memory", lambda: FakeMem)

    with pytest.raises(MemoryError, match="scored pairs"):
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature=sig,
        )


def test_driver_oom_guard_passes_under_normal_memory(_ray_local, tmp_path: Path):
    """Sanity: with real psutil reporting actual system memory, the
    guard does NOT fire on a tiny test fixture. Anchors that the guard
    isn't pathologically tight."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    # Score on __mk_name__ -- same as the OOM test so both tests exercise
    # the same code path; guard difference is purely the psutil mock.
    mk = MatchkeyConfig(
        name="mk_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )
    # Should return normally (pairs depend on fixture content; just
    # assert no exception).
    pairs = ray_backend.score_blocks_ray(
        blocks, mk, set(),
        store_path=store_path, signature=sig,
    )
    assert isinstance(pairs, list)


# ── Phase 4 tests: real-Ray equivalence + block-not-found + cross-process ─


def test_key_mode_equivalence_with_df_mode(_ray_local, tmp_path: Path):
    """Same input -> same pairs whether key-mode or df-mode. Locks in the
    semantic invariant; without this, key-mode silently producing
    different pairs would be unobservable."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
    )

    pairs_df_mode = ray_backend.score_blocks_ray(blocks, mk, set())
    pairs_key_mode = ray_backend.score_blocks_ray(
        blocks, mk, set(),
        store_path=store_path, signature=sig,
    )

    # Pairs may arrive in different orders; compare as sets after
    # canonicalizing (id_a, id_b) order.
    def canon(p):
        a, b, s = p
        return (min(a, b), max(a, b), round(s, 6))

    assert {canon(p) for p in pairs_key_mode} == {canon(p) for p in pairs_df_mode}


def test_key_mode_block_not_found_raises_runtime_error(_ray_local, tmp_path: Path):
    """Pass a wrong signature; worker raises RuntimeError citing both
    likely root causes (signature drift, block_assignments off-by-one)."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, _good_sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
    )

    with pytest.raises(Exception) as exc_info:
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature="WRONG-SIG",
        )
    msg = str(exc_info.value)
    assert "signature drift" in msg
    assert "block_assignments" in msg


def _worker_open_and_read(store_path: str, signature: str, block_key: str, queue):
    """Top-level (picklable) target for multiprocessing.Process.

    Opens a read-only PreparedRecordStore, calls load_block, puts the
    row count (or exception) on the queue.
    """
    try:
        from goldenmatch.distributed.record_store import (
            PreparedRecordStore,
            load_block,
        )
        store = PreparedRecordStore(path=store_path, cleanup=False, read_only=True)
        try:
            df = load_block(store, signature=signature, block_key=block_key)
            queue.put(("ok", df.height if df is not None else None))
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001 -- preserve in the queue
        queue.put(("err", repr(e)))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific concurrent-read regression anchor")
def test_two_processes_can_read_same_store_concurrently(tmp_path: Path):
    """Spawn 2 multiprocessing.Process workers that each open the same
    DuckDB file read-only and call load_block simultaneously. Both must
    succeed. This is the direct test of the spec Error handling Windows
    concurrent-read concern; Ray's local mode might serialize workers
    and paper over the issue."""
    store_path, sig, _ = _build_small_blocks(tmp_path)

    ctx = mp.get_context("spawn")
    q1 = ctx.Queue()
    q2 = ctx.Queue()
    p1 = ctx.Process(target=_worker_open_and_read, args=(store_path, sig, "A", q1))
    p2 = ctx.Process(target=_worker_open_and_read, args=(store_path, sig, "B", q2))
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)

    r1 = q1.get(timeout=5)
    r2 = q2.get(timeout=5)
    assert r1[0] == "ok", f"worker 1 failed: {r1}"
    assert r2[0] == "ok", f"worker 2 failed: {r2}"
    assert r1[1] is not None and r1[1] > 0
    assert r2[1] is not None and r2[1] > 0
