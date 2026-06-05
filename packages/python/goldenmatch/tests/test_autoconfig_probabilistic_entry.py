"""Tests for the probabilistic auto-config entry point (auto_configure_probabilistic_df).

Phase 0 of the agentic-config-optimizer work: makes the Fellegi-Sunter
probabilistic matchkey type reachable from the auto-config surface (the
iterative auto_configure_df only emits exact/weighted matchkeys).
"""
from __future__ import annotations

import goldenmatch
import polars as pl
import pytest
from goldenmatch.core.autoconfig import auto_configure_probabilistic_df


def _person_df() -> pl.DataFrame:
    return pl.DataFrame({
        "first_name": ["John", "Jon", "Jane", "Bob", "Bobby"],
        "last_name": ["Smith", "Smith", "Doe", "Jones", "Jones"],
        "email": ["j@x.com", "j@x.com", "jane@y.com", "b@z.com", "b@z.com"],
    })


def test_builds_a_probabilistic_matchkey():
    cfg = auto_configure_probabilistic_df(_person_df())
    mks = cfg.get_matchkeys()
    assert mks, "expected at least one matchkey"
    assert any(m.type == "probabilistic" for m in mks)
    assert cfg.blocking is not None  # probabilistic keys need blocking


def test_config_runs_through_dedupe():
    df = _person_df()
    cfg = auto_configure_probabilistic_df(df)
    result = goldenmatch.dedupe_df(df, config=cfg)
    # John/Jon+Smith+same email, Jane, Bob/Bobby+Jones+same email -> 3 clusters.
    assert len(result.clusters) == 3
    assert result.unique.height == 1


def test_accepts_lazyframe():
    cfg = auto_configure_probabilistic_df(_person_df().lazy())
    assert any(m.type == "probabilistic" for m in cfg.get_matchkeys())


def test_raises_when_no_matchable_columns():
    # All numeric / perfectly-unique columns -> probabilistic skips numeric and
    # excludes card==1.0 surrogate keys (#721) -> no matchkey.
    df = pl.DataFrame({"row_id": [1, 2, 3], "count": [10, 20, 30]})
    with pytest.raises(ValueError, match="probabilistic"):
        auto_configure_probabilistic_df(df)


def test_exported_from_top_level():
    assert hasattr(goldenmatch, "auto_configure_probabilistic_df")
    assert "auto_configure_probabilistic_df" in goldenmatch.__all__
