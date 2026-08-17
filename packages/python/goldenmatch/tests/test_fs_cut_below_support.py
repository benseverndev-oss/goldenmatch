"""A link cutoff below the whole score distribution filters NOTHING.

person @ 1,000,000 rows, `gm_probabilistic_shipped`, measured (run 32077679523):

    score_histogram: min 0.60, max 1.00, largest_gap 0.70 -> 0.80
    counts   0.60 -> 37,878   0.69 -> 12,486   0.80 -> 42,230
             0.90 -> 92,821   1.00 -> 91,421      (every bin below 0.60 is ZERO)

    applied link_threshold: 0.50, source "fallback"

The cut sits BELOW the minimum observed score, so every scored pair links.
That single fact produces every symptom at once:

    pairwise  P 0.2627  R 0.9996  F1 0.4160      (finds everything, filters nothing)
    B3        P 0.9590  R 0.9999
    clusters  771,202 against 799,927 true       (transitive chaining)
    splink, same fixture, cutting at 0.85: pairwise F1 0.9951, 801,731 clusters

It also explains the refit's decision, which looked like evidence and was not:

    {"reason": "no-max-reduction", "default_link": 0.5, "candidate": 0.6,
     "max_default": 618, "max_candidate": 618, "expelled_if_taken": 0.0}

Because nothing scores below 0.60, cutting at 0.50 and at 0.60 admit the SAME
pairs. `618 -> 618` and `expelled 0.0` were not "no repair is available" -- the
candidate was a literal no-op, and the guard had no way to say so. A decline
that reports equal maxima is ambiguous between "the candidate does not help"
and "the candidate does nothing at all", and those call for opposite responses.

`cut_is_inert` names it, costs one `min()`, and is recorded on every path.

## Why the valley proposed 0.60

FS scores here are DISCRETE -- five distinct values across 276,836 sampled
pairs, because the matchkey has five fields with mostly binary agreement
levels. `fs_refit_threshold` histograms into `_REFIT_BINS = 20` and looks for a
trough, so with five occupied bins it has almost nothing to work with and
settles on the bottom of the support. The real separation is the measured gap
at 0.70 -> 0.80, which no 20-bin trough search was going to find.
"""
from __future__ import annotations

import numpy as np
from goldenmatch.core.probabilistic import (
    _threshold_sweep,
    fs_refit_link_threshold,
)


def _discrete_person_shape():
    """The person@1M shape in miniature: nothing below 0.60, a gap at 0.70-0.80,
    and a weak band whose removal breaks the chain.

    120 groups of 12 chained at 0.90 (true clusters), bridged into ONE component
    by 0.60/0.69 links -- the band that a cut at 0.80 removes.
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
    for g in range(groups - 1):                 # weak bridges, below the gap
        a.append(g * group)
        b.append((g + 1) * group)
        s.append(0.60 if g % 2 == 0 else 0.69)
    return a, b, s


def test_the_fixture_has_no_mass_below_the_default():
    """Guard the guard: if anything scores under 0.50 this tests nothing."""
    _a, _b, s = _discrete_person_shape()
    assert min(s) == 0.60 > 0.50


def test_an_inert_cut_is_named_as_such():
    """The production signal. A cut below the support is not a threshold."""
    a, b, s = _discrete_person_shape()
    decision: dict = {}
    fs_refit_link_threshold(a, b, s, 0.50, decision_out=decision)
    assert decision.get("cut_is_inert") is True, (
        f"cut 0.50 admits every pair (min score {min(s)}) but the decision "
        f"does not say so: {decision}"
    )
    assert decision.get("score_min") == 0.60


def test_a_real_cut_is_not_flagged_inert():
    """Pinned so the flag means something. Cutting at 0.95 excludes real mass."""
    a, b, s = _discrete_person_shape()
    decision: dict = {}
    fs_refit_link_threshold(a, b, s, 0.95, decision_out=decision)
    assert decision.get("cut_is_inert") is False


def test_the_sweep_finds_the_cut_that_breaks_the_chain():
    """The sweep exists because the valley could not see this.

    Raising past the 0.70-0.80 gap must collapse the single over-merged
    component into the true groups of 12.
    """
    a, b, s = _discrete_person_shape()
    rows = _threshold_sweep(a, b, s, 0.50)
    by_cut = {r["cut"]: r for r in rows}

    assert by_cut[0.60]["max_component"] == 1440, "one chained component at the default"
    assert by_cut[0.90]["max_component"] == 12, "past the gap, the true groups"
    assert by_cut[0.90]["expelled"] == 0.0, "breaking the bridges strands nobody"


def test_the_sweep_is_bounded_on_continuous_scores():
    """Discrete scores give a handful of cuts; continuous scores must not give
    one row per distinct value. A diagnostic that is O(distinct scores) in
    clustering passes would be unusable on the shape it is most needed for."""
    rng = np.random.default_rng(0)
    n = 4000
    a = list(range(n))
    b = [i + n for i in range(n)]
    s = [float(x) for x in rng.uniform(0.5, 1.0, n)]
    rows = _threshold_sweep(a, b, s, 0.50)
    assert 0 < len(rows) <= 12, f"{len(rows)} rows"
    assert rows == sorted(rows, key=lambda r: r["cut"]), "cuts must be ordered"


def test_sweep_rows_carry_what_a_decision_needs():
    a, b, s = _discrete_person_shape()
    for row in _threshold_sweep(a, b, s, 0.50):
        assert set(row) >= {"cut", "linked_pairs", "max_component", "expelled"}
        assert 0.0 <= row["expelled"] <= 1.0
