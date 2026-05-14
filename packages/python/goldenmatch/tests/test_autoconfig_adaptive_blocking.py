"""Tests for the two adaptive-blocking pieces:

1. Eager promotion in ``autoconfig._maybe_promote_blocking_to_adaptive``
   triggered by row count.
2. Reactive controller rule ``rule_blocking_adaptive_on_p99_outlier``
   triggered by measured block-size distribution.
"""
from __future__ import annotations

from typing import Literal

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig import _maybe_promote_blocking_to_adaptive
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import (
    DEFAULT_RULES,
    rule_blocking_adaptive_on_p99_outlier,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)


def _profile(p50: int, p99: int) -> ComplexityProfile:
    """Build a minimal ComplexityProfile with the block-size signal set."""
    return ComplexityProfile(
        data=DataProfile(n_rows=100_000, n_cols=5, column_types={}),
        blocking=BlockingProfile(
            n_blocks=1000,
            block_sizes_p50=p50,
            block_sizes_p95=p99,
            block_sizes_p99=p99,
            block_sizes_max=p99,
        ),
        scoring=ScoringProfile(),
        cluster=ClusterProfile(),
    )


_StrategyLiteral = Literal[
    "static", "adaptive", "sorted_neighborhood", "multi_pass",
    "ann", "canopy", "ann_pairs", "learned",
]


def _cfg(strategy: _StrategyLiteral = "static") -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(
                        field="last_name", scorer="jaro_winkler", weight=1.0,
                    ),
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy=strategy,
            keys=[BlockingKeyConfig(fields=["zip"])],
        ),
    )


def _blocking_of(cfg: GoldenMatchConfig) -> BlockingConfig:
    """Tests construct cfg with blocking, so narrow Optional for pyright."""
    assert cfg.blocking is not None
    return cfg.blocking


# ── Eager promotion in autoconfig ────────────────────────────────────────


class TestMaybePromoteEager:
    def test_below_threshold_no_change(self):
        blocking = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
        )
        result = _maybe_promote_blocking_to_adaptive(blocking, n_rows=500_000)
        assert result is blocking
        assert result.strategy == "static"

    def test_above_threshold_promotes(self):
        blocking = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
        )
        result = _maybe_promote_blocking_to_adaptive(blocking, n_rows=1_500_000)
        assert result is not None
        assert result.strategy == "adaptive"
        # Keys preserved
        assert result.keys == blocking.keys

    def test_multi_pass_untouched(self):
        """multi_pass has its own escape hatches; don't promote."""
        blocking = BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["zip"])],
            passes=[BlockingKeyConfig(fields=["zip"])],
        )
        result = _maybe_promote_blocking_to_adaptive(blocking, n_rows=5_000_000)
        assert result is blocking
        assert result.strategy == "multi_pass"

    def test_canopy_untouched(self):
        from goldenmatch.config.schemas import CanopyConfig

        blocking = BlockingConfig(
            strategy="canopy",
            keys=[BlockingKeyConfig(fields=["description"])],
            canopy=CanopyConfig(fields=["description"]),
        )
        result = _maybe_promote_blocking_to_adaptive(blocking, n_rows=5_000_000)
        assert result.strategy == "canopy"

    def test_none_input_returns_none(self):
        """Pass-through for the no-blocking case (exact-only configs)."""
        assert _maybe_promote_blocking_to_adaptive(None, n_rows=10_000_000) is None

    def test_exact_threshold_boundary(self):
        """Threshold is inclusive (>=)."""
        blocking = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"])],
        )
        result = _maybe_promote_blocking_to_adaptive(
            blocking, n_rows=1_000_000,
        )
        assert result.strategy == "adaptive"


# ── Reactive controller rule ─────────────────────────────────────────────


class TestRuleAdaptiveOnP99Outlier:
    def test_fires_on_heavy_tail(self):
        """p99=1000, p50=10: 100x ratio, well over 10x trigger."""
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=10, p99=1000),
            _cfg(strategy="static"),
            RunHistory(),
        )
        assert result is not None
        new_cfg, decision = result
        assert _blocking_of(new_cfg).strategy == "adaptive"
        assert decision.rule_name == "blocking_adaptive_on_p99_outlier"

    def test_skips_on_uniform_distribution(self):
        """p99 within 10x of p50 — no oversize tail, no need to promote."""
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=50, p99=400),
            _cfg(strategy="static"),
            RunHistory(),
        )
        assert result is None

    def test_skips_small_blocks_below_absolute_gate(self):
        """Heavy tail ratio but small absolute size: scorer chews through
        sub-1000-row blocks fine in-memory; promoting strategy preempts
        the existing rule chain without measurable gain. Pins this gate
        so we don't regress DBLP-ACM and other small-block fixtures.
        """
        # ratio 50x but absolute p99 below the gate
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=2, p99=100),
            _cfg(strategy="static"),
            RunHistory(),
        )
        assert result is None

    def test_skips_when_already_adaptive(self):
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=10, p99=1000),
            _cfg(strategy="adaptive"),
            RunHistory(),
        )
        assert result is None

    def test_skips_multi_pass(self):
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=10, p99=1000),
            _cfg(strategy="multi_pass"),
            RunHistory(),
        )
        assert result is None

    def test_skips_zero_p50(self):
        """Empty profile — don't divide-by-zero, just bail."""
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=0, p99=1000),
            _cfg(strategy="static"),
            RunHistory(),
        )
        assert result is None

    def test_skips_no_keys(self):
        """Degenerate blocking — don't touch it."""
        cfg = _cfg(strategy="static")
        # Bypass the validator by direct mutation — the test models a
        # degenerate config the controller might briefly produce.
        _blocking_of(cfg).keys = []
        result = rule_blocking_adaptive_on_p99_outlier(
            _profile(p50=10, p99=1000), cfg, RunHistory(),
        )
        assert result is None

    def test_is_in_default_rules_before_too_coarse(self):
        """Adaptive promotion is strictly cheaper than key swap; should fire
        first when both would match."""
        from goldenmatch.core.autoconfig_rules import rule_blocking_too_coarse

        assert rule_blocking_adaptive_on_p99_outlier in DEFAULT_RULES
        assert rule_blocking_too_coarse in DEFAULT_RULES
        idx_adaptive = DEFAULT_RULES.index(rule_blocking_adaptive_on_p99_outlier)
        idx_too_coarse = DEFAULT_RULES.index(rule_blocking_too_coarse)
        assert idx_adaptive < idx_too_coarse
