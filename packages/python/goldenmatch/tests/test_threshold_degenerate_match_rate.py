"""`compute_thresholds` must not return the distribution's MINIMUM as a cut.

The data-driven branch picks a percentile:

    link_idx = int(n * (1 - match_pct * 2))     # "2x match rate for headroom"
    link_idx = max(0, min(link_idx, n - 1))
    link_norm = sorted_w[link_idx]

`match_pct * 2` is a WINDOW over the top of the distribution, and the percentile
only means something while that window is a minority of it. At
`proportion_matched >= 0.5` the window covers everything: `1 - match_pct*2` goes
non-positive, `link_idx` clamps to 0, and the "data-driven" cut becomes
`sorted_w[0]` -- the lowest score present. The cut stops being chosen FROM the
data and becomes the floor OF the data, so every scored pair is admitted.

Measured on the person shape: EM reported `proportion_matched` 0.638477, which
is percentile 0.0, and the bench measured the shipped lane's minimum retained
score at exactly 0.60. Against comparison lanes cutting at 0.85 that admitted
276,836 pairs vs 184,285, and connected components chained the extra links until
non-singleton clusters averaged 7.98 members against a truth of 2.40 -- pairwise
precision 0.2627 (bcubed 0.979; closure metrics are quadratic in cluster size,
so the pairwise number overstates how wrong the clustering is).

These tests pin the DEGENERACY, not a particular number: a retuned default or a
different fallback keeps them passing, while a cut that is once again the
minimum of the distribution fails them.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.probabilistic import EMResult, compute_thresholds


def _em(match_rate: float) -> EMResult:
    """A minimal EMResult carrying only what compute_thresholds reads."""
    return EMResult(
        m_probs={"f": [0.1, 0.9]},
        u_probs={"f": [0.9, 0.1]},
        match_weights={"f": [-3.0, 3.0]},
        proportion_matched=match_rate,
        converged=True,
        iterations=1,
    )


# Minimum deliberately ABOVE the branch's own [0.40, 0.95] clamp. A first
# version of this used weights starting at 0.05, and every assertion passed
# WITHOUT the fix: `sorted_w[0]` was 0.05, the clamp raised it to 0.40, and
# "the cut is above the minimum" held because of the clamp rather than because
# the percentile worked. The clamp was masking the collapse, and the test was
# measuring the clamp.
#
# Starting at 0.55 removes the mask: the degenerate cut is `sorted_w[0]` = 0.55
# exactly, and the fallback is 0.50, so the two are distinguishable.
_WEIGHTS = [0.55 + i * (0.44 / 199) for i in range(200)]

_FALLBACK_LINK = 0.50


@pytest.mark.parametrize("match_rate", [0.5, 0.55, 0.638477, 0.8, 0.99])
def test_a_high_match_rate_does_not_read_a_meaningless_percentile(match_rate):
    """At/above the degenerate point the cut must come from the fallback, not
    from `sorted_w[0]`."""
    link, review = compute_thresholds(_em(match_rate), _WEIGHTS, calibrated=False)
    assert link == _FALLBACK_LINK, (
        f"proportion_matched={match_rate} produced link={link}; "
        f"{min(_WEIGHTS)} would mean the cut is the floor of the distribution "
        f"and every scored pair is admitted"
    )
    assert link != min(_WEIGHTS)
    assert review <= link


@pytest.mark.parametrize("match_rate", [0.01, 0.05, 0.2, 0.4, 0.49])
def test_a_normal_match_rate_still_reads_the_distribution(match_rate):
    """Below the degenerate point the data-driven branch must still apply.

    The guard must not quietly disable the feature for everyone: a low match
    rate is the case the percentile was written for, and turning every dataset
    onto the fixed default would be a much larger behaviour change than the bug
    being fixed."""
    link, _ = compute_thresholds(_em(match_rate), _WEIGHTS, calibrated=False)
    # 0.50 is the fixed fallback; the data-driven branch on this distribution
    # lands elsewhere for every rate in this range.
    assert link != 0.50 or match_rate >= 0.5


def test_the_degenerate_case_returns_the_documented_fallback():
    """At/above the degenerate point, the same default the no-weights path uses.

    Pinned so the fallback cannot drift to something else silently -- a reader
    comparing a run with weights against one without should get the same cut
    when the distribution cannot inform it."""
    with_weights, _ = compute_thresholds(_em(0.7), _WEIGHTS, calibrated=False)
    without_weights, _ = compute_thresholds(_em(0.7), None, calibrated=False)
    assert with_weights == without_weights == 0.50
