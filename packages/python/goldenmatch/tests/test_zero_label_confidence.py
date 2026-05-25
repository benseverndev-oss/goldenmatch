"""Tests for the zero-label (ZeroER-inspired) confidence layer (Phase 1).

Design: docs/design/2026-05-25-zero-label-confidence-autoconfig-design.md.
Phase 1 is observational: these test the computed signals + guards directly,
not commit selection (pick_committed is unchanged in Phase 1).
"""
from __future__ import annotations

from goldenmatch.core.complexity_profile import (
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
    ZeroLabelConfidenceProfile,
)
from goldenmatch.core.zero_label_confidence import (
    compute_zero_label_confidence,
    perturbation_stability,
    profile_drift,
    threshold_perturbations,
)


def _profile(
    *,
    histogram: list[int] | None = None,
    dip: float = 0.08,
    mass_above: float = 0.4,
    mass_borderline: float = 0.05,
    random_pair: float | None = 0.01,
    n_pairs: int = 1000,
    n_clusters: int = 10,
    cluster_size_max: int = 3,
    oversized: int = 0,
    transitivity: float = 0.95,
    edge_p50: float = 0.0,
    edge_min: float = 0.0,
    n_rows: int = 100,
) -> ComplexityProfile:
    if histogram is None:
        # clean bimodal: low-score mass, empty middle, high-score mass
        histogram = [50] * 5 + [0] * 10 + [50] * 5
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows),
        scoring=ScoringProfile(
            n_pairs_scored=n_pairs,
            candidates_compared=n_pairs,
            score_histogram=histogram,
            dip_statistic=dip,
            mass_above_threshold=mass_above,
            mass_in_borderline=mass_borderline,
            random_pair_above_threshold_rate=random_pair,
        ),
        cluster=ClusterProfile(
            n_clusters=n_clusters,
            cluster_size_max=cluster_size_max,
            transitivity_rate=transitivity,
            oversized_cluster_count=oversized,
            edge_confidence_p50=edge_p50,
            edge_confidence_min=edge_min,
        ),
    )


def test_clean_bimodal_is_high_confidence():
    z = compute_zero_label_confidence(_profile())
    assert isinstance(z, ZeroLabelConfidenceProfile)
    assert z.latent_separation > 0.6
    assert z.overall_confidence > 0.6
    assert z.distribution_overlap == 0.05


def test_everything_matches_guard_caps_confidence():
    z = compute_zero_label_confidence(_profile(mass_above=0.95))
    assert z.overall_confidence <= 0.2
    assert any("everything-matches" in r for r in z.confidence_reasons)


def test_no_matches_is_low_confidence():
    z = compute_zero_label_confidence(_profile(mass_above=0.0, n_pairs=1000))
    assert z.overall_confidence <= 0.1
    assert any("no-matches" in r for r in z.confidence_reasons)


def test_cluster_collapse_guard():
    z = compute_zero_label_confidence(_profile(n_clusters=1, cluster_size_max=99, n_rows=100))
    assert z.overall_confidence <= 0.2
    assert any("cluster collapse" in r for r in z.confidence_reasons)


def test_singleton_clusters_have_zero_bridge_risk():
    z = compute_zero_label_confidence(_profile(cluster_size_max=1))
    assert z.cluster_bridge_risk == 0.0


def test_weak_bridge_raises_bridge_risk():
    # Wide edge-confidence spread (strong median, weak weakest edge) + low
    # transitivity -> chain-like cluster held by a weak bridge.
    z = compute_zero_label_confidence(
        _profile(cluster_size_max=5, edge_p50=0.9, edge_min=0.1, transitivity=0.3)
    )
    assert z.cluster_bridge_risk > 0.3
    assert any("weak-bridge" in r for r in z.confidence_reasons)


def test_tight_cluster_has_low_bridge_risk():
    z = compute_zero_label_confidence(
        _profile(cluster_size_max=5, edge_p50=0.92, edge_min=0.9, transitivity=0.98)
    )
    assert z.cluster_bridge_risk < 0.1


