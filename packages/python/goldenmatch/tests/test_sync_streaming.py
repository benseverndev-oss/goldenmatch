"""Tests for the streaming-block sync code path (#386).

Spec: docs/superpowers/specs/2026-05-21-streaming-block-sync-design.md
Plan: docs/superpowers/plans/2026-05-21-streaming-block-sync.md
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl
import pytest


@pytest.fixture
def staging_with_blocks(tmp_path) -> Path:
    """Write a small synthetic staging parquet with known block sizes.

    Three blocks by last_name, sizes [5, 3, 1] = 9 rows total. Lets us
    pin both the aggregation and the descending sort order.
    """
    df = pl.DataFrame({
        "last_name": (
            ["smith"] * 5 +
            ["jones"] * 3 +
            ["doe"] * 1
        ),
        "first_name": [f"f{i}" for i in range(9)],
        "id": list(range(9)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")
    return tmp_path


def _config_with_blocking(field: str = "last_name"):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
    )
    return GoldenMatchConfig(
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=[field])]),
    )


def test_index_block_sizes_streaming_groups_correctly(staging_with_blocks):
    """Step 1: _index_block_sizes returns one row per distinct block key
    with the correct count, sorted descending."""
    from goldenmatch.db.sync import _index_block_sizes

    cfg = _config_with_blocking()
    index = _index_block_sizes(staging_with_blocks, cfg)

    assert index.height == 3
    counts = index["count"].to_list()
    keys = index["__block_key__"].to_list()
    # Sorted desc by count
    assert counts == [5, 3, 1]
    # Sanity-check the keys correspond
    by_key = dict(zip(keys, counts))
    assert by_key["smith"] == 5
    assert by_key["jones"] == 3
    assert by_key["doe"] == 1


def test_index_block_sizes_filters_null_block_keys(tmp_path):
    """NULL block keys are excluded from the index. Matches the existing
    blocker._build_static_blocks behaviour: NULL keys don't form valid
    blocks, so they shouldn't appear in the streaming index either."""
    from goldenmatch.db.sync import _index_block_sizes

    df = pl.DataFrame({
        "last_name": ["smith", "smith", None, None, "jones"],
        "id": list(range(5)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _config_with_blocking()
    index = _index_block_sizes(tmp_path, cfg)

    assert "smith" in index["__block_key__"].to_list()
    assert "jones" in index["__block_key__"].to_list()
    assert None not in index["__block_key__"].to_list()
    # The two NULL rows must not appear under any key.
    assert index["count"].sum() == 3


def test_index_block_sizes_handles_no_blocking_config(tmp_path):
    """config.blocking is None -> single degenerate '__all__' block
    covering every row. Callers can iterate uniformly."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.db.sync import _index_block_sizes

    df = pl.DataFrame({"id": list(range(7))})
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = GoldenMatchConfig()  # no blocking
    index = _index_block_sizes(tmp_path, cfg)

    assert index.height == 1
    assert index["__block_key__"].to_list() == ["__all__"]
    assert index["count"].to_list() == [7]


def test_index_block_sizes_streams_across_multiple_chunks(tmp_path):
    """Staging dir has multiple parquet chunks (the real shape after
    _read_all_lazy stages 100s of chunks). The index pass must aggregate
    across all chunks."""
    from goldenmatch.db.sync import _index_block_sizes

    # Three chunks, "smith" spans 1 and 2; "jones" spans 2 and 3.
    pl.DataFrame({"last_name": ["smith"] * 3, "id": [0, 1, 2]}).write_parquet(
        tmp_path / "chunk_000000.parquet",
    )
    pl.DataFrame({
        "last_name": ["smith", "jones", "jones"],
        "id": [3, 4, 5],
    }).write_parquet(tmp_path / "chunk_000001.parquet")
    pl.DataFrame({"last_name": ["jones", "doe"], "id": [6, 7]}).write_parquet(
        tmp_path / "chunk_000002.parquet",
    )

    cfg = _config_with_blocking()
    index = _index_block_sizes(tmp_path, cfg)

    by_key = dict(zip(
        index["__block_key__"].to_list(),
        index["count"].to_list(),
    ))
    assert by_key["smith"] == 4   # 3 from chunk 0 + 1 from chunk 1
    assert by_key["jones"] == 3   # 2 from chunk 1 + 1 from chunk 2
    assert by_key["doe"] == 1


# ---------------------------------------------------------------------------
# Step 2: _score_block_streaming
# ---------------------------------------------------------------------------


def _kernel_config(df: pl.DataFrame, block_field: str = "last_name"):
    """Build a driver-committed config the streaming kernel can use.

    Mirrors what the run_sync orchestrator does in the streaming path:
    auto-configures once on the input, then ships the committed config
    to per-block scoring.
    """
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.autoconfig import auto_configure_df

    cfg = auto_configure_df(
        df, confidence_required=False, _skip_finalize=True,
    )
    # Force a known blocking key so test fixtures are predictable.
    cfg.blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=[block_field])])
    cfg.backend = "bucket"
    return cfg


def test_score_block_streaming_only_reads_one_block(tmp_path):
    """The per-block scorer must filter to ONE block_key. Pairs returned
    must only involve rows from that block, never cross-block."""
    from goldenmatch.db.sync import _score_block_streaming

    # Two blocks. smith records share first_name "alice" (should match
    # within block); jones records share first_name "bob" (also match).
    # If the scorer leaks across blocks, alice-vs-bob pairs would emerge.
    df = pl.DataFrame({
        "last_name": ["smith"] * 4 + ["jones"] * 4,
        "first_name": ["alice"] * 4 + ["bob"] * 4,
        "id": list(range(8)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)
    matched_pairs: set[tuple[int, int]] = set()
    pairs = _score_block_streaming(tmp_path, "smith", cfg, matched_pairs)

    # Every pair's row_ids must come from the smith block (rows 0-3).
    # The kernel uses scan-local row_ids (with_row_index inside the
    # kernel), so the IDs start at 0 within the block frame -- the
    # invariant we test is "all row_ids are < 4" since the smith block
    # is 4 rows.
    assert len(pairs) > 0
    for a, b, _s in pairs:
        assert 0 <= a < 4
        assert 0 <= b < 4


def test_score_block_streaming_mutates_matched_pairs(tmp_path):
    """matched_pairs is the driver-side cross-block dedup set. The
    streaming primitive must add to it in place."""
    from goldenmatch.db.sync import _score_block_streaming

    df = pl.DataFrame({
        "last_name": ["smith"] * 4,
        "first_name": ["alice"] * 4,
        "id": list(range(4)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)
    matched_pairs: set[tuple[int, int]] = set()
    pairs = _score_block_streaming(tmp_path, "smith", cfg, matched_pairs)

    assert len(matched_pairs) == len(pairs)
    # Every canonical pair from the result is in the set.
    for a, b, _s in pairs:
        assert (min(a, b), max(a, b)) in matched_pairs


def test_score_block_streaming_dedupes_against_matched_pairs(tmp_path):
    """Pairs that already exist in matched_pairs (from a prior block's
    scoring or an external seed) must be filtered out of the new return."""
    from goldenmatch.db.sync import _score_block_streaming

    df = pl.DataFrame({
        "last_name": ["smith"] * 4,
        "first_name": ["alice"] * 4,
        "id": list(range(4)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)

    # First call: learn what pairs the kernel emits for this block.
    matched_pairs1: set[tuple[int, int]] = set()
    pairs1 = _score_block_streaming(tmp_path, "smith", cfg, matched_pairs1)
    assert len(pairs1) > 0

    # Second call with the same set already populated: should dedupe to []
    matched_pairs2 = set(matched_pairs1)
    pairs2 = _score_block_streaming(tmp_path, "smith", cfg, matched_pairs2)
    assert pairs2 == []
    assert matched_pairs2 == matched_pairs1  # set unchanged


def test_score_block_streaming_skips_singleton_blocks(tmp_path):
    """A block with < 2 rows can't produce pairs. Return empty list
    fast without invoking the scoring kernel."""
    from goldenmatch.db.sync import _score_block_streaming

    df = pl.DataFrame({
        "last_name": ["smith"] * 3 + ["doe"],
        "first_name": ["alice", "alice", "alice", "bob"],
        "id": [0, 1, 2, 3],
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)
    matched_pairs: set[tuple[int, int]] = set()
    pairs = _score_block_streaming(tmp_path, "doe", cfg, matched_pairs)
    assert pairs == []


# ---------------------------------------------------------------------------
# Step 3: _full_scan_streaming orchestrator (equivalence + return shape)
# ---------------------------------------------------------------------------


class _FakeConnector:
    """Mock DatabaseConnector that captures writes without actually
    hitting Postgres. Lets us assert match_log + golden_records were
    called without standing up testing.postgresql."""

    def __init__(self):
        self.match_log_calls: list[list] = []
        self.golden_calls: list = []
        self.state_calls: list = []

    # The minimal surface _full_scan_streaming touches.
    def execute(self, *args, **kwargs):
        return None

    def close(self):
        pass


def _capture_log_matches_batch(monkeypatch, fake_conn):
    """Patch log_matches_batch so we can assert it was called."""
    from goldenmatch.db import sync as sync_mod

    def fake(connector, actions, run_id):
        fake_conn.match_log_calls.append(actions)

    monkeypatch.setattr(sync_mod, "log_matches_batch", fake)


def _capture_writes(monkeypatch, fake_conn):
    """Patch write_golden_records + update_state too."""
    from goldenmatch.db import sync as sync_mod

    def fake_golden(connector, clusters, golden_df, source_table, output_mode):
        fake_conn.golden_calls.append((clusters, golden_df))

    def fake_state(connector, source_table, **kwargs):
        fake_conn.state_calls.append(kwargs)

    monkeypatch.setattr(sync_mod, "write_golden_records", fake_golden)
    monkeypatch.setattr(sync_mod, "update_state", fake_state)


def test_full_scan_streaming_returns_expected_dict_shape(tmp_path, monkeypatch):
    """Step 3: orchestrator returns the same dict shape as
    _full_scan_pipeline so callers see no API difference."""
    from goldenmatch.db.sync import _full_scan_streaming

    df = pl.DataFrame({
        "last_name": ["smith"] * 4 + ["jones"] * 4,
        "first_name": ["alice"] * 4 + ["bob"] * 4,
        "id": list(range(8)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)
    fake_conn = _FakeConnector()
    _capture_log_matches_batch(monkeypatch, fake_conn)
    _capture_writes(monkeypatch, fake_conn)

    result = _full_scan_streaming(
        connector=fake_conn,
        staging_dir=tmp_path,
        source_table="test_table",
        config=cfg,
        matchkeys=cfg.get_matchkeys(),
        output_mode="separate",
        dry_run=False,
        run_id="run_1",
        cfg_hash="hash_1",
        total_rows=8,
    )

    # Shape contract from _full_scan_pipeline.
    assert set(result.keys()) >= {
        "new_records", "matches", "clusters", "golden_records",
        "actions", "run_id",
    }
    assert result["new_records"] == 8
    assert result["run_id"] == "run_1"
    # Two distinct blocks each with intra-block matches -> > 0 pairs.
    assert result["matches"] > 0


def test_full_scan_streaming_writes_incrementally(tmp_path, monkeypatch):
    """Step 3: match log writes fire as blocks complete, not all at the
    end. Lets a long sync show progress as blocks finish.

    #424: post-#424, writes batch via GOLDENMATCH_MATCH_LOG_FLUSH_PAIRS
    (default 10K) so the COPY-based path amortizes commit overhead.
    Set the env to 1 here to verify the per-block incremental contract
    still works when buffering is disabled.
    """
    from goldenmatch.db.sync import _full_scan_streaming

    monkeypatch.setenv("GOLDENMATCH_MATCH_LOG_FLUSH_PAIRS", "1")
    monkeypatch.setenv("GOLDENMATCH_STREAMING_BLOCK_WORKERS", "1")

    # Three distinct blocks of 3 rows each -- 3 calls to log_matches_batch
    # if streaming writes correctly.
    df = pl.DataFrame({
        "last_name": ["a"] * 3 + ["b"] * 3 + ["c"] * 3,
        "first_name": ["x"] * 9,
        "id": list(range(9)),
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)
    fake_conn = _FakeConnector()
    _capture_log_matches_batch(monkeypatch, fake_conn)
    _capture_writes(monkeypatch, fake_conn)

    _full_scan_streaming(
        connector=fake_conn,
        staging_dir=tmp_path,
        source_table="t",
        config=cfg,
        matchkeys=cfg.get_matchkeys(),
        output_mode="separate",
        dry_run=False,
        run_id="r",
        cfg_hash="h",
        total_rows=9,
    )

    # At least 2 calls -- proves incremental writes when flush threshold
    # is set low. Could be exactly 3 or fewer if a block emits zero
    # pairs.
    assert len(fake_conn.match_log_calls) >= 2, (
        "streaming sync must write match log incrementally per block "
        "(with GOLDENMATCH_MATCH_LOG_FLUSH_PAIRS=1); "
        f"got {len(fake_conn.match_log_calls)} call(s)"
    )


def test_full_scan_streaming_dry_run_skips_writes(tmp_path, monkeypatch):
    """dry_run=True: no writes to log/golden/state."""
    from goldenmatch.db.sync import _full_scan_streaming

    df = pl.DataFrame({
        "last_name": ["a"] * 3,
        "first_name": ["x"] * 3,
        "id": [0, 1, 2],
    })
    df.write_parquet(tmp_path / "chunk_000000.parquet")

    cfg = _kernel_config(df)
    fake_conn = _FakeConnector()
    _capture_log_matches_batch(monkeypatch, fake_conn)
    _capture_writes(monkeypatch, fake_conn)

    _full_scan_streaming(
        connector=fake_conn,
        staging_dir=tmp_path,
        source_table="t",
        config=cfg,
        matchkeys=cfg.get_matchkeys(),
        output_mode="separate",
        dry_run=True,
        run_id="r",
        cfg_hash="h",
        total_rows=3,
    )

    assert fake_conn.match_log_calls == []
    assert fake_conn.golden_calls == []
    assert fake_conn.state_calls == []


# ---------------------------------------------------------------------------
# Step 4: threshold routing in run_sync
# ---------------------------------------------------------------------------


def test_run_sync_routes_to_streaming_above_threshold(monkeypatch):
    """Step 4: total_rows > GOLDENMATCH_SYNC_STREAMING_THRESHOLD ->
    _full_scan_streaming is called, not _full_scan_pipeline."""
    from unittest.mock import MagicMock

    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.db import sync as sync_mod

    monkeypatch.setenv("GOLDENMATCH_SYNC_STREAMING_THRESHOLD", "100")

    # Mock the underlying pipelines so we can detect which one fired.
    streaming_called = {"count": 0}
    legacy_called = {"count": 0}

    def fake_streaming(*a, **kw):
        streaming_called["count"] += 1
        return {"new_records": 0, "matches": 0, "actions": []}

    def fake_legacy(*a, **kw):
        legacy_called["count"] += 1
        return {"new_records": 0, "matches": 0, "actions": []}

    monkeypatch.setattr(sync_mod, "_full_scan_streaming", fake_streaming)
    monkeypatch.setattr(sync_mod, "_full_scan_pipeline", fake_legacy)

    # Mock the read step so we don't need a Postgres connector.
    fake_lf = pl.DataFrame({"id": [1, 2, 3]}).lazy()
    staging_path = Path(tempfile.mkdtemp(prefix="gm_route_test_"))
    monkeypatch.setattr(
        sync_mod, "_read_all_lazy",
        lambda *a, **kw: (fake_lf, staging_path),
    )
    monkeypatch.setattr(sync_mod, "ensure_metadata_tables", lambda *a, **kw: None)
    monkeypatch.setattr(sync_mod, "get_state", lambda *a, **kw: None)

    fake_conn = MagicMock()
    fake_conn.get_row_count.return_value = 1000  # > threshold of 100

    sync_mod.run_sync(
        connector=fake_conn,
        source_table="t",
        config=GoldenMatchConfig(),
        full_rescan=True,
    )

    assert streaming_called["count"] == 1, "should have routed to streaming"
    assert legacy_called["count"] == 0, "should NOT have called legacy"


def test_run_sync_uses_legacy_path_below_threshold(monkeypatch):
    """Step 4: total_rows <= threshold -> _full_scan_pipeline (legacy)."""
    from unittest.mock import MagicMock

    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.db import sync as sync_mod

    monkeypatch.setenv("GOLDENMATCH_SYNC_STREAMING_THRESHOLD", "10000")

    streaming_called = {"count": 0}
    legacy_called = {"count": 0}

    monkeypatch.setattr(
        sync_mod, "_full_scan_streaming",
        lambda *a, **kw: streaming_called.__setitem__("count", streaming_called["count"] + 1) or {},
    )
    monkeypatch.setattr(
        sync_mod, "_full_scan_pipeline",
        lambda *a, **kw: legacy_called.__setitem__("count", legacy_called["count"] + 1) or {},
    )

    fake_lf = pl.DataFrame({"id": [1, 2, 3]}).lazy()
    staging_path = Path(tempfile.mkdtemp(prefix="gm_route_test_"))
    monkeypatch.setattr(
        sync_mod, "_read_all_lazy",
        lambda *a, **kw: (fake_lf, staging_path),
    )
    monkeypatch.setattr(sync_mod, "ensure_metadata_tables", lambda *a, **kw: None)
    monkeypatch.setattr(sync_mod, "get_state", lambda *a, **kw: None)

    fake_conn = MagicMock()
    fake_conn.get_row_count.return_value = 1000  # < threshold of 10000

    sync_mod.run_sync(
        connector=fake_conn,
        source_table="t",
        config=GoldenMatchConfig(),
        full_rescan=True,
    )

    assert legacy_called["count"] == 1
    assert streaming_called["count"] == 0


def test_default_threshold_routes_million_row_table_to_streaming(monkeypatch):
    """#401: with the env var UNSET (default threshold), a real-world
    1.13M-row sync must route to streaming-block. Pins the default
    against future regressions that re-raise the threshold and break
    8 GB sandbox users.

    The historical default of 5M (from #386) failed open on 8 GB hosts;
    the 500K default keeps memory bounded for any meaningful table.
    """
    from unittest.mock import MagicMock

    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.db import sync as sync_mod

    # Explicitly DELETE the env var so we exercise the default.
    monkeypatch.delenv("GOLDENMATCH_SYNC_STREAMING_THRESHOLD", raising=False)

    streaming_called = {"count": 0}
    legacy_called = {"count": 0}

    monkeypatch.setattr(
        sync_mod, "_full_scan_streaming",
        lambda *a, **kw: streaming_called.__setitem__("count", streaming_called["count"] + 1) or {},
    )
    monkeypatch.setattr(
        sync_mod, "_full_scan_pipeline",
        lambda *a, **kw: legacy_called.__setitem__("count", legacy_called["count"] + 1) or {},
    )

    fake_lf = pl.DataFrame({"id": [1, 2, 3]}).lazy()
    staging_path = Path(tempfile.mkdtemp(prefix="gm_route_test_"))
    monkeypatch.setattr(
        sync_mod, "_read_all_lazy",
        lambda *a, **kw: (fake_lf, staging_path),
    )
    monkeypatch.setattr(sync_mod, "ensure_metadata_tables", lambda *a, **kw: None)
    monkeypatch.setattr(sync_mod, "get_state", lambda *a, **kw: None)

    fake_conn = MagicMock()
    fake_conn.get_row_count.return_value = 1_131_769  # #401's actual table size

    sync_mod.run_sync(
        connector=fake_conn,
        source_table="t",
        config=GoldenMatchConfig(),
        full_rescan=True,
    )

    assert streaming_called["count"] == 1, (
        "1.13M-row sync must route to streaming with the default threshold. "
        "If this fails, the default has crept back up past 1.13M and 8 GB "
        "sandbox users will OOM again. See #401."
    )
    assert legacy_called["count"] == 0


def test_default_threshold_keeps_small_tables_on_legacy_path(monkeypatch):
    """Companion to the #401 default-threshold test: a small table
    (< 500K rows) still routes to the faster legacy path with the
    default threshold."""
    from unittest.mock import MagicMock

    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.db import sync as sync_mod

    monkeypatch.delenv("GOLDENMATCH_SYNC_STREAMING_THRESHOLD", raising=False)

    streaming_called = {"count": 0}
    legacy_called = {"count": 0}

    monkeypatch.setattr(
        sync_mod, "_full_scan_streaming",
        lambda *a, **kw: streaming_called.__setitem__("count", streaming_called["count"] + 1) or {},
    )
    monkeypatch.setattr(
        sync_mod, "_full_scan_pipeline",
        lambda *a, **kw: legacy_called.__setitem__("count", legacy_called["count"] + 1) or {},
    )

    fake_lf = pl.DataFrame({"id": [1, 2, 3]}).lazy()
    staging_path = Path(tempfile.mkdtemp(prefix="gm_route_test_"))
    monkeypatch.setattr(
        sync_mod, "_read_all_lazy",
        lambda *a, **kw: (fake_lf, staging_path),
    )
    monkeypatch.setattr(sync_mod, "ensure_metadata_tables", lambda *a, **kw: None)
    monkeypatch.setattr(sync_mod, "get_state", lambda *a, **kw: None)

    fake_conn = MagicMock()
    fake_conn.get_row_count.return_value = 100_000  # well below 500K

    sync_mod.run_sync(
        connector=fake_conn,
        source_table="t",
        config=GoldenMatchConfig(),
        full_rescan=True,
    )

    assert legacy_called["count"] == 1
    assert streaming_called["count"] == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
