"""Pipeline-hook tests for Learning Memory (Phase 2).

Phase 4 (collection points) hasn't landed yet — these tests seed the
MemoryStore directly via store.add_correction(...) rather than going through
production code paths that capture corrections.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import polars as pl

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


def test_pipeline_memory_disabled_does_not_open_store(tmp_path):
    df = pl.DataFrame({"name": ["Acme Corp", "Beta Inc"], "zip": ["10001", "20002"]})
    db_path = tmp_path / "mem.db"
    config = _build_config(str(db_path), memory_enabled=False)
    result = dedupe_df(df, config=config)
    assert result.memory_stats is None
    # MemoryConfig.enabled=False — the pipeline should never open the store.
    assert not db_path.exists()