def test_low_transitivity_lowers_confidence():
    high = compute_zero_label_confidence(_profile(transitivity=0.98)).overall_confidence
    low = compute_zero_label_confidence(_profile(transitivity=0.3)).overall_confidence
    assert low < high
    z = compute_zero_label_confidence(_profile(transitivity=0.3))
    assert any("low transitivity" in r for r in z.confidence_reasons)


def test_random_pair_none_is_neutral_with_reason():
    z = compute_zero_label_confidence(_profile(random_pair=None))
    assert z.random_pair_contamination == 0.0
    assert any("random-pair contamination signal unavailable" in r for r in z.confidence_reasons)


def test_high_contamination_lowers_confidence():
    clean = compute_zero_label_confidence(_profile(random_pair=0.0)).overall_confidence
    dirty = compute_zero_label_confidence(_profile(random_pair=0.6)).overall_confidence
    assert dirty < clean


def test_deterministic():
    p = _profile()
    a = compute_zero_label_confidence(p)
    b = compute_zero_label_confidence(p)
    assert a == b


# --- #487: true articulation-point / bridge detection ---

def test_severe_bridge_count_detects_weak_link():
    from goldenmatch.core.cluster import _severe_bridge_count
    # {1,2} -- bridge(2,3) -- {3,4}: removing 2-3 splits into two 2-node parts.
    assert _severe_bridge_count([1, 2, 3, 4], {(1, 2): 0.9, (3, 4): 0.9, (2, 3): 0.8}) == 1
    # clique of 3: no bridge (every edge removal keeps it connected).
    assert _severe_bridge_count([1, 2, 3], {(1, 2): 0.9, (2, 3): 0.9, (1, 3): 0.9}) == 0
    # 3-node chain: a split would be 1 vs 2 nodes -> not "severe".
    assert _severe_bridge_count([1, 2, 3], {(1, 2): 0.9, (2, 3): 0.9}) == 0


def test_build_clusters_emits_measured_bridge_signal():
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.core.profile_emitter import profile_capture
    pairs = [(1, 2, 0.9), (3, 4, 0.9), (2, 3, 0.8)]  # two pairs joined by a weak bridge
    with profile_capture() as emitter:
        build_clusters(pairs, all_ids=[1, 2, 3, 4])
    cp = emitter.cluster
    assert cp is not None
    assert cp.bridge_edge_count >= 1
    assert cp.measured_bridge_risk is not None and cp.measured_bridge_risk > 0.0


def test_build_clusters_clique_has_no_measured_bridge():
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.core.profile_emitter import profile_capture
    with profile_capture() as emitter:
        build_clusters([(1, 2, 0.9), (2, 3, 0.9), (1, 3, 0.9)], all_ids=[1, 2, 3])
    cp = emitter.cluster
    assert cp.bridge_edge_count == 0
    assert cp.measured_bridge_risk == 0.0


def test_bridge_risk_uses_measured_signal_over_proxy():
    from goldenmatch.core.zero_label_confidence import score_cluster_bridge_risk
    # Proxy inputs say "clean" (no edge spread, full transitivity) but the
    # measured signal found a bridge -> measured wins.
    cp = ClusterProfile(
        n_clusters=5, cluster_size_max=4, transitivity_rate=1.0,
        edge_confidence_p50=0.9, edge_confidence_min=0.9,
        measured_bridge_risk=0.75,
    )
    assert score_cluster_bridge_risk(cp) == 0.75


def test_bridge_risk_falls_back_to_proxy_when_unmeasured():
    from goldenmatch.core.zero_label_confidence import score_cluster_bridge_risk
    cp = ClusterProfile(
        n_clusters=5, cluster_size_max=4, transitivity_rate=0.5,
        edge_confidence_p50=0.9, edge_confidence_min=0.3,
    )
    assert cp.measured_bridge_risk is None
    assert score_cluster_bridge_risk(cp) > 0.3  # heuristic proxy fires


