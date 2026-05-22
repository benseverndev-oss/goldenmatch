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
    __source__ is present + clusters have enough members.

    Each cluster has 4 members: 2 from source A (both emit consensus),
    1 from B (agrees 50% of the time), 1 from C (always disagrees).
    Two members from A guarantee mode() = consensus deterministically.
    """
    rows = []
    consensus_per_cluster = {0: "Alice", 1: "Bob", 2: "Carol", 3: "Dan"}
    for cid, consensus in consensus_per_cluster.items():
        # Source a: 2 members per cluster, both = consensus (so mode is
        # deterministic regardless of what b and c emit).
        rows.append({"__source__": "a", "name": consensus})
        rows.append({"__source__": "a", "name": consensus})
        # Source b: 1 member; agrees on cid 0/1, disagrees on cid 2/3.
        rows.append({
            "__source__": "b",
            "name": consensus if cid < 2 else "Other",
        })
        # Source c: 1 member; always disagrees.
        rows.append({"__source__": "c", "name": "Wrong"})

    # Need >= 10 attempts per source per field for the agreement signal.
    # 4 base clusters × 4 copies = 16 clusters. Source A: 32 attempts
    # (2 per cluster), B: 16, C: 16 -- all above the threshold.
    full_rows = rows * 4
    for i, r in enumerate(full_rows):
        r["__row_id__"] = i

    df = pl.DataFrame(full_rows)

    # 16 clusters, each with 4 members.
    clusters: dict[int, dict] = {}
    members_per_cluster = 4
    for cluster_idx in range(16):
        base = cluster_idx * members_per_cluster
        clusters[cluster_idx] = {
            "members": list(range(base, base + members_per_cluster)),
            "size": members_per_cluster,
        }

    signals = compute_refinement_signals(clusters, df, column_profiles=[])

    assert "name" in signals.per_source_agreement
    rates = signals.per_source_agreement["name"]
    # Source A agrees 100% (both its members emit consensus, which is
    # the mode by 2-vote majority over b + c).
    assert rates["a"] > 0.95
    # Source B agrees on half the clusters (cid 0,1 yes; 2,3 no).
    assert 0.4 < rates["b"] < 0.6
    # Source C never agrees.
    assert rates["c"] < 0.05


def test_source_priority_rule_uses_agreement_over_completeness():
    """When agreement signal is present, it overrides completeness
    for the source_priority ranking. Uses 3 sources because the
    existing median calc (rates[len//2]) returns the larger of two
    sorted values for 2-source cases -- can't satisfy dominance there."""
    # Completeness: B dominates. Agreement: A dominates.
    # Refiner should rank by agreement -> A first.
    signals = RefinementSignals(
        within_cluster_spread={"name": 1.5},
        per_source_completeness={"name": {"a": 0.50, "b": 0.95, "c": 0.50}},
        per_source_agreement={"name": {"a": 0.95, "b": 0.40, "c": 0.30}},
        date_column_coverage={},
        col_type={"name": "name"},
        avg_len={"name": 10.0},
        null_rate={"name": 0.1},
    )
    result = _pick_strategy_for_field("name", signals)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "source_priority"
    # Agreement sorted desc: a=0.95, b=0.40, c=0.30. Median=0.40,
    # top/median=2.375 > 1.5. Dominance met; A wins.
    assert kwargs["source_priority"][0] == "a"


def test_source_priority_falls_back_to_completeness_when_no_agreement():
    """When agreement dict is empty (< 10 attempts), fall back to
    completeness for source_priority ranking. Uses 3 sources for
    the same dominance reason."""
    signals = RefinementSignals(
        within_cluster_spread={"name": 1.5},
        per_source_completeness={
            "name": {"a": 0.95, "b": 0.30, "c": 0.20},
        },
        per_source_agreement={},
        date_column_coverage={},
        col_type={"name": "name"},
        avg_len={"name": 10.0},
        null_rate={"name": 0.1},
    )
    result = _pick_strategy_for_field("name", signals)
    assert result is not None
    strategy, kwargs = result
    assert strategy == "source_priority"
    # Completeness sorted desc: a=0.95, b=0.30, c=0.20. Median=0.30,
    # top/median=3.17 > 1.5. Dominance met; A wins.
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


# ---------------------------------------------------------------------------
# #3: per-cluster strategy overrides
# ---------------------------------------------------------------------------


def test_cluster_overrides_field_exists_on_golden_rules_config():
    """GoldenRulesConfig has a cluster_overrides field defaulting to None."""
    cfg = GoldenRulesConfig(default_strategy="most_complete")
    assert cfg.cluster_overrides is None


def test_polars_native_fast_path_disabled_when_overrides_set():
    """Setting cluster_overrides forces the slow per-cluster path."""
    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import _polars_native_eligible

    cfg_no_overrides = GoldenRulesConfig(default_strategy="most_complete")
    assert _polars_native_eligible(cfg_no_overrides, quality_scores=None) is True

    cfg_with_overrides = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={
            42: {"name": GoldenFieldRule(strategy="unanimous_or_null")},
        },
    )
    assert _polars_native_eligible(cfg_with_overrides, quality_scores=None) is False


def test_refiner_sets_unanimous_or_null_on_weak_clusters():
    """A cluster with cluster_quality='weak' gets per-field overrides
    to unanimous_or_null."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": ["a", "b", "c", "d"],
    })
    clusters = {
        100: {
            "members": [0, 1], "size": 2, "cluster_quality": "weak",
            "oversized": False,
        },
    }
    out = refine_golden_rules(base, clusters, df, column_profiles=[])
    assert out.cluster_overrides is not None
    assert 100 in out.cluster_overrides
    assert out.cluster_overrides[100]["name"].strategy == "unanimous_or_null"


def test_refiner_sets_confidence_majority_on_oversized_clusters():
    """Oversized clusters get per-field confidence_majority overrides."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "name": ["a", "b", "c", "d", "e", "f"],
    })
    clusters = {
        200: {
            "members": [0, 1, 2, 3, 4, 5], "size": 6,
            "cluster_quality": "strong", "oversized": True,
        },
    }
    out = refine_golden_rules(base, clusters, df, column_profiles=[])
    assert out.cluster_overrides is not None
    assert 200 in out.cluster_overrides
    assert out.cluster_overrides[200]["name"].strategy == "confidence_majority"


def test_refiner_sets_unanimous_or_null_on_size_2_clusters():
    """Size-2 clusters get unanimous_or_null overrides (binary
    agreement; one disagreement = NULL is safer than picking one)."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "name": ["a", "b"],
    })
    clusters = {
        300: {
            "members": [0, 1], "size": 2,
            "cluster_quality": "strong", "oversized": False,
        },
    }
    out = refine_golden_rules(base, clusters, df, column_profiles=[])
    assert out.cluster_overrides is not None
    assert 300 in out.cluster_overrides
    assert out.cluster_overrides[300]["name"].strategy == "unanimous_or_null"


def test_refiner_does_not_set_overrides_on_strong_normal_clusters():
    """Strong cluster, not oversized, size > 2 -> no per-cluster override."""
    base = GoldenRulesConfig(default_strategy="most_complete", adaptive=True)
    df = pl.DataFrame({
        "__row_id__": list(range(5)),
        "name": list("abcde"),
    })
    clusters = {
        400: {
            "members": list(range(5)), "size": 5,
            "cluster_quality": "strong", "oversized": False,
        },
    }
    out = refine_golden_rules(base, clusters, df, column_profiles=[])
    if out.cluster_overrides is not None:
        assert 400 not in out.cluster_overrides


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
