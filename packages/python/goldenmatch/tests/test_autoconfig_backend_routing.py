"""Tests for scale-aware backend selection in auto_configure_df.

The routing exists so that zero-config users at large N automatically
get an out-of-core backend (today: duckdb pair store) instead of OOMing
the default polars-direct path. See `_scale_aware_backend` in
`goldenmatch/core/autoconfig.py`.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.autoconfig import (
    _AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD,
    _scale_aware_backend,
)


class TestScaleAwareBackendHelper:
    """Unit tests for the row-count → backend mapping."""

    def test_small_data_returns_none(self):
        """Below threshold: keep polars-direct (fastest at small N)."""
        assert _scale_aware_backend(0) is None
        assert _scale_aware_backend(100) is None
        assert _scale_aware_backend(_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD - 1) is None

    def test_large_data_returns_duckdb(self):
        """At/above threshold: spill via duckdb pair store."""
        assert _scale_aware_backend(_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD) == "duckdb"
        assert _scale_aware_backend(5_000_000) == "duckdb"
        assert _scale_aware_backend(100_000_000) == "duckdb"

    def test_threshold_env_override(self, monkeypatch):
        """Threshold can be lowered/raised via env var."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "10000")
        assert _scale_aware_backend(9_999) is None
        assert _scale_aware_backend(10_000) == "duckdb"

    def test_threshold_env_malformed_falls_back(self, monkeypatch, caplog):
        """Garbage threshold env var should warn and use the default."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "not-a-number")
        assert _scale_aware_backend(100) is None
        assert _scale_aware_backend(_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD) == "duckdb"

    def test_backend_env_disables(self, monkeypatch):
        """Explicit opt-out: no auto-selection regardless of N."""
        for token in ("0", "false", "disabled", ""):
            monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", token)
            assert _scale_aware_backend(50_000_000) is None

    def test_backend_env_none_token(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "none")
        assert _scale_aware_backend(50_000_000) is None

    def test_backend_env_forces_explicit(self, monkeypatch):
        """Explicit force: small N also gets the named backend."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "duckdb")
        assert _scale_aware_backend(10) == "duckdb"

    def test_backend_env_passes_through_unknown(self, monkeypatch):
        """Unknown backend names pass through; pipeline layer validates."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "ray")
        assert _scale_aware_backend(10) == "ray"


class TestAutoConfigureDfWiring:
    """auto_configure_df() must apply the selected backend to the returned config."""

    def _personlike_df(self, n: int) -> pl.DataFrame:
        """Synthetic person-shape dataframe sized to N rows.

        Cheap to construct at small N; matches what the controller's
        column-classifier needs to pick weighted/exact matchkeys.
        """
        names = ["Alice", "Bob", "Charlie", "Dana", "Eve", "Frank"]
        zips = ["10001", "10002", "10003", "10004", "10005"]
        return pl.DataFrame({
            "id": list(range(n)),
            "name": [names[i % len(names)] for i in range(n)],
            "email": [f"user{i}@example.com" for i in range(n)],
            "zip": [zips[i % len(zips)] for i in range(n)],
        })

    def test_small_df_leaves_backend_unset(self, monkeypatch):
        """Below threshold: backend remains None (no behavior change)."""
        # Ensure no env override leaks from CI/dev shell.
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND", raising=False)
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", raising=False)
        # Disable cross-run memory so cached configs don't override the test.
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import auto_configure_df

        df = self._personlike_df(100)
        cfg = auto_configure_df(df)
        assert cfg.backend is None

    def test_large_df_promotes_to_duckdb(self, monkeypatch):
        """At threshold: backend auto-set to duckdb without touching matchkeys."""
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND", raising=False)
        # Use a tiny threshold so we don't have to build a 1M-row fixture.
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "50")
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import auto_configure_df

        df = self._personlike_df(60)
        cfg = auto_configure_df(df)
        assert cfg.backend == "duckdb", (
            f"Expected duckdb at 60 rows with threshold=50, got {cfg.backend!r}"
        )

    def test_disable_env_overrides_threshold(self, monkeypatch):
        """Explicit disable wins over scale heuristic."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "0")
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "10")
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import auto_configure_df

        df = self._personlike_df(100)
        cfg = auto_configure_df(df)
        assert cfg.backend is None