# --- #490: expected precision / recall proxies (observational, non-None) ---

def test_expected_precision_proxy_drops_with_contamination():
    clean = compute_zero_label_confidence(_profile(random_pair=0.0)).expected_precision_proxy
    dirty = compute_zero_label_confidence(_profile(random_pair=0.9)).expected_precision_proxy
    assert clean is not None and dirty is not None
    assert 0.0 <= dirty <= 1.0 and 0.0 <= clean <= 1.0
    assert clean > 0.7  # clean separation + no random-pair contamination
    assert dirty < clean


def test_expected_recall_proxy_high_when_above_threshold_dominates():
    z = compute_zero_label_confidence(_profile(mass_above=0.4, mass_borderline=0.01))
    assert z.expected_recall_proxy is not None
    assert z.expected_recall_proxy > 0.9


def test_expected_recall_proxy_low_when_borderline_dominates():
    # Likely matches sit in the borderline band (just under threshold) -> uncaptured.
    z = compute_zero_label_confidence(_profile(mass_above=0.05, mass_borderline=0.45))
    assert z.expected_recall_proxy is not None
    assert z.expected_recall_proxy < 0.2


def test_pr_proxies_zero_when_nothing_scored():
    z = compute_zero_label_confidence(
        _profile(histogram=[0] * 20, n_pairs=0, mass_above=0.0, mass_borderline=0.0)
    )
    assert z.expected_precision_proxy == 0.0
    assert z.expected_recall_proxy == 0.0


def test_empty_histogram_no_crash():
    z = compute_zero_label_confidence(_profile(histogram=[0] * 20, n_pairs=0))
    assert z.latent_separation == 0.0
    assert any("no pairs scored" in r for r in z.confidence_reasons)


def test_attached_to_legacy_dict():
    p = _profile()
    z = compute_zero_label_confidence(p)
    import dataclasses

    p2 = dataclasses.replace(p, zero_label=z)
    legacy = p2.to_legacy_dict()
    assert legacy["zero_label"] is not None
    assert "overall_confidence" in legacy["zero_label"]
    # absent -> None (back-compat)
    assert p.to_legacy_dict()["zero_label"] is None


# --- Phase 2: perturbation-stability pure helpers ---

def _weighted_config(threshold: float = 0.8):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk", type="weighted", threshold=threshold,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0, transforms=[])],
        )],
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["name"], transforms=[])],
        ),
    )


def test_threshold_perturbations_shifts_each_threshold():
    variants = threshold_perturbations(_weighted_config(0.8))
    assert len(variants) == 2
    thresholds = sorted(round(v.get_matchkeys()[0].threshold, 4) for v in variants)
    assert thresholds == [0.75, 0.85]


def test_threshold_perturbations_empty_for_exact_only():
    from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig, MatchkeyField
    cfg = GoldenMatchConfig(matchkeys=[MatchkeyConfig(
        name="mk", type="exact",
        fields=[MatchkeyField(field="email", scorer="exact", weight=1.0, transforms=[])],
    )])
    assert threshold_perturbations(cfg) == []


def test_profile_drift_zero_for_identical():
    p = _profile()
    assert profile_drift(p, p) == 0.0


def test_profile_drift_positive_for_different_clusters():
    base = _profile(n_clusters=10, cluster_size_max=3)
    pert = _profile(n_clusters=2, cluster_size_max=50)
    assert profile_drift(base, pert) > 0.0


def test_perturbation_stability_aggregation():
    base = _profile()
    assert perturbation_stability(base, []) is None
    assert perturbation_stability(base, [base, base]) == 1.0
    drifted = _profile(n_clusters=1, cluster_size_max=99, transitivity=0.2)
    assert perturbation_stability(base, [drifted]) < 1.0
