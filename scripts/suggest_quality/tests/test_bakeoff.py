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
