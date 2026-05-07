"""Verify _signals_view prefers controller_profile over legacy signals
and renders identical key-shape regardless of which is set.

Task #25 / Spec Task 6.1: migration of PostflightReport.signals consumers
to ComplexityProfile via the _signals_view() helper.
"""
import polars as pl
import pytest
import goldenmatch
from goldenmatch.core.autoconfig_verify import PostflightReport, _signals_view
from goldenmatch.core.complexity_profile import (
    ComplexityProfile,
    ScoringProfile,
    BlockingProfile,
    ClusterProfile,
    DataProfile,
)


def _make_profile(
    histogram: list[int] | None = None,
    reduction_ratio: float = 0.95,
    n_pairs_scored: int = 50,
    oversized_cluster_count: int = 0,
) -> ComplexityProfile:
    """Build a ComplexityProfile with sensible defaults for migration tests."""
    if histogram is None:
        histogram = [5] * 20
    return ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        scoring=ScoringProfile(
            score_histogram=histogram,
            mass_above_threshold=0.5,
            candidates_compared=100,
            n_pairs_scored=n_pairs_scored,
            dip_statistic=0.05,
        ),
        blocking=BlockingProfile(
            reduction_ratio=reduction_ratio,
            n_blocks=10,
            block_sizes_p99=20,
        ),
        cluster=ClusterProfile(
            cluster_size_p50=2,
            cluster_size_p99=5,
            cluster_size_max=8,
            oversized_cluster_count=oversized_cluster_count,
            transitivity_rate=0.95,
        ),
    )


# ── _signals_view routing ─────────────────────────────────────────────────────


def test_signals_view_prefers_controller_profile():
    """When controller_profile is set, _signals_view returns its to_legacy_dict()."""
    profile = _make_profile(histogram=[5] * 20)
    pf = PostflightReport(
        signals={"score_histogram": {"bins": [], "counts": [99] * 100}},  # legacy — ignored
        controller_profile=profile,
    )
    view = _signals_view(pf)
    # Must come from the typed profile, not the legacy dict
    assert view["score_histogram"] == [5] * 20


def test_signals_view_falls_back_to_legacy_signals():
    """When controller_profile is None, _signals_view returns the legacy dict."""
    pf = PostflightReport(
        signals={"score_histogram": {"bins": [], "counts": []}, "blocking_recall": 0.7},
        controller_profile=None,
    )
    view = _signals_view(pf)
    assert view["score_histogram"] == {"bins": [], "counts": []}
    assert view["blocking_recall"] == 0.7


def test_signals_view_returns_empty_dict_when_both_none():
    pf = PostflightReport(signals=None, controller_profile=None)
    view = _signals_view(pf)
    assert view == {}


def test_signals_view_returns_empty_dict_when_signals_is_empty_dict():
    """Empty signals dict (default factory) with no controller → empty view."""
    pf = PostflightReport()  # signals defaults to {}
    view = _signals_view(pf)
    assert view == {}


# ── to_legacy_dict key coverage ───────────────────────────────────────────────


def test_to_legacy_dict_has_required_keys():
    """to_legacy_dict() returns all PostflightSignals keys."""
    profile = _make_profile()
    d = profile.to_legacy_dict()
    required = {
        "score_histogram",
        "blocking_recall",
        "block_size_percentiles",
        "threshold_overlap_pct",
        "total_pairs_scored",
        "current_threshold",
        "preliminary_cluster_sizes",
        "oversized_clusters",
    }
    assert required.issubset(set(d.keys()))


def test_to_legacy_dict_score_histogram_is_list():
    """score_histogram in to_legacy_dict() is a list[int] from the typed profile."""
    hist = [i for i in range(20)]
    profile = _make_profile(histogram=hist)
    d = profile.to_legacy_dict()
    assert d["score_histogram"] == hist
    assert isinstance(d["score_histogram"], list)


def test_to_legacy_dict_blocking_recall_maps_reduction_ratio():
    """blocking_recall in to_legacy_dict() equals blocking.reduction_ratio."""
    profile = _make_profile(reduction_ratio=0.88)
    d = profile.to_legacy_dict()
    assert d["blocking_recall"] == pytest.approx(0.88)


def test_to_legacy_dict_total_pairs_scored():
    """total_pairs_scored comes from scoring.n_pairs_scored."""
    profile = _make_profile(n_pairs_scored=123)
    d = profile.to_legacy_dict()
    assert d["total_pairs_scored"] == 123


def test_to_legacy_dict_oversized_clusters_length():
    """oversized_clusters list length equals cluster.oversized_cluster_count."""
    profile = _make_profile(oversized_cluster_count=3)
    d = profile.to_legacy_dict()
    assert len(d["oversized_clusters"]) == 3


def test_to_legacy_dict_block_size_percentiles_shape():
    """block_size_percentiles has p50/p95/p99/max keys."""
    profile = _make_profile()
    d = profile.to_legacy_dict()
    bsp = d["block_size_percentiles"]
    assert "p50" in bsp
    assert "p95" in bsp
    assert "p99" in bsp
    assert "max" in bsp


def test_to_legacy_dict_preliminary_cluster_sizes_shape():
    """preliminary_cluster_sizes has p50/p95/p99/max/count keys."""
    profile = _make_profile()
    d = profile.to_legacy_dict()
    pcs = d["preliminary_cluster_sizes"]
    assert "p50" in pcs
    assert "p95" in pcs
    assert "p99" in pcs
    assert "max" in pcs
    assert "count" in pcs


def test_to_legacy_dict_current_threshold_sentinel():
    """current_threshold is 0.0 sentinel (not stored in ComplexityProfile)."""
    profile = _make_profile()
    d = profile.to_legacy_dict()
    assert d["current_threshold"] == 0.0


# ── end-to-end via dedupe_df ──────────────────────────────────────────────────


def test_signals_view_end_to_end_via_dedupe_df():
    """After gm.dedupe_df zero-config, _signals_view on the result's postflight
    report yields a non-empty dict with the expected keys."""
    df = pl.DataFrame({
        "name": ["alice", "alyce", "bob", "bobby"] * 10,
        "city": ["nyc", "la", "sf", "boston"] * 10,
    })
    result = goldenmatch.dedupe_df(df)
    assert result.postflight_report is not None
    view = _signals_view(result.postflight_report)
    # At minimum the score_histogram key must be present
    assert "score_histogram" in view
    # Histogram should be a list (from the typed profile when controller ran)
    assert isinstance(view["score_histogram"], (list, dict))
