"""The data-driven FS cut must not admit more pairs than EM says are matches.

`compute_thresholds`' data-driven branch picked the `1 - 2*match_rate`
percentile:

    link_idx = int(n * (1 - match_pct * 2))  # 2x match rate for headroom

so it deliberately admitted TWICE the match rate EM estimated. If EM says 30%
of blocked pairs are matches, that admits the top 60% -- by the model's own
estimate, half of what it admits is a non-match. That is a precision ceiling of
~0.5 BY CONSTRUCTION, before connected components chains anything.

Measured, person @ 1,000,000 rows (run 32063484894, `gm_probabilistic_shipped`,
which sets no `link_threshold` and so resolves through this function):

    pairwise  P 0.263  R 1.000  F1 0.416     TP 239,829  FP 673,277  FN 89
    B3        P 0.959  R 1.000  F1 0.979
    clusters  771,202 against splink's 801,820

Recall 1.000 with precision 0.263 is the signature of a cut chosen for headroom:
it finds everything and pays for it in false positives. Splink on the same
fixture, cutting at 0.85, scored F1 0.995 with EIGHT false positives.

## Why 1x rather than some other number

The model is the only thing here that knows how many matches to expect, so its
own estimate is the defensible bound: admit about as many pairs as EM says are
matches, not a multiple of it. Anything above 1x is choosing a precision ceiling
without saying so.

The change is self-limiting, which is what makes it safe -- though not for the
reason I first wrote. I assumed low match rates "nearly coincide"; measuring
showed the [0.40, 0.95] CLAMP is what decides at both ends:

    match_pct 0.01   2x percentile 0.98, 1x 0.99  -> both above the 0.95 ceiling
                                                     -> 0.95 either way, unchanged
    match_pct 0.30   2x percentile 0.40           -> at the 0.40 floor, admits 60%
    match_pct 0.45   2x percentile 0.10           -> below the floor, admits 60%

So the old rule did not merely admit "twice as many" at high match rates -- it
drove the percentile under the floor, and 0.40 then admitted 60% of pairs
REGARDLESS of the distribution. That is how a data-driven cut stopped depending
on the data.

## What this does NOT do

It does not touch the calibrated (posterior) branch, where thresholds are
absolute probabilities rather than distribution percentiles, nor the
`proportion_matched >= 0.5` degenerate guard added in #2627, nor the [0.40, 0.95]
clamp. Each is pinned below so a later change to them is a decision rather than
a side effect of this one.
"""
from __future__ import annotations

from goldenmatch.core.probabilistic import EMResult, compute_thresholds


def _em(proportion_matched: float) -> EMResult:
    return EMResult(
        m_probs={"a": [0.1, 0.9]}, u_probs={"a": [0.9, 0.1]},
        match_weights={"a": [-3.0, 3.0]}, converged=True, iterations=5,
        proportion_matched=proportion_matched,
    )


def _weights(n: int = 1000) -> list[float]:
    """A uniform 0..1 score distribution, so the percentile a cut lands on is
    readable straight off the returned threshold."""
    return [i / (n - 1) for i in range(n)]


def _admitted_fraction(link: float, weights: list[float]) -> float:
    return sum(1 for w in weights if w >= link) / len(weights)


def test_admitted_fraction_tracks_the_model_estimate():
    """The core claim: admit about what EM says is there, not double it.

    At a 30% match rate the old rule admitted ~60% of pairs. Anything near 0.6
    here means the headroom multiplier is back.
    """
    w = _weights()
    link, _review = compute_thresholds(_em(0.30), w, calibrated=False)
    admitted = _admitted_fraction(link, w)
    assert 0.25 <= admitted <= 0.35, f"admitted {admitted:.3f}, expected ~0.30"


def test_the_old_two_times_headroom_is_gone():
    """Pinned separately from the test above, because 'roughly right' and 'not
    the old behaviour' are different assertions and the second is the
    regression guard."""
    w = _weights()
    link, _ = compute_thresholds(_em(0.30), w, calibrated=False)
    assert _admitted_fraction(link, w) < 0.50, (
        "admitting >= 50% at a 30% match rate is the 2x headroom rule"
    )


def test_low_match_rates_are_unchanged_because_the_clamp_decides_there():
    """The change must be self-limiting, and measurement says why.

    At a 1% match rate BOTH rules put the percentile above the 0.95 clamp
    ceiling (2x -> 0.98, 1x -> 0.99), so the clamp returns 0.95 either way and
    the headroom multiplier never gets a say. The regime this fix targets is the
    other end, where 2x drives the percentile BELOW the 0.40 floor:

        match_pct 0.30   2x percentile 0.40 -> clamped -> admits 60%
        match_pct 0.45   2x percentile 0.10 -> clamped -> admits 60%

    So this is not a global tightening. Pinned as an exact value rather than a
    bound, because "unchanged" is the claim."""
    w = _weights()
    link, _ = compute_thresholds(_em(0.01), w, calibrated=False)
    assert link == 0.95, "the clamp ceiling, not the headroom, decides here"


def test_degenerate_guard_still_fires():
    """#2627 is untouched: at proportion_matched >= 0.5 the percentile carries
    no information and the fixed default is returned."""
    assert compute_thresholds(_em(0.638477), _weights(), calibrated=False) == (0.50, 0.35)


def test_clamp_still_applies():
    """The [0.40, 0.95] clamp is unchanged. A distribution living entirely below
    0.40 must still return the floor rather than a lower cut."""
    low = [0.0 + i / 5000 for i in range(1000)]  # 0.0 .. 0.2
    link, _ = compute_thresholds(_em(0.30), low, calibrated=False)
    assert link == 0.40


def test_calibrated_branch_is_untouched():
    """Posterior thresholds are absolute probabilities, not percentiles of a
    distribution, so this rule must not reach them."""
    assert compute_thresholds(_em(0.30), _weights(), calibrated=True) == (0.99, 0.50)


def test_review_stays_below_link():
    """The review band must remain a band. Whatever the percentiles do, review
    below link is an invariant callers rely on."""
    for pm in (0.01, 0.10, 0.30, 0.45):
        link, review = compute_thresholds(_em(pm), _weights(), calibrated=False)
        assert review <= link, f"review {review} > link {link} at match_pct={pm}"
