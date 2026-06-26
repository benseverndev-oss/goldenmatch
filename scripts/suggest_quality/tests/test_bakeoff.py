"""Unit tests for the verify-gate proxy bake-off (pure functions, no native)."""
from scripts.suggest_quality import bakeoff


def test_build_proxies_includes_legacy_and_cohesion_variants():
    proxies = dict(bakeoff.build_proxies())
    # legacy + the three cohesion statistics at the default cap, at minimum.
    assert "legacy" in proxies
    assert "cohesion_min_edge" in proxies
    assert "cohesion_mean_bottomk_edge" in proxies
    assert "cohesion_edge_below_cutoff_fraction" in proxies
    # every value is callable(clusters, n_records) -> float
    for name, fn in proxies.items():
        val = fn({}, 0)
        assert isinstance(val, float)


def test_legacy_proxy_matches_health_legacy():
    from goldenmatch.core.suggest import health
    proxies = dict(bakeoff.build_proxies())
    clusters = {1: {"size": 2, "members": [0, 1], "confidence": 0.9, "pair_scores": {(0, 1): 0.9}}}
    assert proxies["legacy"](clusters, 4) == health._health_legacy(clusters, 4)
