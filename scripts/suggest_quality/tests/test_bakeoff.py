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


def _row(proxy, accept, f1_delta, dataset="d", pert="p", step=0):
    return {"proxy": proxy, "accept": accept, "f1_delta": f1_delta,
            "dataset": dataset, "perturbation": pert, "step": step}


def test_score_proxy_precision_and_recall():
    rows = [
        _row("A", accept=True, f1_delta=0.2),    # accepted real win
        _row("A", accept=False, f1_delta=0.1),   # missed win
        _row("A", accept=True, f1_delta=0.0),    # accepted neutral (not harmful)
    ]
    s = bakeoff.score_proxy([r for r in rows if r["proxy"] == "A"])
    assert s["n_accepted"] == 2
    assert s["n_accepted_harmful"] == 0
    assert s["n_real_wins"] == 2
    assert s["recall"] == 0.5          # 1 of 2 real wins accepted
    assert s["precision_safe"] == 1.0  # no accepted harmful


def test_select_best_disqualifies_accepted_harmful_then_maxes_recall():
    rows = [
        _row("A", accept=True, f1_delta=-0.3),
        _row("A", accept=True, f1_delta=0.2),
        _row("B", accept=True, f1_delta=0.2),
        _row("B", accept=False, f1_delta=0.1),
        _row("C", accept=True, f1_delta=0.2),
        _row("C", accept=True, f1_delta=0.1),
    ]
    winner, table = bakeoff.select_best(rows)
    assert winner == "C"
    assert table["A"]["n_accepted_harmful"] == 1
    assert table["C"]["recall"] == 1.0


def test_select_best_returns_none_when_all_disqualified():
    rows = [_row("A", accept=True, f1_delta=-0.1)]
    winner, table = bakeoff.select_best(rows)
    assert winner is None


def test_select_best_nan_recall_proxy_loses_to_real_recall():
    # Z has no real wins -> recall is nan; it must sort WORSE than A (recall 1.0),
    # never win on a nan comparison footgun. Both are eligible (no accepted harmful).
    rows = [
        _row("Z", accept=True, f1_delta=0.0),   # accepted neutral -> no real win
        _row("A", accept=True, f1_delta=0.2),   # accepted real win -> recall 1.0
    ]
    winner, table = bakeoff.select_best(rows)
    assert table["Z"]["recall"] != table["Z"]["recall"]  # nan
    assert winner == "A"


def test_select_best_tie_broken_by_lexically_smaller_name():
    # bbb and aaa: identical recall (1.0) AND n_accepted (1) -> lex-smaller wins.
    rows = [
        _row("bbb", accept=True, f1_delta=0.2),
        _row("aaa", accept=True, f1_delta=0.2),
    ]
    winner, _ = bakeoff.select_best(rows)
    assert winner == "aaa"


def test_default_health_proxy_is_cohesion_min_edge_cap50(monkeypatch):
    # Default (no env) must resolve to the bake-off winner: min_edge cohesion at
    # coverage-cap 0.50 -- NOT legacy.
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_HEALTH", raising=False)
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_COHESION", raising=False)
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_COVERAGE_CAP", raising=False)
    from goldenmatch.core.suggest import health

    clusters = {
        1: {"size": 2, "members": [0, 1], "confidence": 0.9, "pair_scores": {(0, 1): 0.9}},
        2: {"size": 3, "members": [2, 3, 4], "confidence": 0.6, "pair_scores": {(2, 3): 0.6, (3, 4): 0.7}},
    }
    n = 10
    default_val = health.suggestion_health_from_clusters(clusters, n)
    cohesion_val = health._cohesion_min(clusters) * health._coverage(clusters, n, cap=0.50)
    legacy_val = health._health_legacy(clusters, n)
    assert default_val == cohesion_val      # default routes to min_edge cohesion @ cap 0.50
    assert default_val != legacy_val        # and is NOT legacy (the flip happened)


def test_legacy_still_available_via_env(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_HEALTH", "legacy")
    from goldenmatch.core.suggest import health
    clusters = {1: {"size": 2, "members": [0, 1], "confidence": 0.9, "pair_scores": {(0, 1): 0.9}}}
    assert health.suggestion_health_from_clusters(clusters, 10) == health._health_legacy(clusters, 10)
