"""Tests for precision-sensitive cohesion x coverage health proxy.

Task 1: cohesion statistics (_cluster_min_edges, _cohesion_min,
        _cohesion_mean_bottomk, _cohesion_edge_below_cutoff)
Task 2: suggestion_health_cohesion proxy
Task 3: env-gated selector in suggestion_health_from_clusters
"""
from goldenmatch.core.suggest.health import (
    _cluster_min_edges,
    _cohesion_edge_below_cutoff,
    _cohesion_mean_bottomk,
    _cohesion_min,
)


def _cl(*min_edges):
    return {
        i: {
            "size": 2,
            "oversized": False,
            "members": [2 * i, 2 * i + 1],
            "pair_scores": {(2 * i, 2 * i + 1): e},
            "confidence": e,
            "cluster_quality": "strong",
            "bottleneck_pair": (2 * i, 2 * i + 1),
        }
        for i, e in enumerate(min_edges)
    }


# ---------------------------------------------------------------------------
# Task 1 tests
# ---------------------------------------------------------------------------


def test_cluster_min_edges_extracts_per_cluster_min():
    assert sorted(_cluster_min_edges(_cl(0.9, 0.66, 0.8))) == [0.66, 0.8, 0.9]


def test_cohesion_min_is_global_min():
    assert _cohesion_min(_cl(0.9, 0.66, 0.8)) == 0.66


def test_cohesion_mean_bottomk_averages_weakest():
    assert abs(_cohesion_mean_bottomk(_cl(0.9, 0.66, 0.8), k=2) - 0.73) < 1e-9


def test_cohesion_edge_below_cutoff_fraction():
    assert abs(_cohesion_edge_below_cutoff(_cl(0.9, 0.66, 0.8), cutoff=0.75) - (1 - 1 / 3)) < 1e-9


def test_min_edges_includes_oversized_clusters():
    cl = _cl(0.9)
    cl[1] = {
        "size": 50,
        "oversized": True,
        "members": list(range(50)),
        "pair_scores": {(0, 1): 0.4},
        "confidence": 0.4,
        "cluster_quality": "strong",
        "bottleneck_pair": (0, 1),
    }
    assert min(_cluster_min_edges(cl)) == 0.4


def test_empty_returns_empty():
    assert _cluster_min_edges({}) == []


# ---------------------------------------------------------------------------
# Task 2 tests
# ---------------------------------------------------------------------------

from goldenmatch.core.suggest.health import suggestion_health_cohesion  # noqa: E402


def test_overmerge_scores_below_clean_at_same_matched_rate():
    clean = {
        i: {
            "size": 2,
            "oversized": False,
            "members": [2 * i, 2 * i + 1],
            "pair_scores": {(2 * i, 2 * i + 1): 0.80},
            "confidence": 0.80,
            "cluster_quality": "strong",
            "bottleneck_pair": (2 * i, 2 * i + 1),
        }
        for i in range(4)
    }
    degraded = {k: dict(v) for k, v in clean.items()}
    degraded[0] = dict(degraded[0])
    degraded[0]["pair_scores"] = {(0, 1): 0.66}
    n = 100
    assert suggestion_health_cohesion(degraded, n) < suggestion_health_cohesion(clean, n)


def test_recall_collapse_scores_low():
    assert suggestion_health_cohesion({}, 100) <= 0.0 + 1e-9


def test_under_merge_below_balanced_peak():
    tiny = {
        0: {
            "size": 2,
            "oversized": False,
            "members": [0, 1],
            "pair_scores": {(0, 1): 0.95},
            "confidence": 0.95,
            "cluster_quality": "strong",
            "bottleneck_pair": (0, 1),
        }
    }
    full = {
        i: {
            "size": 2,
            "oversized": False,
            "members": [2 * i, 2 * i + 1],
            "pair_scores": {(2 * i, 2 * i + 1): 0.95},
            "confidence": 0.95,
            "cluster_quality": "strong",
            "bottleneck_pair": (2 * i, 2 * i + 1),
        }
        for i in range(20)
    }
    assert suggestion_health_cohesion(tiny, 100) < suggestion_health_cohesion(full, 100)


# ---------------------------------------------------------------------------
# Task 3 tests
# ---------------------------------------------------------------------------

from goldenmatch.core.suggest import health as H  # noqa: E402


def test_cohesion_is_default_and_byte_identical(monkeypatch):
    # The default verify-gate proxy was flipped legacy -> cohesion (min_edge x
    # coverage-cap 0.50) per the 2026-06-26 verify-gate bake-off: cohesion_*_cap50
    # recovers 100% of real wins (net +2.63 F1) with zero real-pair net-negatives,
    # vs legacy's recall 0.286. `legacy` stays reachable via GOLDENMATCH_SUGGEST_HEALTH.
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_HEALTH", raising=False)
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_COHESION", raising=False)
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_COVERAGE_CAP", raising=False)
    cl = {
        0: {
            "size": 3,
            "oversized": False,
            "members": [0, 1, 2],
            "pair_scores": {(0, 1): 0.9, (1, 2): 0.8},
            "confidence": 0.85,
            "cluster_quality": "strong",
            "bottleneck_pair": (1, 2),
        }
    }
    assert H.suggestion_health_from_clusters(cl, 100) == H.suggestion_health_cohesion(cl, 100)


def test_legacy_still_available_via_env(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_HEALTH", "legacy")
    cl = {
        0: {
            "size": 3,
            "oversized": False,
            "members": [0, 1, 2],
            "pair_scores": {(0, 1): 0.9, (1, 2): 0.8},
            "confidence": 0.85,
            "cluster_quality": "strong",
            "bottleneck_pair": (1, 2),
        }
    }
    assert H.suggestion_health_from_clusters(cl, 100) == H._health_legacy(cl, 100)


def test_cohesion_env_routes_to_new_formula(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_HEALTH", "cohesion")
    cl = {
        0: {
            "size": 2,
            "oversized": False,
            "members": [0, 1],
            "pair_scores": {(0, 1): 0.66},
            "confidence": 0.66,
            "cluster_quality": "strong",
            "bottleneck_pair": (0, 1),
        }
    }
    assert H.suggestion_health_from_clusters(cl, 100) == H.suggestion_health_cohesion(cl, 100)
