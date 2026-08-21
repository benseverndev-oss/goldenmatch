"""A refit DECLINE must be as visible as a refit COMMIT.

`fs_refit_link_threshold` picks a link cutoff from the scored-pair distribution
and commits it only when two guards agree. The commit path logged at INFO; all
three DECLINE paths logged at DEBUG. So on any shape where the refit declines --
which is the interesting case, because the cutoff then stays at the fixed
default -- a normal run said nothing about the decision that mattered most.

That asymmetry cost real time. person @ 1M over-merges badly:

    pairwise  P 0.263  R 1.000  F1 0.416   TP 239,829  FP 673,277  FN 89
    splink, same fixture:  F1 0.995 with EIGHT false positives

and the refit is DEFAULT ON, so it ran and declined -- silently. Two changes
aimed at `compute_thresholds` (#2627, the 2x-headroom fix) produced BYTE-
IDENTICAL output because neither touched the branch this path actually uses, and
nothing in the run said which decision was in force.

The three declines imply three different fixes, which is exactly why they must
be distinguishable from the log alone:

  * `no-valley`        -- the distribution has no gap above the default, so a
                          valley-based criterion cannot help at all;
  * `no-max-reduction` -- a candidate exists but does not shrink the LARGEST
                          cluster. Max cluster size is a single-outlier
                          statistic, so a shape with DIFFUSE over-merge (many
                          slightly-oversized clusters) cannot move it -- the
                          decline does not mean there is no over-merge;
  * `expelled-share`   -- a candidate would shrink the max but strand too many
                          matched records as singletons.
"""
from __future__ import annotations

import logging

from goldenmatch.core.probabilistic import fs_refit_link_threshold


def _flat_pairs(n: int = 60, score: float = 0.9):
    """Pairs with NO valley above the default: a single tight score band."""
    return ([i for i in range(n)], [i + 1000 for i in range(n)],
            [score] * n)


def test_no_valley_decline_is_visible_at_info(caplog):
    a, b, s = _flat_pairs()
    with caplog.at_level(logging.INFO, logger="goldenmatch.core.probabilistic"):
        out = fs_refit_link_threshold(a, b, s, 0.50)

    assert out == 0.50, "no valley above the default -> the default stands"
    assert any("refit DECLINED" in r.message for r in caplog.records), (
        "a decline must be visible at INFO; it was DEBUG, so a normal run was "
        "silent about the cutoff decision"
    )


def test_the_decline_reason_is_named(caplog):
    """Not just 'declined' -- WHICH guard. The three reasons imply three
    different fixes, so a log that only says 'declined' still leaves the next
    person guessing between them."""
    a, b, s = _flat_pairs()
    with caplog.at_level(logging.INFO, logger="goldenmatch.core.probabilistic"):
        fs_refit_link_threshold(a, b, s, 0.50)

    msgs = " ".join(r.message for r in caplog.records)
    assert any(tag in msgs for tag in
               ("no-valley", "no-max-reduction", "expelled-share")), (
        f"no decline reason named in: {msgs[:200]}"
    )


def test_decline_does_not_change_the_cutoff(caplog):
    """Observability only. Making a decision visible must not make it different
    -- otherwise the next measurement is of the logging change."""
    a, b, s = _flat_pairs()
    quiet = fs_refit_link_threshold(a, b, s, 0.50)
    with caplog.at_level(logging.INFO, logger="goldenmatch.core.probabilistic"):
        loud = fs_refit_link_threshold(a, b, s, 0.50)
    assert quiet == loud == 0.50


def test_below_min_pairs_is_untouched():
    """The `_REFIT_MIN_PAIRS` short-circuit lives in the caller, so a tiny input
    reaching this function still returns the default rather than raising."""
    assert fs_refit_link_threshold([0], [1], [0.9], 0.50) == 0.50
