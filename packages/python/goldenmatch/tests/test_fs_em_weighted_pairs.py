"""EM over WEIGHTED comparison rows == EM over the pairs they stand for.

## Why this matters

`train_em` reads a driver-side sample of blocked pairs, and that is the whole
reason Fellegi-Sunter training does not distribute. The fix is not to move the
loop onto a cluster -- it is to notice that the loop does not need the pairs.

The E-step reads exactly three things per pair: the comparison vector (a level
per field, -1 unobserved), the pass conditioning (which fields this pair's
blocking pass makes uninformative) and the negative-evidence vector. Two pairs
agreeing on all three get the SAME posterior, every iteration. And every M-step
quantity is a plain sum over pairs:

    p_match        = sum(posterior) / n
    eligible_match = sum(posterior over eligible)
    new_m[level]   = sum(posterior over eligible & level) / eligible_match

All linear. So identical rows can be collapsed into ONE row carrying a count,
and the count multiplies through every sum. That is **exact, not a sample**.

The payoff is that the collapse is bounded: at most `prod(levels + 1)` distinct
vectors per blocking pass -- thousands, not millions -- no matter how many pairs
were compared. Counting comparison vectors is a `groupBy`, which distributes;
the iteration then runs on the tiny result. Splink aggregates the same way.

These tests are the load-bearing claim, and they need no Spark: if weighted and
unweighted disagree HERE, no amount of distributed plumbing makes the trained
model right.
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from goldenmatch.core.probabilistic import train_em

from tests.test_probabilistic import _make_probabilistic_mk


def _frame(n_blocks: int = 12) -> pl.DataFrame:
    """Blocks of near-duplicates, so blocked pairs carry a real match rate.

    Deliberately varied: pairs that agree on everything, on some fields, on
    none, plus nulls -- the aggregation is only interesting if several distinct
    comparison vectors occur, and only trustworthy if some occur MANY times.
    """
    first, last, zips, ids = [], [], [], []
    for b in range(n_blocks):
        z = f"{b:02d}"
        # 4 rows per block: an exact twin, a near twin, a mismatch, a null.
        for firstname, lastname in (
            (f"ann{b}", f"lee{b}"),
            (f"ann{b}", f"lee{b}"),
            (f"anna{b}", f"lee{b}"),
            (f"zed{b}", None),
        ):
            ids.append(len(ids) + 1)
            first.append(firstname)
            last.append(lastname)
            zips.append(z)
    return pl.DataFrame(
        {"__row_id__": ids, "first_name": first, "last_name": last, "zip": zips}
    )


def _train(df, mk, **kw):
    return train_em(df, mk, n_sample_pairs=400, max_iterations=25, seed=7, **kw)


def _as_dict(em):
    return {
        "m": {k: list(v) for k, v in em.m_probs.items()},
        "u": {k: list(v) for k, v in em.u_probs.items()},
        "proportion_matched": em.proportion_matched,
    }


def test_all_ones_weights_are_bit_identical_to_no_weights():
    """The default path must be unchanged, exactly.

    `pair_weights=None` and an explicit vector of 1.0 differ only by a
    multiplication by one, so anything but bit-identical means the weighted
    arithmetic is not the same arithmetic -- and every existing trained model
    would shift underneath callers who asked for nothing.
    """
    df, mk = _frame(), _make_probabilistic_mk()

    plain = _train(df, mk)
    ones = _train(df, mk, pair_weights=np.ones(400))

    # n_sample_pairs is a CEILING, so the real row count is whatever the
    # sampler produced; a mismatched length is a test-fixture problem, not a
    # finding, and it raises rather than silently comparing nothing.
    assert _as_dict(plain) == _as_dict(ones)


def test_a_wrong_length_weight_vector_is_refused():
    """Silently broadcasting or truncating would train on the wrong population
    and produce a plausible model."""
    df, mk = _frame(), _make_probabilistic_mk()
    with pytest.raises(ValueError, match="one weight per comparison row"):
        _train(df, mk, pair_weights=np.ones(3))


def test_non_positive_weights_are_refused():
    """A zero weight is a row that should not have been emitted, and a negative
    one is nonsense that would still produce numbers."""
    df, mk = _frame(), _make_probabilistic_mk()
    n = 400
    bad = np.ones(n)
    bad[0] = 0.0
    with pytest.raises(ValueError, match="positive"):
        _train(df, mk, pair_weights=bad)

def test_weights_are_COUNTS_and_the_scale_is_load_bearing():
    """Doubling every weight DOES move the model, and that is correct.

    I wrote this test first as "scale invariance", expecting a uniform scale to
    cancel out of every ratio. It does not, and the reason matters for anyone
    building on `pair_weights`:

        new_m[level] = (sum + 1e-6) / (eligible_match + n_levels * 1e-6)

    The `1e-6` is Laplace smoothing, and it is ADDITIVE and unscaled. So its
    influence shrinks as the weighted totals grow -- which is exactly what a
    prior should do with more evidence, and exactly why doubling the weights is
    not a no-op. Measured on a near-zero cell it moves by a factor of two.

    The contract this pins: **`pair_weights` are counts, and their sum is the
    number of pairs represented.** Under that contract aggregation is EXACT --
    collapsing identical comparison rows regroups the terms of each sum without
    changing the total, and the smoothing constant is unchanged because the
    represented population is unchanged.

    Pass scaled or normalised weights instead and the trained model shifts in
    the low-probability cells, which is where FS weights are largest in
    magnitude and a shift is least visible.
    """
    df, mk = _frame(), _make_probabilistic_mk()

    ones = _train(df, mk, pair_weights=np.ones(400))
    twos = _train(df, mk, pair_weights=np.full(400, 2.0))

    moved = [
        f for f in ones.m_probs
        if any(
            abs(a - b) > 1e-12
            for a, b in zip(ones.m_probs[f], twos.m_probs[f])
        )
    ]
    assert moved, (
        "doubling every weight left the model bit-identical, so the additive "
        "smoothing this test documents is no longer additive. If the smoothing "
        "was deliberately made scale-aware, weights stop being counts and the "
        "aggregation contract in the module docstring needs rewriting."
    )


def test_the_smoothing_moves_low_probability_cells_the_most():
    """Where the scale sensitivity actually lands, so nobody hunts it later.

    A cell carrying real weighted mass barely notices a 1e-6 prior. A cell with
    almost none is dominated by it, and those are precisely the cells whose
    log2(m/u) match weights are largest -- so an accidental rescale would move
    the strongest evidence in the model while the headline probabilities looked
    unchanged.
    """
    df, mk = _frame(), _make_probabilistic_mk()

    ones = _train(df, mk, pair_weights=np.ones(400))
    twos = _train(df, mk, pair_weights=np.full(400, 2.0))

    worst_field, worst_idx, worst_rel = None, None, 0.0
    for f, probs in ones.m_probs.items():
        for i, (a, b) in enumerate(zip(probs, twos.m_probs[f])):
            rel = abs(a - b) / max(a, b, 1e-300)
            if rel > worst_rel:
                worst_field, worst_idx, worst_rel = f, i, rel

    assert worst_field is not None
    smallest = min(ones.m_probs[worst_field])
    assert ones.m_probs[worst_field][worst_idx] == pytest.approx(smallest, rel=1e-9), (
        f"the largest scale sensitivity is in {worst_field}[{worst_idx}]="
        f"{ones.m_probs[worst_field][worst_idx]!r}, which is NOT that field's "
        f"smallest cell ({smallest!r}). The smoothing story in this file "
        f"explains sensitivity in near-zero cells; if it has moved elsewhere, "
        f"the explanation is wrong."
    )
