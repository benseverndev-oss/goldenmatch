"""Tests for scale-aware backend selection in auto-config.

Phase 4 of the controller v3 planner promoted ``_scale_aware_backend``'s
PR-#239 env-var behavior to a first-class planner rule (rule_chunked +
rule_user_override etc). The original function is now a frozen shim that
still returns PR-#239's single-threshold answer for external callers
during the deprecation window; it does NOT delegate to the planner.

Tests split into:
- ``TestScaleAwareBackendShim`` -- unit tests for the frozen shim. Same
  spec as PR #239; here to prove the shim still behaves predictably.
- ``TestPlannerBackendSelection`` -- planner-level tests using
  ``apply_planner_rules`` directly. These are the authoritative
  source of truth for "which backend gets picked at scale X".
- ``TestAutoConfigureDfWiring`` -- end-to-end tests via auto_configure_df.
  The env-var contract is gone; today these only test that the planner
  doesn't accidentally set backend on small inputs.
"""
from __future__ import annotations

import warnings

import polars as pl
import pytest
from goldenmatch.core.autoconfig import (
    _AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD,
    _scale_aware_backend,
)
from goldenmatch.core.autoconfig_planner import apply_planner_rules
from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ProfileMeta,
)
from goldenmatch.core.runtime_profile import RuntimeProfile


def _profile(n_rows: int = 1000, total_comparisons: int = 100) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows, n_cols=3),
        blocking=BlockingProfile(
            keys_used=[["name"]],
            n_blocks=10,
            total_comparisons=total_comparisons,
            reduction_ratio=0.9,
            block_sizes_p50=10,
            block_sizes_p95=15,
            block_sizes_p99=20,
            block_sizes_max=25,
            singleton_block_count=0,
            oversized_block_count=0,
        ),
        meta=ProfileMeta(
            iteration=0,
            is_sample=False,
            sample_size=n_rows,
            n_rows_full=n_rows,
            wall_clock_ms=0,
            seed=0,
        ),
    )


def _runtime(ram_gb: float = 32.0, cpus: int = 8) -> RuntimeProfile:
    return RuntimeProfile(available_ram_gb=ram_gb, cpu_count=cpus, disk_free_gb=100.0)


