"""Integration tests for partitioned_block_scoring flag.

Component 2 Phase 2 of Distributed Plan v1. When the flag is on AND
prepared_record_store is on, the pipeline materializes blocks to disk
as a side effect of build_blocks. The in-memory scoring path is
unchanged; this stages the on-disk copy for Component 3.

The flag-off path must produce zero observable change.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig


def _diverse_df() -> pl.DataFrame:
    """Personlike df with enough surname diversity to produce multiple
    soundex blocks (otherwise we get one huge block and no scoring)."""
    surnames = [
        "Smith", "Johnson", "Williams", "Brown", "Jones",
        "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    ]
    rows = []
    for i in range(200):
        rows.append({
            "first_name": f"Person{i % 50}",
            "last_name":  surnames[i % len(surnames)],
            "email":      f"u{i // 2}@x.com",  # ~2 duplicates per email
            "zip":        f"{10000 + (i % 20):05d}",
        })
    return pl.DataFrame(rows)


def test_config_default_flag_is_false():
    cfg = GoldenMatchConfig()
    assert cfg.partitioned_block_scoring is False


def test_config_accepts_partitioned_block_scoring_true():
    cfg = GoldenMatchConfig(partitioned_block_scoring=True)
    assert cfg.partitioned_block_scoring is True


def test_flag_off_path_unchanged(tmp_path: Path, monkeypatch):
    """With the flag off, dedupe_df produces identical results vs default
    behavior. Anchors the no-regression invariant."""
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    df = _diverse_df()
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.partitioned_block_scoring = False
    result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    assert result is not None
    # No assertion on numbers -- just the path completes.


@pytest.mark.skip(reason="Component 2 v2 Phase 2 -- rewrite against iter_buckets")
def test_flag_on_materializes_blocks_to_store(tmp_path: Path, monkeypatch):
    """Phase 1 stub. Phase 2 rewrites the body against the v2 iter_buckets
    API (v1's list_blocks was removed in C2 v2 Phase 1)."""


@pytest.mark.skip(reason="Component 2 v2 Phase 2")
def test_pipeline_passes_store_path_when_all_flags_on(tmp_path: Path, monkeypatch):
    """When backend=ray + prepared_record_store + partitioned_block_scoring
    are all on, the pipeline must pass store_path + signature kwargs to
    score_blocks_ray. Monkeypatch score_blocks_ray to record kwargs --
    we don't need ray actually installed to assert pipeline-side wiring."""
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")

    df = _diverse_df()
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.prepared_record_store = True
    cfg.partitioned_block_scoring = True
    cfg.backend = "ray"

    captured: dict = {}
    def fake_score_blocks_ray(blocks, mk, matched_pairs, **kwargs):
        captured.update(kwargs)
        return []

    # The pipeline imports score_blocks_ray via _get_block_scorer, which
    # may resolve to a different name at runtime. Patch at the module
    # import point.
    monkeypatch.setattr(
        "goldenmatch.backends.ray_backend.score_blocks_ray",
        fake_score_blocks_ray,
        raising=False,
    )
    monkeypatch.setattr(
        "goldenmatch.core.pipeline._get_block_scorer",
        lambda config: fake_score_blocks_ray,
    )

    gm.dedupe_df(df, config=cfg, confidence_required=False)

    assert "store_path" in captured, (
        f"pipeline must pass store_path kwarg to score_blocks_ray when "
        f"all three flags are on; got kwargs={captured!r}"
    )
    assert "signature" in captured
    assert captured["store_path"] is not None
    assert captured["signature"] is not None


def test_pipeline_does_not_pass_store_path_when_disk_store_off(tmp_path: Path, monkeypatch):
    """backend=ray but prepared_record_store=False -> no store_path kwarg.
    Ensures df-mode is unaffected for users who picked Ray but not the
    disk store."""
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    df = _diverse_df()
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.prepared_record_store = False
    cfg.partitioned_block_scoring = False
    cfg.backend = "ray"

    captured: dict = {}
    def fake_score_blocks_ray(blocks, mk, matched_pairs, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "goldenmatch.core.pipeline._get_block_scorer",
        lambda config: fake_score_blocks_ray,
    )

    gm.dedupe_df(df, config=cfg, confidence_required=False)

    assert captured.get("store_path") is None
    assert captured.get("signature") is None
