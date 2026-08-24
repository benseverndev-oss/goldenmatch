"""Every RED verdict names its condition, and the name cannot drift from it.

Seven sub-profiles compute a health verdict; the rules in `DEFAULT_RULES` act on
three config surfaces. That asymmetry is how a run reaches RED with nothing to
do about it -- `DataProfile.health`'s own docstring records a signal that
"stayed YELLOW for all 5 controller iterations with no rule addressing it
because the verdict isn't actionable".

Naming each RED condition is what lets `test_rule_action_coverage.py` assert
that every one of them is answerable. `health()` derives from `red_reason()`
wherever the RED branches sit at the top of the function, so the two cannot
disagree; `MatchkeyProfile` computes a per-field max-severity and keeps them
separate, so an explicit agreement test covers it instead.
"""

from __future__ import annotations

import pytest
from goldenmatch.core.complexity_profile import (
    RED_REASONS,
    BlockingProfile,
    ClusterProfile,
    DataProfile,
    FieldStats,
    HealthVerdict,
    MatchkeyProfile,
    ScoringProfile,
)


def _field(cardinality: float) -> FieldStats:
    return FieldStats(
        post_transform_cardinality_ratio=cardinality,
        post_transform_null_rate=0.0,
        post_transform_value_length_p50=8,
    )


# ── the two conditions that had no rule (see test_rule_action_coverage) ──────


def test_cluster_giant_and_low_transitivity_are_distinct_reasons():
    giant = ClusterProfile(n_clusters=3, cluster_size_max=60, transitivity_rate=1.0)
    chained = ClusterProfile(n_clusters=50, cluster_size_max=4, transitivity_rate=0.2)
    assert giant.red_reason(n_rows=100) == "cluster_giant"
    assert chained.red_reason(n_rows=100) == "cluster_low_transitivity"


def test_matchkey_collapsed_field_is_named():
    collapsed = MatchkeyProfile(per_field={"country": _field(0.0)})
    assert collapsed.red_reason() == "matchkey_collapsed_field"


# ── agreement: a reason implies RED, and RED implies a reason ────────────────


@pytest.mark.parametrize(
    "profile,kwargs",
    [
        (DataProfile(n_rows=0, n_cols=0), {}),
        (DataProfile(n_rows=100, n_cols=3), {}),
        (DataProfile(n_rows=100, n_cols=1), {}),  # YELLOW, not RED
        (BlockingProfile(n_blocks=0), {"n_rows": 100}),
        (BlockingProfile(n_blocks=10, reduction_ratio=0.2), {"n_rows": 100}),
        (BlockingProfile(n_blocks=10, reduction_ratio=0.99), {"n_rows": 100}),
        (ScoringProfile(candidates_compared=0, n_pairs_scored=0), {}),
        (
            ScoringProfile(
                candidates_compared=500, n_pairs_scored=0, mass_above_threshold=0.0
            ),
            {},
        ),
        (
            ScoringProfile(
                candidates_compared=500,
                n_pairs_scored=500,
                mass_above_threshold=1.0,
                dip_statistic=0.5,
            ),
            {},
        ),
        (ClusterProfile(n_clusters=3, cluster_size_max=60, transitivity_rate=1.0), {"n_rows": 100}),
        (ClusterProfile(n_clusters=50, cluster_size_max=4, transitivity_rate=0.2), {"n_rows": 100}),
        (ClusterProfile(n_clusters=9, cluster_size_max=2, transitivity_rate=1.0), {"n_rows": 100}),
    ],
)
def test_red_reason_agrees_with_health(profile, kwargs):
    is_red = profile.health(**kwargs) == HealthVerdict.RED
    reason = profile.red_reason(**kwargs)
    assert (reason is not None) == is_red, (
        f"{type(profile).__name__}: health={profile.health(**kwargs)} but "
        f"red_reason={reason!r}"
    )


def test_matchkey_red_reason_agrees_with_health():
    """MatchkeyProfile keeps the two separate (its health is a per-field max
    severity), so agreement is asserted rather than structural."""
    for per_field, expect_red in [
        ({"country": _field(0.0)}, True),
        ({"name": _field(0.5)}, False),
        ({"name": _field(0.5), "country": _field(0.0)}, True),
        ({}, False),
    ]:
        profile = MatchkeyProfile(per_field=per_field)
        is_red = profile.health() == HealthVerdict.RED
        assert (profile.red_reason() is not None) == is_red is expect_red


# ── the registry ────────────────────────────────────────────────────────────


def test_every_reason_returned_is_registered():
    """A slug the gate does not know about is a hole it cannot see."""
    produced = {
        DataProfile(n_rows=0, n_cols=0).red_reason(),
        BlockingProfile(n_blocks=0).red_reason(n_rows=100),
        BlockingProfile(n_blocks=10, reduction_ratio=0.2).red_reason(n_rows=100),
        ScoringProfile(candidates_compared=0, n_pairs_scored=0).red_reason(),
        MatchkeyProfile(per_field={"c": _field(0.0)}).red_reason(),
        ClusterProfile(n_clusters=3, cluster_size_max=60, transitivity_rate=1.0)
        .red_reason(n_rows=100),
        ClusterProfile(n_clusters=50, cluster_size_max=4, transitivity_rate=0.2)
        .red_reason(n_rows=100),
    }
    assert produced <= RED_REASONS, f"unregistered: {sorted(produced - RED_REASONS)}"


def test_a_green_profile_names_nothing():
    assert DataProfile(n_rows=100, n_cols=3).red_reason() is None
    assert ClusterProfile(n_clusters=9, cluster_size_max=2,
                          transitivity_rate=1.0).red_reason(n_rows=100) is None
