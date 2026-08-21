"""The learned-blocking trainer must not lose its training signal as data grows.

`learned_sample_size` was `min(total_rows // 4, 5000)` -- pinned at 5,000 rows
from 50K upward however big the frame got. The learner trains on the TRUE PAIRS
found inside that sample, and the count of pairs with BOTH members sampled decays
as `s^2 / n`. Measured on the QIS realistic shape, ground-truth pairs inside a
5,000-row sample:

    n = 500K -> 86     n = 1M -> 47     n = 2M -> 25     n = 4M -> 14

The signal HALVES every time the data doubles. In CI at 5M the learner saw 13
true pairs (56 at 1M) and emitted 160 blocks of ~31K rows instead of 791 of
~1.3K -- ~78B candidate pairs against 0.6B, and the 5M rung ran 93 minutes
without finishing.

Growing the sample as sqrt(n) holds `s^2/n` constant. Verified end to end: at 5M
the trainer goes from 13 true pairs to 46, against 56 at 1M.

SCOPE NOTE, deliberately pinned below. This is anchored at 1M so it is a pure
extension: every frame at or below the anchor keeps its previous sample size
byte-for-byte, which is why no existing baseline moves. It is also NOT sufficient
on its own -- rule SELECTION is still scored on the sample, where a bounded-
cardinality predicate like `first_name:first_3` shows 13-row blocks that become
5,562-row blocks on the full frame. That is a separate defect.
"""
from __future__ import annotations

import math

from goldenmatch.core.autoconfig import (
    _LEARNED_SAMPLE_CEILING,
    _LEARNED_SAMPLE_FLOOR,
    _learned_sample_size,
)


def _previous_behaviour(total_rows: int) -> int:
    """What shipped before: a flat 5,000-row cap."""
    return min(total_rows // 4, 5000)


class TestUnchangedAtOrBelowTheAnchor:
    """The blast radius is bounded to frames LARGER than 1M, by construction."""

    def test_matches_previous_behaviour_up_to_1m(self):
        for n in (50_000, 100_000, 200_000, 500_000, 999_999, 1_000_000):
            assert _learned_sample_size(n) == _previous_behaviour(n), f"changed at n={n}"

    def test_small_frames_keep_the_quarter_guard(self):
        """Below 20K rows the held-out guard binds, not the floor."""
        assert _learned_sample_size(12_000) == 3_000
        assert _learned_sample_size(4_000) == 1_000


class TestGrowsWithScale:
    def test_sample_grows_above_the_anchor(self):
        """THE regression: a fixed sample is what let the signal decay."""
        assert _learned_sample_size(5_000_000) > _previous_behaviour(5_000_000)

    def test_growth_is_sqrt_so_the_pair_yield_stays_flat(self):
        """Pairs in a sample scale as s^2/n, so s must scale as sqrt(n) to hold
        s^2/n constant. Check the invariant directly rather than the formula."""
        anchor_n, anchor_s = 1_000_000, _learned_sample_size(1_000_000)
        anchor_yield = anchor_s**2 / anchor_n
        for n in (2_000_000, 5_000_000, 16_000_000):
            got = _learned_sample_size(n) ** 2 / n
            # Integer truncation only, so a tight tolerance is right here.
            assert math.isclose(got, anchor_yield, rel_tol=0.01), (
                f"pair yield drifted at n={n}: {got:.0f} vs {anchor_yield:.0f}"
            )

    def test_five_million_lands_where_the_measurement_says(self):
        """5M is the rung that failed; 11,180 is what restored 46 true pairs."""
        assert _learned_sample_size(5_000_000) == 11_180


class TestBounds:
    def test_never_below_the_historical_floor(self):
        """Shrinking the sample would be a silent quality regression."""
        for n in (50_000, 250_000, 1_000_000, 10_000_000):
            assert _learned_sample_size(n) >= min(n // 4, _LEARNED_SAMPLE_FLOOR)

    def test_ceiling_bounds_training_cost(self):
        """The sample run is superlinear in s, so this cannot grow forever."""
        assert _learned_sample_size(10_000_000_000) == _LEARNED_SAMPLE_CEILING

    def test_never_trains_on_more_than_a_quarter_of_the_frame(self):
        """The held-out guard must survive the scaling -- predicates have to
        generalize, not memorize."""
        for n in (50_000, 1_000_000, 5_000_000, 100_000_000):
            assert _learned_sample_size(n) <= n // 4
