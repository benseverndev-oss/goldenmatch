"""Two defects that kept a measured +0.562 F1 repair out of reach.

person @ 1,000,000 rows, one variable changed (run 32079034548):

    lane                       pairwise P       R      F1    B3 P   clusters
    gm_probabilistic_shipped       0.2627  0.9996  0.4160  0.9590    771,202
    gm_probabilistic_cut80         1.0000  0.9576  0.9783  1.0000    807,940
    splink (cuts at 0.85)          0.9999  0.9902  0.9951  1.0000    801,817
    (true clusters 799,927)

The cut is the whole story: not the scorers, not the calibration, not the
model. Two things stopped the refit reaching it.

## 1. The valley proposed a candidate that changed nothing

The shipped cut is 0.50 and the minimum score is 0.60, so the cut admits every
scored pair. The valley then proposed 0.60 -- the bottom of the support, the
SAME pair set. The guards duly reported `max_default 618 -> max_candidate 618`,
which is indistinguishable from "this candidate does not help", and that was
read as evidence for two rounds. A no-op candidate now falls back to one chosen
from the measured sweep; the guards still decide whether to take it.

## 2. The expelled cap asked the cost without asking what it bought

At 0.80 the sweep records expelled 0.1002 against a flat cap of 0.01, so the
safety guard would have refused a repair worth +0.562 F1. Scaling the allowance
by the reduction it achieves classifies every case with a measurement behind it:

    case                        max reduction  expelled  allowance  correct
    shattering (unit test)          5 -> 2      0.4060     0.0250   reject
    panel person                    3 -> 3      0.1020        n/a   reject
    panel household_hardneg         8 -> 3      0.0000     0.0267   accept
    panel cotenant_hardneg              n/a     0.0006      >0.01   accept
    person @ 1M, cut 0.80         618 -> 3      0.1002     0.2500   accept

panel person -- the one dataset that looks like it justifies a tight cap --
never reaches the expelled check at all: its max does not reduce, so the guard
above rejects it. Relaxing this cap therefore cannot regress it, which is the
whole reason the relaxation is safe.

## What was tried and reverted

Bypassing both guards whenever the cut is inert. It broke
`test_rejects_when_correct_clusters_would_be_shattered` and two others: a cut
admitting everything is only pathological when everything should not be
admitted, and expelled-share is precisely what measures that. The guards were
never the problem; the candidate and the cap were.
"""
from __future__ import annotations

import numpy as np
from goldenmatch.core.probabilistic import (
    _REFIT_MAX_EXPELLED_CEILING,
    _REFIT_MAX_EXPELLED_SHARE,
    _expelled_allowance,
    _expelled_share,
    _max_cluster_size,
    fs_refit_link_threshold,
)


def _noop_valley_shape():
    """person@1M in miniature: nothing below 0.60 against a 0.50 cut, so the
    valley's 0.60 admits exactly the pairs 0.50 already admits.

    120 true groups of 12 chained at 0.90, bridged into one 1,440-record
    component by a 0.60/0.69 band.
    """
    a: list[int] = []
    b: list[int] = []
    s: list[float] = []
    group, groups = 12, 120
    for g in range(groups):
        base = g * group
        for i in range(group - 1):
            a.append(base + i)
            b.append(base + i + 1)
            s.append(0.90)
    for g in range(groups - 1):
        a.append(g * group)
        b.append((g + 1) * group)
        s.append(0.60 if g % 2 == 0 else 0.69)
    return a, b, s


def test_the_fixture_reproduces_the_noop_candidate():
    """Guard the guard: the valley must actually return a no-op here, or the
    fallback below is never exercised and the test proves nothing."""
    from goldenmatch.core.probabilistic import fs_refit_threshold
    a, b, s = _noop_valley_shape()
    arr = np.asarray(s, dtype=np.float64)
    cand = fs_refit_threshold(arr, 0.50)
    assert cand > 0.50
    assert int((arr >= cand).sum()) == int((arr >= 0.50).sum()), (
        "the valley candidate must admit the same pairs as the default"
    )


def test_a_noop_candidate_falls_back_to_the_sweep():
    a, b, s = _noop_valley_shape()
    decision: dict = {}
    out = fs_refit_link_threshold(a, b, s, 0.50, decision_out=decision)

    assert decision.get("valley_candidate_was_noop") == 0.60
    assert out > 0.69, f"still admits the bridge band: {out} ({decision})"
    assert decision["reason"] == "committed"


def test_the_fallback_resolves_the_over_merge():
    """The point is the clustering, not the number."""
    a, b, s = _noop_valley_shape()
    decision: dict = {}
    fs_refit_link_threshold(a, b, s, 0.50, decision_out=decision)
    assert decision["max_default"] == 1440
    assert decision["max_candidate"] == 12, "must resolve to the true groups"
    assert decision["expelled"] == 0.0


def test_allowance_scales_with_what_the_candidate_repairs():
    """The person@1M case must pass and the shattering case must fail, on the
    measured numbers rather than on the shape of the formula."""
    assert _expelled_allowance(618, 3) >= 0.1002, "person@1M repair must be allowed"
    assert _expelled_allowance(5, 2) < 0.4060, "the shattering shape must not be"


def test_the_ceiling_is_bracketed_by_those_two_measurements():
    assert 0.1002 < _REFIT_MAX_EXPELLED_CEILING < 0.4060


def test_a_no_reduction_candidate_gets_no_extra_allowance():
    """Ratio 1 means nothing was repaired, so the allowance must not grow. (The
    max guard rejects these first; pinned so the formula is not load-bearing in
    the wrong direction if that ordering ever changes.)"""
    assert _expelled_allowance(9, 9) == _REFIT_MAX_EXPELLED_SHARE


def test_shattering_is_still_rejected_end_to_end():
    """The regression that killed the first attempt at this fix.

    One over-merged 5-clique dissolves (max 5 -> 2) while 100 CORRECT size-2
    clusters in the same low band shatter. The max guard accepts; only the
    expelled share sees the damage, and it must still see it after scaling.
    """
    pairs: list[tuple[int, int, float]] = []
    for i in range(5):
        for j in range(i + 1, 5):
            pairs.append((i, j, 0.55))
    for k in range(100):
        a = 1000 + 2 * k
        pairs.append((a, a + 1, 0.55))
    for k in range(150):
        a = 5000 + 2 * k
        pairs.append((a, a + 1, 0.95))
    ia = [p[0] for p in pairs]
    ib = [p[1] for p in pairs]
    sc = [p[2] for p in pairs]

    decision: dict = {}
    out = fs_refit_link_threshold(ia, ib, sc, 0.50, decision_out=decision)
    assert out == 0.50, f"shattered 205 of 505 matched records: {decision}"
    assert decision["reason"] == "expelled-share"
    assert _expelled_share(ia, ib, sc, 0.50, decision["candidate"]) > _expelled_allowance(
        _max_cluster_size(ia, ib, sc, 0.50),
        _max_cluster_size(ia, ib, sc, decision["candidate"]),
    )
