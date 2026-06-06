"""Tests for the planning-effort tier + Phase 1 measure + Phase 3 in-house embedding.

Spec: docs/superpowers/specs/2026-06-06-autoconfig-search-strategy-after-engine-speedup-design.md
"""
from __future__ import annotations

import polars as pl
import pytest

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_controller import (
    ControllerBudget,
    resolve_planning_effort,
)


# --------------------------------------------------------------------------- #
# Phase 0 — ControllerBudget.for_dataset(n_rows, effort)
# --------------------------------------------------------------------------- #
def test_normal_is_byte_for_byte_backcompat():
    """The default 'normal' tier must equal the historical single-arg budget."""
    for n in (1_000, 10_000, 200_000, 2_000_000):
        assert ControllerBudget.for_dataset(n) == ControllerBudget.for_dataset(n, "normal")


def test_unknown_effort_falls_back_to_normal():
    assert ControllerBudget.for_dataset(10_000, "bogus") == ControllerBudget.for_dataset(10_000)


def test_fast_collapses_to_single_pass():
    b = ControllerBudget.for_dataset(10_000, "fast")
    assert b.max_iterations == 1
    assert b.max_seconds <= 15.0


def test_thinking_widens_sample_iters_and_budget():
    base = ControllerBudget.for_dataset(10_000)  # sample 2000, iters 3, 30s
    b = ControllerBudget.for_dataset(10_000, "thinking")
    assert b.sample_size_default == base.sample_size_default * 2
    assert b.max_iterations == base.max_iterations + 2
    assert b.max_seconds == base.max_seconds * 3.0


def test_einstein_widens_more_than_thinking():
    base = ControllerBudget.for_dataset(10_000)
    b = ControllerBudget.for_dataset(10_000, "einstein")
    assert b.sample_size_default == base.sample_size_default * 4
    assert b.max_iterations == base.max_iterations + 4
    assert b.max_seconds == base.max_seconds * 6.0


def test_effort_sample_never_exceeds_row_count():
    # einstein would want 2000*4=8000 but only 3000 rows exist.
    b = ControllerBudget.for_dataset(3_000, "einstein")
    assert b.sample_size_default == 3_000


# --------------------------------------------------------------------------- #
# Phase 0 — resolve_planning_effort + config field
# --------------------------------------------------------------------------- #
def test_resolve_explicit_wins():
    assert resolve_planning_effort("thinking") == "thinking"
    assert resolve_planning_effort("EINSTEIN") == "einstein"


def test_resolve_unknown_is_normal():
    assert resolve_planning_effort("nonsense") == "normal"


def test_resolve_env_fallback(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_PLANNING_EFFORT", "fast")
    assert resolve_planning_effort(None) == "fast"
    monkeypatch.delenv("GOLDENMATCH_PLANNING_EFFORT", raising=False)
    assert resolve_planning_effort(None) == "normal"


def test_config_default_is_normal():
    assert GoldenMatchConfig().planning_effort == "normal"
    assert GoldenMatchConfig(planning_effort="einstein").planning_effort == "einstein"


def test_config_rejects_bad_effort():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GoldenMatchConfig(planning_effort="turbo")


# --------------------------------------------------------------------------- #
# Phase 1 — measure_blocking_profile
# --------------------------------------------------------------------------- #
def _exact_cfg_with_blocking() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="exact", fields=[MatchkeyField(field="a")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["a"])]),
    )


def test_measure_blocking_profile_counts_real_pairs():
    from goldenmatch.core.blocker import measure_blocking_profile

    df = pl.DataFrame({"a": ["x", "x", "y", "z"]})
    prof = measure_blocking_profile(df, _exact_cfg_with_blocking())
    assert prof is not None
    # one block of size 2 (the two "x") -> exactly one candidate pair.
    assert prof.estimated_pair_count == 1
    assert prof.n_blocks >= 1


def test_measure_blocking_profile_none_without_blocking():
    from goldenmatch.core.blocker import measure_blocking_profile

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="exact", fields=[MatchkeyField(field="a")])],
        blocking=None,
    )
    assert measure_blocking_profile(pl.DataFrame({"a": ["x"]}), cfg) is None


# --------------------------------------------------------------------------- #
# Phase 3 — provider-aware in-house embedding exemption
# --------------------------------------------------------------------------- #
def _embedding_cfg(model: str | None) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="desc", scorer="embedding", weight=1.0, model=model)],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["desc"])]),
    )


def test_inhouse_embedding_not_demoted():
    from goldenmatch.core.autoconfig_verify import PreflightReport, _check_remote_assets

    cfg = _embedding_cfg("inhouse:/tmp/model")
    report = PreflightReport()
    _check_remote_assets(cfg, report, allow_remote_assets=False)
    assert cfg.get_matchkeys()[0].fields[0].scorer == "embedding"
    assert report.config_was_modified is False


def test_cloud_embedding_still_demoted():
    from goldenmatch.core.autoconfig_verify import PreflightReport, _check_remote_assets

    cfg = _embedding_cfg("all-MiniLM-L6-v2")
    report = PreflightReport()
    _check_remote_assets(cfg, report, allow_remote_assets=False)
    assert cfg.get_matchkeys()[0].fields[0].scorer == "ensemble"
    assert report.config_was_modified is True


def test_embedding_provider_env_exempts(monkeypatch):
    from goldenmatch.core.autoconfig_verify import PreflightReport, _check_remote_assets

    monkeypatch.setenv("GOLDENMATCH_EMBEDDING_PROVIDER", "inhouse")
    monkeypatch.setenv("GOLDENMATCH_INHOUSE_MODEL", "/tmp/model")
    cfg = _embedding_cfg(None)  # no per-field model, but global provider is in-house
    report = PreflightReport()
    _check_remote_assets(cfg, report, allow_remote_assets=False)
    assert cfg.get_matchkeys()[0].fields[0].scorer == "embedding"


def test_inhouse_availability_probe(monkeypatch):
    from goldenmatch.core.embedder import inhouse_embedding_available

    monkeypatch.delenv("GOLDENMATCH_INHOUSE_MODEL", raising=False)
    assert inhouse_embedding_available() is False


# --------------------------------------------------------------------------- #
# End-to-end plumbing smoke
# --------------------------------------------------------------------------- #
def test_dedupe_df_accepts_planning_effort():
    import goldenmatch as gm

    df = pl.DataFrame(
        {
            "name": ["Alice Smith", "Alice Smith", "Bob Jones", "Carol White"],
            "email": ["a@x.com", "a@x.com", "b@y.com", "c@z.com"],
        }
    )
    # fast tier should run the zero-config path without error.
    res = gm.dedupe_df(df, planning_effort="fast")
    assert res is not None
