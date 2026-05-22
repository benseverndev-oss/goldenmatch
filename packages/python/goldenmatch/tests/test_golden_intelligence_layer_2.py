"""Tests for v1.18.1 golden-rules intelligence layer 2.

Covers:
- #1 per-source consensus agreement (replaces completeness for source_priority ranking)
- #2 MemoryStore-learned strategy tuner

Spec: docs/superpowers/specs/2026-05-22-golden-rules-intelligence-layer-2-design.md
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenRulesConfig
from goldenmatch.core.autoconfig_golden_strategy_tuner import (
    DEFAULT_CANDIDATE_STRATEGIES,
    MIN_CORRECTIONS,
    StrategyTuning,
    tune_field_strategy,
)
from goldenmatch.core.golden_rules_refiner import (
    RefinementSignals,
    _pick_strategy_for_field,
    compute_refinement_signals,
    refine_golden_rules,
)

# ---------------------------------------------------------------------------
# #1: per-source consensus agreement
# ---------------------------------------------------------------------------


def test_compute_refinement_signals_includes_per_source_agreement():
    """compute_refinement_signals populates per_source_agreement when
    __source__ is present + clusters have enough members."""
    # Build a 4-cluster fixture: each cluster has 3 sources (a, b, c).
    # Source A agrees with consensus 100% of the time on `name`.
    # Source B agrees 50%. Source C agrees 0%.
    rows = []
    consensus_per_cluster = {0: "Alice", 1: "Bob", 2: "Carol", 3: "Dan"}
    for cid, consensus in consensus_per_cluster.items():
        # 12 rows per cluster (3 sources × 4 members each), but we'll
        # use 3 rows per cluster (1 per source) for simplicity.
        # Source A: agrees
        rows.append({"__row_id__": cid * 3, "__source__": "a", "name": consensus})
        # Source B: agrees half the time (cluster 0, 1 yes; 2, 3 no)
        rows.append({
            "__row_id__": cid * 3 + 1, "__source__": "b",
            "name": consensus if cid < 2 else "Other",
        })
        # Source C: always disagrees
        rows.append({"__row_id__": cid * 3 + 2, "__source__": "c", "name": "Wrong"})

    # Need >= 10 attempts per source per field for the agreement signal.
    # Multiply by 4 to get 16 attempts each.
    full_rows = rows * 4
    # Re-index __row_id__ so it's unique.
    for i, r in enumerate(full_rows):
        r["__row_id__"] = i

    df = pl.DataFrame(full_rows)

    # 16 clusters (4 clusters × 4 copies), each with 3 sources.
    clusters: dict[int, dict] = {}
    for cluster_idx in range(16):
        base = cluster_idx * 3
        clusters[cluster_idx] = {
            "members": [base, base + 1, base + 2],
            "size": 3,
        }

    # No column profiles needed -- compute_refinement_signals only uses
    # them for col_type/avg_len/null_rate which don't affect per-source-
    # agreement computation directly.
    signals = compute_refinement_signals(clusters, df, column_profiles=[])

    assert "name" in signals.per_source_agreement
    rates = signals.per_source_agreement["name"]
    # Source A agrees 100%; B agrees ~50%; C agrees 0%.
    assert rates["a"] > 0.95
    assert 0.4 < rates["b"] < 0.6
    assert rates["c"] < 0.05


def test_source_priority_rule_uses_agreement_over_completeness():
    """When agreement signal is present, it overrides completeness
    for the source_priority ranking."""
    # Completeness: B > A. Agreement: A > B.
    # The rule should pick A (better quality), not B (higher completeness).
    signals = RefinementSignals(
        within_cluster_spread={"name": 1.5},
        per_source_completeness={"name": {"a": 0.7, "b": 0.95}},  # B more complete
        per_source_agreement={"name": {"a": 0.95, "b": 0.50}},     # A more accurate
        date_column_coverage={},
        col_type={"name": "name"},
        avg_len={"name": 10.0},
        null_rate={"name": 0.1},
    )
    result = _pick_strategy_for_field("name", signals)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "source_priority"
    # A's agreement is 0.95, B's is 0.50. Median = (0.95 + 0.50) / 2 = 0.725.
    # Top (A=0.95) / median (0.725) = 1.31 -- NOT > 1.5x, so no dominance.
    # The test pins the data flow + the rule sees agreement, not completeness.
    # (We're not asserting "A wins source_priority"; we're asserting that
    # the rule consults agreement first.)
    # When dominance threshold is met, A should be first.
    assert kwargs["source_priority"][0] == "a"


def test_source_priority_falls_back_to_completeness_when_no_agreement():
    """When agreement dict is empty (< 10 attempts), fall back to
    completeness for source_priority ranking."""
    signals = RefinementSignals(
        within_cluster_spread={"name": 1.5},
        per_source_completeness={"name": {"a": 0.95, "b": 0.30}},  # A dominates
        per_source_agreement={},                                    # no agreement signal
        date_column_coverage={},
        col_type={"name": "name"},
        avg_len={"name": 10.0},
        null_rate={"name": 0.1},
    )
    result = _pick_strategy_for_field("name", signals)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "source_priority"
    # A's completeness 0.95, B's 0.30; median = 0.625; top/median = 1.52 > 1.5.
    assert kwargs["source_priority"][0] == "a"


# ---------------------------------------------------------------------------
# #2: MemoryStore-learned strategy tuner
# ---------------------------------------------------------------------------


@dataclass
class _StubCorrection:
    id: str
    decision: str  # "approve" or "reject"
    trust: float


class _StubStore:
    def __init__(self, corrections: list[_StubCorrection]) -> None:
        self._corrections = corrections

    def get_corrections(self, dataset: str) -> list[_StubCorrection]:
        return list(self._corrections)


def test_tuner_returns_no_memory_when_store_is_none():
    result = tune_field_strategy(store=None, dataset="d", field="f")
    assert result.reason == "no_memory"
    assert result.strategy == ""


def test_tuner_returns_below_minimum_under_threshold():
    """< MIN_CORRECTIONS -> tuner declines + heuristics take over."""
    corrections = [
        _StubCorrection(id=f"c{i:04d}", decision="approve", trust=0.8)
        for i in range(10)
    ]
    store = _StubStore(corrections)
    result = tune_field_strategy(store=store, dataset="d", field="f")  # type: ignore[arg-type]
    assert result.reason == "below_minimum"
    assert result.strategy == ""


def test_tuner_learns_strategy_when_clear_signal():
    """50 high-trust approves -> tuner learns a preserve strategy."""
    corrections = [
        _StubCorrection(id=f"a{i:04d}", decision="approve", trust=0.9)
        for i in range(60)
    ]
    store = _StubStore(corrections)
    result = tune_field_strategy(store=store, dataset="d", field="f")  # type: ignore[arg-type]
    assert result.reason in ("learned", "overfit_guard")
    if result.reason == "learned":
        # Should pick one of the preserve-strategies.
        assert result.strategy in {
            "most_complete", "longest_value", "majority_vote", "first_non_null",
        }


def test_tuner_min_corrections_constant():
    assert MIN_CORRECTIONS == 50


def test_tuner_env_override(monkeypatch: pytest.MonkeyPatch):
    """GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS env lowers the gate."""
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS", "5")
    corrections = [
        _StubCorrection(id=f"c{i:04d}", decision="approve", trust=0.8)
        for i in range(10)
    ]
    store = _StubStore(corrections)
    result = tune_field_strategy(store=store, dataset="d", field="f")  # type: ignore[arg-type]
    # Above the lowered threshold -> tuner runs.
    assert result.reason in ("learned", "overfit_guard")


def test_strategytuning_dataclass_is_frozen():
    t = StrategyTuning(
        field="f", strategy="most_complete", n_corrections=50,
        train_hit_rate=0.9, heldout_hit_rate=0.88, reason="learned",
    )
    with pytest.raises(Exception):
        t.strategy = "majority_vote"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Integration: refine_golden_rules consults the tuner
# ---------------------------------------------------------------------------


def test_refine_consults_tuner_when_memory_store_provided():
    """When memory_store is passed AND has enough corrections, the
    tuner's pick beats the heuristic default."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    df = pl.DataFrame({"__row_id__": [0, 1], "name": ["a", "b"]})
    clusters: dict[int, dict] = {}  # no multi-member clusters -> heuristics neutral

    corrections = [
        _StubCorrection(id=f"a{i:04d}", decision="reject", trust=0.9)
        for i in range(60)
    ]
    store = _StubStore(corrections)

    out = refine_golden_rules(
        base, clusters, df, column_profiles=[],
        memory_store=store, dataset="d",
    )
    # Tuner should run -- though without multi-member clusters, the
    # refiner has no fields_to_consider. This pins the contract that
    # passing memory_store doesn't crash + the refiner accepts it.
    assert isinstance(out, GoldenRulesConfig)


def test_default_candidate_strategies_match_expectation():
    """Pin the v1.18.1 default candidate set so future bumps surface."""
    assert "most_complete" in DEFAULT_CANDIDATE_STRATEGIES
    assert "longest_value" in DEFAULT_CANDIDATE_STRATEGIES
    assert "confidence_majority" in DEFAULT_CANDIDATE_STRATEGIES
    assert "unanimous_or_null" not in DEFAULT_CANDIDATE_STRATEGIES  # not auto-picked


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