@pytest.fixture(autouse=True)
def _silence_deprecation_warnings():
    """Shim emits DeprecationWarning; tests assert behavior, not the warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield


class TestScaleAwareBackendShim:
    """Unit tests for the frozen PR-#239 shim. Same behavior as before
    Phase 4; the shim is kept exportable for one release as deprecation
    cushion. The planner is now the source of truth for production paths."""

    def test_small_data_returns_none(self):
        assert _scale_aware_backend(0) is None
        assert _scale_aware_backend(100) is None
        assert _scale_aware_backend(_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD - 1) is None

    def test_large_data_returns_duckdb(self):
        assert _scale_aware_backend(_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD) == "duckdb"
        assert _scale_aware_backend(5_000_000) == "duckdb"
        assert _scale_aware_backend(100_000_000) == "duckdb"

    def test_threshold_env_override(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "10000")
        assert _scale_aware_backend(9_999) is None
        assert _scale_aware_backend(10_000) == "duckdb"

    def test_threshold_env_malformed_falls_back(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "not-a-number")
        assert _scale_aware_backend(100) is None
        assert _scale_aware_backend(_AUTOCONFIG_BACKEND_DEFAULT_THRESHOLD) == "duckdb"

    def test_backend_env_disables(self, monkeypatch):
        for token in ("0", "false", "disabled", ""):
            monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", token)
            assert _scale_aware_backend(50_000_000) is None

    def test_backend_env_none_token(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "none")
        assert _scale_aware_backend(50_000_000) is None

    def test_backend_env_forces_explicit(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "duckdb")
        assert _scale_aware_backend(10) == "duckdb"

    def test_backend_env_passes_through_unknown(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "ray")
        assert _scale_aware_backend(10) == "ray"

    def test_shim_emits_deprecation_warning(self):
        """The shim is deprecated; new code should use apply_planner_rules."""
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            _scale_aware_backend(100)
        assert any(
            issubclass(w.category, DeprecationWarning) for w in recorded
        ), "shim should emit DeprecationWarning"


class TestPlannerBackendSelection:
    """Authoritative tests for which backend the planner picks at scale X."""

    def test_planner_picks_polars_direct_below_100k(self):
        plan = apply_planner_rules(
            profile=_profile(n_rows=100, total_comparisons=10),
            runtime=_runtime(),
            n_rows_full=100,
            rules=DEFAULT_RULES,
            context={"user_backend": None},
        )
        assert plan.backend == "polars-direct"
        assert plan.rule_name == "plan_selected_simple"

    def test_planner_picks_fast_box_at_500k_with_64gb_and_sparse_pairs(self):
        plan = apply_planner_rules(
            profile=_profile(n_rows=500_000, total_comparisons=10_000_000),
            runtime=_runtime(ram_gb=64.0, cpus=16),
            n_rows_full=500_000,
            rules=DEFAULT_RULES,
            context={"user_backend": None},
        )
        assert plan.backend == "polars-direct"
        assert plan.rule_name == "plan_selected_fast_box"

    def test_planner_picks_chunked_at_2m_with_dense_pairs(self):
        plan = apply_planner_rules(
            profile=_profile(n_rows=2_000_000, total_comparisons=200_000_000),
            runtime=_runtime(ram_gb=32.0, cpus=16),
            n_rows_full=2_000_000,
            rules=DEFAULT_RULES,
            context={"user_backend": None},
        )
        assert plan.backend == "chunked"
        assert plan.rule_name == "plan_selected_chunked"
        assert plan.chunk_size is not None and plan.chunk_size > 0

    def test_planner_user_override_beats_scale_heuristics(self):
        """User explicitly picking 'ray' beats every other rule, even at
        small N where rule_simple_plan would otherwise fire."""
        plan = apply_planner_rules(
            profile=_profile(n_rows=100, total_comparisons=10),
            runtime=_runtime(),
            n_rows_full=100,
            rules=DEFAULT_RULES,
            context={"user_backend": "ray"},
        )
        assert plan.backend == "ray"
        assert plan.rule_name == "plan_user_override"


class TestAutoConfigureDfWiring:
    """auto_configure_df() now drives backend selection through the planner.
    The env-var contract (GOLDENMATCH_AUTOCONFIG_BACKEND / _THRESHOLD) is
    deprecated and only honored by the frozen shim, NOT by the planner --
    so it no longer affects what auto_configure_df returns."""

    def _personlike_df(self, n: int) -> pl.DataFrame:
        names = ["Alice", "Bob", "Charlie", "Dana", "Eve", "Frank"]
        zips = ["10001", "10002", "10003", "10004", "10005"]
        return pl.DataFrame({
            "id": list(range(n)),
            "name": [names[i % len(names)] for i in range(n)],
            "email": [f"user{i}@example.com" for i in range(n)],
            "zip": [zips[i % len(zips)] for i in range(n)],
        })

    def test_small_df_leaves_backend_unset(self, monkeypatch):
        """Below the simple-plan ceiling: planner picks polars-direct,
        plan.apply_to leaves config.backend at its default (None)."""
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND", raising=False)
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", raising=False)
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import auto_configure_df

        df = self._personlike_df(100)
        cfg = auto_configure_df(df)
        assert cfg.backend is None

    def test_threshold_env_no_longer_promotes_to_duckdb(self, monkeypatch):
        """Phase 4 disconnected the env var from auto_configure_df.
        Setting threshold=50 on a 60-row input used to promote to duckdb
        (PR #239 behavior); after Phase 4 the planner ignores the env
        and picks rule_simple_plan -> polars-direct -> backend stays None."""
        monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_BACKEND", raising=False)
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "50")
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import auto_configure_df

        df = self._personlike_df(60)
        cfg = auto_configure_df(df)
        assert cfg.backend is None, (
            f"Phase 4: planner ignores GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD; "
            f"60-row input should land on polars-direct, got backend={cfg.backend!r}"
        )

    def test_disable_env_no_longer_relevant(self, monkeypatch):
        """The disable env var was a counter to the shim's auto-promotion;
        with the shim disconnected, neither setting matters for the
        planner. 100-row inputs land on polars-direct regardless."""
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND", "0")
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "10")
        monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
        from goldenmatch.core.autoconfig import auto_configure_df

        df = self._personlike_df(100)
        cfg = auto_configure_df(df)
        assert cfg.backend is None
