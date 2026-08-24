"""The over-merge detector has to be able to fire.

`ClusterProfile.red_reason` flagged `cluster_giant` on
`cluster_size_max > 0.1 * n_rows`. But `build_clusters` splits any cluster above
`max_cluster_size` (default 100) down to it, so `cluster_size_max` SATURATES at
the cap and can never reach that bar for n_rows >= 1000. The detector -- and
`rule_cluster_giant`, its only actor -- were dead code on every realistic
dataset (#2750).

Verified across 72 scored configs in docs/measurements/: `cluster_size_max`
maxes out at exactly 100, and the over-merged configs sit ON the cap while
scoring precision 0.008-0.023.

The fix reads `cluster_size_max` relative to the cap it saturates against.
These tests pin REACHABILITY -- that the condition can be met at all -- because
a bar above its own signal's ceiling is the defect, not the value of the bar.
"""

from __future__ import annotations

import pytest
from goldenmatch.core.complexity_profile import ClusterProfile

_CAP = 100


def _profile(cluster_size_max: int, *, cap: int = _CAP,
             transitivity: float = 1.0) -> ClusterProfile:
    """Transitivity defaults to 1.0 so `cluster_low_transitivity` -- the OTHER
    RED reason -- cannot be what fires. Otherwise these tests pass for the
    wrong reason."""
    return ClusterProfile(
        n_clusters=500, cluster_size_max=cluster_size_max,
        transitivity_rate=transitivity, max_cluster_size=cap,
    )


@pytest.mark.parametrize("n_rows", [1_000, 2_173, 4_589, 100_000])
def test_the_old_bar_was_unreachable_at_every_realistic_size(n_rows):
    """The bug, stated as arithmetic: a saturated signal cannot cross a bar
    that sits above its ceiling."""
    from goldenmatch.core.complexity_profile import _GIANT_CLUSTER_FRACTION

    assert _GIANT_CLUSTER_FRACTION * n_rows >= _CAP, (
        f"at n_rows={n_rows} the old bar is {_GIANT_CLUSTER_FRACTION * n_rows} "
        f"but cluster_size_max can never exceed {_CAP}"
    )


@pytest.mark.parametrize("n_rows", [2_173, 4_589, 100_000])
def test_a_cluster_pinned_to_the_cap_is_now_flagged(n_rows):
    """The Abt-Buy shape: clusters pinned at the cap, precision 0.023, and the
    old detector saw nothing."""
    assert _profile(_CAP).red_reason(n_rows) == "cluster_giant"


def test_a_healthy_cluster_shape_is_not_flagged():
    """The guard against the opposite error. Abt-Buy's BEST config sits at
    cluster_size_max=12 with F1 0.4059 -- flagging it would make the detector
    fire on the configs it should be steering toward."""
    assert _profile(12).red_reason(2_173) is None
    assert _profile(18).red_reason(4_589) is None  # Amazon-Google's best
    assert _profile(5).red_reason(6_000) is None   # NCVR-synthetic's best


def test_the_bar_sits_between_the_worst_and_the_best_measured_configs():
    """Fitted, not guessed. Every measured config at or above the bar scored
    worse than every config below it on the same lane."""
    # Abt-Buy: flagged region cmax 100..56 (F1 0.045-0.141)
    for cmax in (100, 90, 73, 58, 56):
        assert _profile(cmax).red_reason(2_173) == "cluster_giant", cmax
    # ...and the unflagged region cmax 29..12 (F1 0.198-0.406)
    for cmax in (29, 26, 20, 12):
        assert _profile(cmax).red_reason(2_173) is None, cmax


def test_an_unrecorded_cap_skips_the_check_rather_than_guessing():
    """`max_cluster_size=0` means "not recorded" -- older profiles, and the
    linkage lanes where `match_df` emits no cluster profile at all. Guessing a
    cap there would fire the detector on lanes that have no clustering."""
    p = ClusterProfile(n_clusters=500, cluster_size_max=100,
                       transitivity_rate=1.0, max_cluster_size=0)
    assert p.red_reason(2_173) is None


def test_the_small_frame_path_still_works():
    """The n_rows bar is genuinely reachable below ~1000 rows, so it stays."""
    p = ClusterProfile(n_clusters=10, cluster_size_max=60,
                       transitivity_rate=1.0, max_cluster_size=0)
    assert p.red_reason(100) == "cluster_giant"  # 60 > 0.1 * 100


# ── the rule's step size ─────────────────────────────────────────────────────
#
# Making the detector fire is not enough if its action moves too slowly to
# arrive. Abt-Buy dedupe commits threshold 0.70 against an optimum of >=0.95
# (F1 0.4059 vs 0.1361). At a flat +0.05 that is five firings -- and the rule
# spends its first firings offering splitting, so against a 4-iteration budget
# it lands ONE raise and stops at 0.75.


def test_the_step_scales_with_how_far_over_the_bar_the_run_is():
    """A run pinned at the cap is badly over-merged and should move decisively;
    one just over the bar should not."""
    from goldenmatch.core.autoconfig_rules import _giant_threshold_step

    pinned = _giant_threshold_step(_profile(100))
    mild = _giant_threshold_step(_profile(50))
    assert pinned > mild, "severity must drive the step"
    assert mild == pytest.approx(0.05), "at the bar, the old flat step"
    assert pinned == pytest.approx(0.20), "pinned at the cap, the full step"


def test_abt_buys_actual_shape_moves_in_one_step_not_five():
    """cluster_size_max=90 is what Abt-Buy dedupe actually reports. One firing
    should carry 0.70 into the high-0.8s, where the sweep measures F1 ~0.22,
    rather than to 0.75 where it measures 0.141."""
    from goldenmatch.core.autoconfig_rules import _giant_threshold_step

    step = _giant_threshold_step(_profile(90))
    assert 0.70 + step > 0.85, f"0.70 + {step:.3f} should clear 0.85"


def test_a_lane_that_needs_less_distance_takes_a_smaller_step():
    """Amazon-Google's optimum is 0.75, not 0.95. Its saturation is lower, so
    the same rule must not overshoot it -- the scaling is what prevents one
    global step size from being wrong for one lane or the other."""
    from goldenmatch.core.autoconfig_rules import _giant_threshold_step

    step = _giant_threshold_step(_profile(58))
    assert 0.70 + step == pytest.approx(0.774, abs=0.01)


def test_an_unrecorded_cap_falls_back_to_the_flat_step():
    """No cap recorded (older profiles, linkage lanes) -> do not invent a
    severity from a signal nobody measured."""
    from goldenmatch.core.autoconfig_rules import _giant_threshold_step

    assert _giant_threshold_step(_profile(100, cap=0)) == pytest.approx(0.05)


def test_the_ceiling_still_caps_the_raise():
    from goldenmatch.core.autoconfig_rules import (
        _GIANT_THRESHOLD_CEILING,
        _giant_threshold_step,
    )

    assert min(_GIANT_THRESHOLD_CEILING, 0.90 + _giant_threshold_step(_profile(100))) == (
        pytest.approx(_GIANT_THRESHOLD_CEILING)
    )
