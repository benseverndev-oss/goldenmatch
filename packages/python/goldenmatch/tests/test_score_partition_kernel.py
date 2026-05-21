"""Tests for the narrow `_score_partition_with_config` scoring kernel.

Spec: docs/superpowers/specs/2026-05-21-distributed-score-partition-narrow-primitive-design.md
Issue: #396
Plan: docs/superpowers/plans/2026-05-21-distributed-score-partition-narrow-primitive.md
"""

from __future__ import annotations

from unittest.mock import patch

import polars as pl
import pytest


def _person_fixture() -> pl.DataFrame:
    return pl.DataFrame({
        "first_name": ["Alice"] * 5 + ["Bob"] * 5 + ["Alyce"] * 5 + ["Robert"] * 5,
        "last_name": ["Smith"] * 5 + ["Jones"] * 5 + ["Smith"] * 5 + ["Jones"] * 5,
    })


def _kernel_committed_config(df: pl.DataFrame):
    """Build a config the kernel can use: auto-config first (driver-side),
    then hand the committed config to the kernel just like the distributed
    path does after the controller runs.
    """
    from goldenmatch.core.autoconfig import auto_configure_df
    return auto_configure_df(df, confidence_required=False, _skip_finalize=True)


def test_kernel_returns_pairs_for_simple_fixture():
    """Smoke test: kernel produces scored pairs against a person-shaped
    fixture using a driver-committed config."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    df = _person_fixture()
    cfg = _kernel_committed_config(df)
    cfg.backend = "bucket"  # same posture as score_blocks_distributed

    pairs = _score_partition_with_config(df, cfg)

    assert isinstance(pairs, list)
    if pairs:
        a, b, s = pairs[0]
        assert isinstance(a, int)
        assert isinstance(b, int)
        assert isinstance(s, float)


def test_kernel_skips_clustering():
    """The kernel must NOT call build_clusters; clustering is the driver's
    job after pair-merge across partitions."""
    from goldenmatch.core import pipeline

    df = _person_fixture()
    cfg = _kernel_committed_config(df)
    cfg.backend = "bucket"

    with patch.object(pipeline, "build_clusters") as mock_clusters:
        pipeline._score_partition_with_config(df, cfg)

    assert not mock_clusters.called, (
        "kernel must skip build_clusters -- clustering happens on the "
        "driver after partition pairs are merged. See #396."
    )


def test_kernel_skips_autoconfig():
    """The kernel must NOT call auto_configure_df; the driver already ran
    auto-config on a sample (Phase 2) before dispatching."""
    df = _person_fixture()
    cfg = _kernel_committed_config(df)
    cfg.backend = "bucket"

    # Patch the module attribute the kernel resolves at call time via the
    # `from goldenmatch.core.autoconfig import auto_configure_df` inside
    # `_run_dedupe_pipeline`. The kernel never imports it (by design); if
    # this test fails, someone added a re-auto-config inside the kernel.
    with patch("goldenmatch.core.autoconfig.auto_configure_df") as mock_ac:
        from goldenmatch.core.pipeline import _score_partition_with_config
        _score_partition_with_config(df, cfg)

    assert not mock_ac.called, (
        "kernel must skip auto_configure_df -- driver runs the controller "
        "once on a sample and ships the committed config. See #396."
    )


def test_kernel_skips_golden_build():
    """The kernel must NOT build golden records per partition; that's a
    driver-side post-cluster step."""
    from goldenmatch.core import pipeline

    df = _person_fixture()
    cfg = _kernel_committed_config(df)
    cfg.backend = "bucket"

    with patch("goldenmatch.core.golden.build_golden_records_batch") as mock_g:
        pipeline._score_partition_with_config(df, cfg)

    assert not mock_g.called, (
        "kernel must skip build_golden_records_batch -- golden is a "
        "driver-side post-cluster step. See #396."
    )


def test_kernel_empty_input_returns_empty():
    """Empty / tiny partitions return [] without crashing."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    df = _person_fixture()
    cfg = _kernel_committed_config(df)
    cfg.backend = "bucket"

    # df.height < 2 short-circuit
    tiny = df.head(1)
    assert _score_partition_with_config(tiny, cfg) == []

    # df.height = 0 short-circuit
    empty = df.head(0)
    assert _score_partition_with_config(empty, cfg) == []


def test_kernel_no_matchkeys_returns_empty():
    """Config with empty matchkey list returns [] without crashing."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.pipeline import _score_partition_with_config

    df = _person_fixture()
    cfg = GoldenMatchConfig()  # no matchkeys, no blocking
    assert _score_partition_with_config(df, cfg) == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
