"""Unit tests for _identify_failing_subprofile.

Spec §Design / Confidence gate -- priority order [data, blocking,
scoring, matchkey, cluster] (root causes upstream first).
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_controller import _identify_failing_subprofile
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    MatchkeyProfile,
    ProfileMeta,
    ScoringProfile,
)


def _green_profile() -> ComplexityProfile:
    """Builds a profile where every sub-profile reports GREEN."""
    return ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3, column_types={
            "a": "text", "b": "text", "c": "text",
        }),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=100,
            reduction_ratio=0.9, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
            singleton_block_count=0, oversized_block_count=0,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=100,
            mass_above_threshold=0.5, mass_in_borderline=0.1,
            dip_statistic=0.01,
        ),
        matchkey=MatchkeyProfile(),
        cluster=ClusterProfile(),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=1000,
            n_rows_full=1000, wall_clock_ms=0, seed=0,
        ),
    )


def test_data_red_returns_data():
    """Data sub-profile RED (n_rows == 0) -> 'data'."""
    p = _green_profile()
    import dataclasses
    p = dataclasses.replace(p, data=DataProfile(n_rows=0))
    assert _identify_failing_subprofile(p) == "data"


def test_blocking_red_returns_blocking():
    """Blocking sub-profile RED (n_blocks == 0) -> 'blocking'."""
    p = _green_profile()
    import dataclasses
    bp = BlockingProfile()  # n_blocks=0 default -> RED via health()
    p = dataclasses.replace(p, blocking=bp)
    assert _identify_failing_subprofile(p) == "blocking"


def test_scoring_red_returns_scoring():
    """Scoring RED (mass_above_threshold == 0 with candidates compared)."""
    p = _green_profile()
    import dataclasses
    sp = ScoringProfile(
        n_pairs_scored=100, candidates_compared=100,
        mass_above_threshold=0.0, mass_in_borderline=0.1,
    )
    p = dataclasses.replace(p, scoring=sp)
    assert _identify_failing_subprofile(p) == "scoring"


def test_priority_order_data_beats_blocking():
    """When multiple sub-profiles RED, data wins (root cause upstream)."""
    p = _green_profile()
    import dataclasses
    p = dataclasses.replace(
        p,
        data=DataProfile(n_rows=0),               # RED
        blocking=BlockingProfile(),               # RED
    )
    assert _identify_failing_subprofile(p) == "data"


def test_priority_order_blocking_beats_scoring():
    """Blocking RED + Scoring RED -> 'blocking' (upstream cause)."""
    p = _green_profile()
    import dataclasses
    p = dataclasses.replace(
        p,
        blocking=BlockingProfile(),
        scoring=ScoringProfile(
            n_pairs_scored=0, candidates_compared=0,
        ),
    )
    assert _identify_failing_subprofile(p) == "blocking"


def test_all_green_returns_empty_string():
    """Defensive: gate's RED-precondition means this shouldn't happen,
    but the helper must not raise. Returns '' so the error message
    degrades gracefully."""
    p = _green_profile()
    assert _identify_failing_subprofile(p) == ""
