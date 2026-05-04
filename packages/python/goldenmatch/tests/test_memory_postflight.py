"""Postflight rendering tests for Learning Memory (Phase 3).

These tests verify that when memory is enabled and corrections are applied,
the postflight report's rendered string surfaces a one-line memory section.
Empty/zero memory_stats omit the section entirely.
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
from goldenmatch.core.autoconfig_verify import PostflightReport
from goldenmatch.core.memory.corrections import CorrectionStats
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


def _seed_reject(db_path: str, id_a: int, id_b: int) -> None:
    """Seed an empty-hash reject correction (always-applies path)."""
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


def test_postflight_renders_memory_section(tmp_path):
    """A correction was applied; postflight string surfaces 'Memory:' line."""
    df = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )
    db_path = str(tmp_path / "mem.db")
    _seed_reject(db_path, 0, 1)

    config = _build_config(db_path)
    result = dedupe_df(df, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1
    text = str(result.postflight_report) if result.postflight_report else ""
    assert "Memory:" in text, f"expected 'Memory:' in postflight, got: {text!r}"
    # Plural-form is fine; accept either for robustness
    assert "1 correction applied" in text or "1 corrections applied" in text


def test_postflight_renders_stale_ambiguous():
    """Direct render check: a fake CorrectionStats with stale_ambiguous>0
    surfaces the 'stale-ambiguous' label."""
    report = PostflightReport()
    report.memory_stats = CorrectionStats(
        applied=2, stale=1, stale_ambiguous=1, stale_unanchorable=0,
    )
    text = str(report)
    assert "Memory:" in text
    assert "stale-ambiguous" in text


def test_postflight_omits_memory_when_zero_counts(tmp_path):
    """Memory enabled, no corrections in store -> no 'Memory:' line."""
    df = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )
    db_path = str(tmp_path / "mem.db")
    # Note: no _seed_reject call. Store is empty.
    config = _build_config(db_path)
    result = dedupe_df(df, config=config)

    # memory_stats may be present (memory enabled) but with zero counts.
    text = str(result.postflight_report) if result.postflight_report else ""
    assert "Memory:" not in text


def test_postflight_omits_memory_when_stats_none():
    """Direct render check: report without memory_stats has no 'Memory:'."""
    report = PostflightReport()
    # No memory_stats attached
    text = str(report)
    assert "Memory:" not in text
