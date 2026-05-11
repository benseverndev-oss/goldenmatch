"""Pipeline-hook tests for Learning Memory (Phase 2).

Phase 4 (collection points) hasn't landed yet — these tests seed the
MemoryStore directly via store.add_correction(...) rather than going through
production code paths that capture corrections.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import polars as pl
import pytest
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    MemoryConfig,
)
from goldenmatch.core.memory.store import Correction, MemoryStore


def _build_config(db_path: str, *, memory_enabled: bool = True) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.75,
                fields=[
                    MatchkeyField(
                        field="name",
                        scorer="jaro_winkler",
                        transforms=["lowercase"],
                        weight=1.0,
                    ),
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"], transforms=["lowercase"])],
            maxBlockSize=1000,
            skipOversized=True,
        ),
        memory=MemoryConfig(enabled=memory_enabled, path=db_path),
    )


def _seed_reject(db_path: str, df: pl.DataFrame, id_a: int, id_b: int) -> None:
    """Seed a reject correction with empty hashes (row-ID match path).

    Empty hashes short-circuit the dual-hash staleness check — equivalent to
    the unmerge / REST collection points which lack df context.
    """
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        store.add_correction(
            Correction(
                id=str(uuid.uuid4()),
                id_a=id_a,
                id_b=id_b,
                decision="reject",
                source="steward",
                trust=1.0,
                field_hash="",
                record_hash="",
                original_score=0.95,
                matchkey_name=None,
                reason=None,
                dataset=None,
                created_at=datetime.now(),
            )
        )
    finally:
        store.close()


def test_pipeline_applies_seeded_correction(tmp_path):
    df = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )
    db_path = str(tmp_path / "mem.db")
    _seed_reject(db_path, df, 0, 1)

    config = _build_config(db_path)
    result = dedupe_df(df, config=config)

    # The (0, 1) pair was rejected — score must be overridden to 0.0.
    rejected_scores = [s for a, b, s in result.scored_pairs if (a, b) == (0, 1)]
    assert all(s == 0.0 for s in rejected_scores)
    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1


def test_pipeline_no_memory_stats_when_disabled():
    """Default config has no memory section — memory_stats should be None."""
    df = pl.DataFrame({"name": ["Acme Corp", "Beta Inc"], "zip": ["10001", "20002"]})
    config = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.75,
                fields=[
                    MatchkeyField(
                        field="name",
                        scorer="jaro_winkler",
                        transforms=["lowercase"],
                        weight=1.0,
                    ),
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"], transforms=["lowercase"])],
            maxBlockSize=1000,
            skipOversized=True,
        ),
    )
    result = dedupe_df(df, config=config)
    assert result.memory_stats is None


def test_pipeline_persists_stale_pairs_to_review_queue(tmp_path):
    """Stale corrections must be enqueued to a SQLite review queue colocated
    with the memory store, so the next `goldenmatch review` invocation
    surfaces them across processes."""
    import uuid as _uuid
    from pathlib import Path

    from goldenmatch.core.memory.store import Correction, MemoryStore
    from goldenmatch.core.review_queue import ReviewQueue

    df = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )
    db_path = tmp_path / "mem.db"
    # Seed a correction with non-empty hashes that won't match the test df,
    # forcing it to be classified as stale.
    store = MemoryStore(backend="sqlite", path=str(db_path))
    try:
        store.add_correction(
            Correction(
                id=str(_uuid.uuid4()),
                id_a=0,
                id_b=1,
                decision="reject",
                source="steward",
                trust=1.0,
                field_hash="bogus_field_hash_will_not_match",
                record_hash="bogus_record_hash_will_not_match",
                original_score=0.95,
                matchkey_name=None,
                reason=None,
                dataset=None,
                created_at=datetime.now(),
            )
        )
    finally:
        store.close()

    config = _build_config(str(db_path))
    result = dedupe_df(df, config=config)

    assert result.memory_stats is not None
    assert len(result.memory_stats.stale_pairs) >= 1

    # Sibling SQLite file should appear next to the memory store.
    queue_path = db_path.with_name("review_queue.db")
    assert Path(queue_path).exists(), f"queue file not at {queue_path}"

    # Querying the same backend should surface the stale pair.
    rq = ReviewQueue(backend="sqlite", path=str(queue_path))
    pending = rq.list_pending("memory_stale")
    rq.close()
    pair_ids = {(it.id_a, it.id_b) for it in pending}
    assert (0, 1) in pair_ids or (1, 0) in pair_ids


def test_pipeline_memory_disabled_does_not_open_store(tmp_path):
    df = pl.DataFrame({"name": ["Acme Corp", "Beta Inc"], "zip": ["10001", "20002"]})
    db_path = tmp_path / "mem.db"
    config = _build_config(str(db_path), memory_enabled=False)
    result = dedupe_df(df, config=config)
    assert result.memory_stats is None
    # MemoryConfig.enabled=False — the pipeline should never open the store.
    assert not db_path.exists()


def test_pipeline_continues_when_store_open_fails(tmp_path, caplog):
    """Garbage bytes at memory.db path: pipeline still returns valid DedupeResult.

    memory_stats is None (or marked failed) and the regular clusters are
    populated. A warning is logged describing the failure.
    """
    import logging

    df = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )
    db_path = tmp_path / "mem.db"
    # Write garbage so sqlite3.connect succeeds but executescript fails.
    db_path.write_bytes(b"this is not a sqlite database file at all" * 100)

    config = _build_config(str(db_path))
    with caplog.at_level(logging.WARNING):
        result = dedupe_df(df, config=config)

    assert result is not None
    assert result.clusters is not None
    # memory_stats should be None or marked as failed (commit 2 adds the
    # failure sentinel).
    if result.memory_stats is not None:
        assert getattr(result.memory_stats, "failed", False) is True
    # A warning should have been logged about the memory failure.
    assert any(
        "memory" in rec.message.lower() or "memorystore" in rec.message.lower()
        for rec in caplog.records
        if rec.levelname == "WARNING"
    )


# ── Tier 3.1: MemoryConfig.dataset validator ────────────────────────────


def test_memory_config_rejects_empty_dataset():
    """Empty string dataset is rejected at validation time."""
    import pydantic
    with pytest.raises(pydantic.ValidationError, match="non-empty"):
        MemoryConfig(dataset="")


def test_memory_config_rejects_whitespace_dataset():
    """Whitespace-only dataset is rejected (would silently fail downstream)."""
    import pydantic
    with pytest.raises(pydantic.ValidationError, match="non-empty"):
        MemoryConfig(dataset="   ")


def test_memory_config_accepts_none_dataset():
    """None remains valid (means: dataset filtering off)."""
    cfg = MemoryConfig(dataset=None)
    assert cfg.dataset is None


def test_memory_config_strips_dataset():
    """Padding whitespace is trimmed but real value preserved."""
    cfg = MemoryConfig(dataset="  prod  ")
    assert cfg.dataset == "prod"
