"""Regression tests for #739: _run_phase5_pipeline must honor caller kwargs.

The Phase-5 distributed pipeline forwarded only ``output_path`` and
``confidence_required``; an explicit ``config`` and ``allow_red_config`` flowed
into ``**kwargs`` and were silently dropped. On a large input that meant a
hand-built, valid config was ignored, auto-config ran anyway, and a RED commit
raised ``ControllerNotConfidentError`` -- even though the caller had supplied
both a config AND ``allow_red_config=True`` (the documented escape hatch).

These tests mock the distributed stages so they exercise ONLY the config-branch
logic -- no real Ray run, no 346M-row repro (xdist/ray full runs OOM the dev
box; see packages CLAUDE.md). The boundary asserted is "what config does the
scorer receive, and was auto_configure_df called".
"""
from __future__ import annotations

from unittest.mock import MagicMock

# No ``importorskip("ray")``: every distributed stage is mocked, and the
# distributed modules import ray lazily (inside functions), so this exercises
# the config-branch logic with no Ray runtime -- it runs in the default lane.


def _patch_distributed_stages(monkeypatch):
    """Stub every distributed stage so _run_phase5_pipeline runs end to end
    without touching Ray. Returns the auto_configure_df mock for assertions."""
    import goldenmatch.core.autoconfig as autoconfig
    import goldenmatch.distributed.clustering as clustering
    import goldenmatch.distributed.golden as golden
    import goldenmatch.distributed.pipeline as pipeline
    import goldenmatch.distributed.scoring as scoring

    auto_cfg = MagicMock(name="auto_configured")
    auto_configure_df = MagicMock(name="auto_configure_df", return_value=auto_cfg)
    monkeypatch.setattr(autoconfig, "auto_configure_df", auto_configure_df)

    monkeypatch.setattr(scoring, "score_blocks_distributed", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(clustering, "local_cc_assignments", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(golden, "build_golden_records_distributed", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(pipeline, "_join_assignments_distributed", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(pipeline, "_row_columns", MagicMock(return_value=["first_name"]))

    return auto_configure_df, scoring


def test_explicit_config_bypasses_auto_configure(monkeypatch):
    """A caller-supplied config is used verbatim; auto_configure_df is NOT called
    and the scorer receives the explicit config."""
    from goldenmatch.distributed.pipeline import _run_phase5_pipeline

    auto_configure_df, scoring = _patch_distributed_stages(monkeypatch)
    explicit = MagicMock(name="explicit_config")

    result = _run_phase5_pipeline(MagicMock(name="ds"), output_path=None, config=explicit)

    auto_configure_df.assert_not_called()
    # The scorer must score against the explicit config, not an auto-config.
    assert scoring.score_blocks_distributed.call_args.args[1] is explicit
    assert result.config is explicit


def test_allow_red_config_forwarded_to_auto_configure(monkeypatch):
    """With no explicit config, allow_red_config reaches auto_configure_df so the
    documented escape hatch works on the distributed path."""
    from goldenmatch.distributed.pipeline import _run_phase5_pipeline

    auto_configure_df, _ = _patch_distributed_stages(monkeypatch)

    _run_phase5_pipeline(MagicMock(name="ds"), output_path=None, allow_red_config=True)

    auto_configure_df.assert_called_once()
    assert auto_configure_df.call_args.kwargs.get("allow_red_config") is True


def test_default_path_passes_allow_red_config_false(monkeypatch):
    """Default path (no config, no allow_red_config) still auto-configures with
    allow_red_config=False -- behavior unchanged for existing callers."""
    from goldenmatch.distributed.pipeline import _run_phase5_pipeline

    auto_configure_df, _ = _patch_distributed_stages(monkeypatch)

    _run_phase5_pipeline(MagicMock(name="ds"), output_path=None)

    auto_configure_df.assert_called_once()
    assert auto_configure_df.call_args.kwargs.get("allow_red_config") is False
