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


# ── the last link: counts in, model out ──────────────────────────────

def _patterns_and_matrix(df, mk, n=400, seed=7):
    """The comparison rows `train_em` would build, plus their counted form."""
    from collections import Counter

    from goldenmatch.core.probabilistic import (
        _build_comparison_matrix,
        _row_lookup_for_pairs,
    )
    ids = df["__row_id__"].to_list()
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]][:n]
    cols = [f.field for f in mk.fields]
    lookup = _row_lookup_for_pairs(df, cols, [pairs])
    matrix = _build_comparison_matrix(pairs, lookup, mk)
    counts = Counter(tuple(int(v) for v in row) for row in matrix)
    return matrix, sorted(counts.items())


def _u_from(matrix, mk):
    """A fixed u, so the comparison isolates the m estimation."""
    out = {}
    for j, f in enumerate(mk.fields):
        col = matrix[:, j]
        obs = float((col >= 0).sum())
        out[f.field] = [
            (float((col == lvl).sum()) + 1e-6) / (obs + f.levels * 1e-6)
            for lvl in range(f.levels)
        ]
    return out


def test_counts_train_the_same_model_as_the_rows_they_stand_for():
    """THE gate for distributed training.

    `train_em_from_counts` over collapsed vectors must equal `_em_iterate` over
    the full row set, because collapsing regroups the terms of each sum without
    changing the total. Exact, not approximate -- if this drifts, a model
    trained on a cluster is quietly not the model the one-box would have built.
    """
    import numpy as np
    from goldenmatch.core.probabilistic import _em_iterate, train_em_from_counts

    df, mk = _frame(), _make_probabilistic_mk()
    matrix, patterns = _patterns_and_matrix(df, mk)
    u = _u_from(matrix, mk)

    n = matrix.shape[0]
    full_m, _, full_p, _, _ = _em_iterate(
        mk, matrix, np.zeros((n, 0), dtype=np.int64),
        np.zeros((n, len(mk.fields)), dtype=bool), np.zeros((n, 0), dtype=bool),
        np.ones(n), float(n), [], u, {}, set(), set(), None, None, 20, 0.001,
    )
    counted = train_em_from_counts(mk, patterns, u)

    assert len(patterns) < n, (
        f"{len(patterns)} patterns for {n} rows -- nothing collapsed, so this "
        f"would pass with the weighting removed"
    )
    for f in mk.fields:
        assert counted.m_probs[f.field] == pytest.approx(
            full_m[f.field], abs=1e-12
        ), f"{f.field}: counted and row-wise EM disagree"
    assert counted.proportion_matched == pytest.approx(full_p, abs=1e-12)


def test_a_vector_of_the_wrong_width_is_refused():
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk = _make_probabilistic_mk()
    with pytest.raises(ValueError, match="ordered by mk.fields"):
        train_em_from_counts(mk, [((0, 1), 5)], _u_from(*_patterns_and_matrix(_frame(), mk)[:1], mk))


def test_empty_counts_are_refused():
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk = _make_probabilistic_mk()
    matrix, _ = _patterns_and_matrix(_frame(), mk)
    with pytest.raises(ValueError, match="nothing to train on"):
        train_em_from_counts(mk, [], _u_from(matrix, mk))


def test_negative_evidence_is_refused_not_silently_dropped():
    """NE needs a per-pair matrix the counts do not carry. Training without it
    would produce a model the config did not ask for, and it would look fine."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk = _make_probabilistic_mk(
        negative_evidence=[
            NegativeEvidenceField(field="zip", scorer="exact", threshold=0.9)
        ]
    )
    matrix, patterns = _patterns_and_matrix(_frame(), _make_probabilistic_mk())
    with pytest.raises(NotImplementedError, match="negative-evidence"):
        train_em_from_counts(mk, patterns, _u_from(matrix, _make_probabilistic_mk()))


# ── u, the other half of the likelihood ratio ────────────────────────

def test_u_from_counts_equals_the_u_train_em_estimates():
    """THE gate for distributed u.

    `train_em` estimates u from a RANDOM pair sample:

        u[level] = (count(level) + 1e-6) / (observed + n_levels * 1e-6)

    which is a per-level count and a denominator that excludes unobserved
    (-1) entries. Both are sums over pairs, so counted vectors carry everything
    it needs -- and this asserts that against `train_em`'s ACTUAL output rather
    than against the formula rewritten in the test, which would pass even if
    both copies were wrong together.

    The sample is reproduced with the same seeded `_sample_pairs` call
    `train_em` makes, so the two see the same pairs.
    """
    from collections import Counter

    from goldenmatch.core.probabilistic import (
        _build_comparison_matrix,
        _row_lookup_for_pairs,
        _sample_pairs,
        estimate_u_from_counts,
    )

    df, mk = _frame(), _make_probabilistic_mk()
    n, seed = 400, 7

    # No blocks and no blocking_fields, so nothing is `always_conditioned` and
    # train_em applies no neutral-u override -- this compares the ESTIMATE, not
    # the blocking-field prior, which has its own test.
    trained = train_em(df, mk, n_sample_pairs=n, max_iterations=25, seed=seed)

    pairs = _sample_pairs(df, min(n, 5000), seed)
    lookup = _row_lookup_for_pairs(df, [f.field for f in mk.fields], [pairs])
    matrix = _build_comparison_matrix(pairs, lookup, mk)
    counts = sorted(Counter(tuple(int(v) for v in r) for r in matrix).items())
    assert len(counts) < len(matrix), "nothing collapsed; the test proves nothing"

    from_counts = estimate_u_from_counts(mk, counts)
    for f in mk.fields:
        assert from_counts[f.field] == pytest.approx(
            trained.u_probs[f.field], abs=1e-12
        ), f"{f.field}: counted u differs from the u train_em estimated"


def test_u_from_counts_excludes_unobserved_from_the_denominator():
    """A field null on one side is UNOBSERVED (-1), not a disagreement.

    Dividing by every pair instead of the observed ones would shrink every level
    of a sparsely-populated field toward zero in proportion to its missingness,
    inflating log2(m/u) for exactly the fields the data supports least.
    """
    from goldenmatch.core.probabilistic import estimate_u_from_counts

    mk = _make_probabilistic_mk()
    n_fields = len(mk.fields)
    # 10 pairs observed at level 0, 90 pairs unobserved, on the first field.
    obs = tuple([0] + [0] * (n_fields - 1))
    unobs = tuple([-1] + [0] * (n_fields - 1))
    u = estimate_u_from_counts(mk, [(obs, 10), (unobs, 90)])

    first = mk.fields[0]
    assert u[first.field][0] == pytest.approx(
        (10 + 1e-6) / (10 + first.levels * 1e-6), abs=1e-12
    ), "the 90 unobserved pairs must not be in the denominator"


def test_a_conditioned_field_gets_the_FIXED_weights_not_log2_m_over_u():
    """A blocking field's weights are the bounded -3..+3 ramp, not log2(m/u).

    `train_em` does this (probabilistic.py, "Fixed weights: linearly increasing
    from -3 to +3"), `estimate_m_from_labels` mirrors it, and the Rust
    `em_core.rs` implements it. The counted path did not, and the two halves of
    #1835 both broke:

      * the DISAGREEMENT PENALTY, which drives precision, was more than halved
        (-1.54 against -3.0 on the fixture below);
      * the AGREEMENT WEIGHT, bounded at +3.0 to preserve recall, ran to +4.47.

    A conditioned field's `m` is never updated -- it keeps the exponential
    init -- so `log2(m/u)` there is the ratio of an arbitrary initialisation to
    a random-pair estimate. It is not a quantity, and it lands in the model
    looking exactly like every other weight.
    """
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    patterns = [((0, 1), 500), ((1, 1), 300), ((0, 0), 150), ((1, 0), 50)]
    u = {"first": [0.9, 0.1], "zip": [0.97, 0.03]}

    em = train_em_from_counts(mk, patterns, u, conditioned_fields=("zip",))

    assert em.match_weights["zip"] == pytest.approx([-3.0, 3.0], abs=1e-12), (
        "a conditioned field must carry the bounded fixed ramp; got "
        f"{em.match_weights['zip']}"
    )
    # `first` is free, so it must still be LEARNED -- if it also came back
    # [-3, 3] the rule is being applied to everything.
    assert em.match_weights["first"] != pytest.approx([-3.0, 3.0], abs=1e-9)


def test_a_three_level_conditioned_field_ramps_across_its_levels():
    """The ramp is `-3 + 6k/(n-1)`, so 3 levels give -3, 0, +3.

    Hard-coding two levels would pass the test above and silently give a
    3-level blocking field a 2-element weight vector, which indexes out of
    range at scoring time or, worse, reads the wrong level's weight.
    """
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.8),
            MatchkeyField(field="city", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.7),
        ],
    )
    patterns = [((0, 2), 400), ((1, 2), 300), ((0, 0), 200), ((1, 1), 100)]
    u = {"first": [0.9, 0.1], "city": [0.8, 0.15, 0.05]}

    em = train_em_from_counts(mk, patterns, u, conditioned_fields=("city",))
    assert em.match_weights["city"] == pytest.approx([-3.0, 0.0, 3.0], abs=1e-12)


def test_a_conditioned_field_reports_the_NEUTRAL_u_not_the_one_passed_in():
    """`train_em` neutralises u for conditioned fields; the counted path must too.

    Found by the Rust parity gate (Phase 0), which is the point of having one:
    `em_core.rs` neutralised and Python did not, and the disagreement is
    invisible from either side alone.

    It does not change the E-step -- a conditioned field is skipped there -- so
    nothing about the trained m moves. What it changes is the u vector the
    persisted EMResult carries, which is half the model: a caller reading
    `em.u_probs["zip"]` off a model trained this way would see a near-unique
    random-pair estimate where `train_em` reports the fixed prior.
    """
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    em = train_em_from_counts(
        mk, [((1, 1), 500), ((0, 1), 300)],
        {"first": [0.9, 0.1], "zip": [0.999, 0.001]},
        conditioned_fields=("zip",),
    )

    assert em.u_probs["zip"] == pytest.approx([0.5, 0.5], abs=1e-12), (
        "the near-unique u handed in must not survive into the model for a "
        "field the blocking made unrepresentative"
    )
    assert em.u_probs["first"] == pytest.approx([0.9, 0.1], abs=1e-12), (
        "a free field must keep the u it was given"
    )


# ── Phase 1: the Rust kernel is the one that runs ────────────────────

def _native_em_available() -> bool:
    from goldenmatch.core._native_loader import native_module

    mod = native_module()
    return mod is not None and hasattr(mod, "train_em_from_counts_native")


def _counted_case():
    """A case with a learnable field AND a conditioned one.

    Both calibration rules the Rust port had to reproduce (`_neutral_u_for`,
    `_fixed_blocking_weights`) only fire for a conditioned field, so a fixture
    without one would compare the two paths on the half that never diverged.
    """
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.8),
            MatchkeyField(field="city", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.7),
        ],
    )
    patterns = [((1, 2), 500), ((0, 2), 300), ((1, 0), 150), ((0, 1), 50)]
    u = {"first": [0.9, 0.1], "city": [0.8, 0.15, 0.05]}
    return mk, patterns, u


@pytest.mark.skipif(not _native_em_available(),
                    reason="the native FS-EM kernel is not built here")
@pytest.mark.parametrize("conditioned", [(), ("city",)])
def test_the_native_kernel_and_the_python_fallback_agree(monkeypatch, conditioned):
    """THE Phase 1 gate: which path ran must not change the model.

    Decision-level, not bitwise. libm's ln/log2/exp differ from CPython's in the
    low mantissa bits, so 1e-9 on probabilities and 1e-7 on match weights -- the
    same tolerances the Rust-side fixture carries, and for the same reason.
    Asserting equality would fail on the first machine with a different libm and
    teach everyone to loosen it, which is how a real divergence gets waved
    through.
    """
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk, patterns, u = _counted_case()
    native = train_em_from_counts(mk, patterns, u, conditioned_fields=conditioned)

    # Force the fallback by gating the component off, which is the same switch
    # an operator has. Patching the module out would test a code path no
    # deployment can reach.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    fallback = train_em_from_counts(mk, patterns, u, conditioned_fields=conditioned)

    for f in mk.fields:
        n = f.field
        assert native.m_probs[n] == pytest.approx(fallback.m_probs[n], abs=1e-9), n
        assert native.u_probs[n] == pytest.approx(fallback.u_probs[n], abs=1e-9), n
        assert native.match_weights[n] == pytest.approx(
            fallback.match_weights[n], abs=1e-7
        ), n
    assert native.proportion_matched == pytest.approx(
        fallback.proportion_matched, abs=1e-9
    )
    assert native.converged == fallback.converged
    assert native.iterations == fallback.iterations, (
        "the two loops took a different number of iterations, so they are not "
        "the same loop even if the numbers landed close"
    )


@pytest.mark.skipif(not _native_em_available(),
                    reason="the native FS-EM kernel is not built here")
def test_the_native_path_is_actually_taken_when_available(monkeypatch):
    """A skipif-guarded parity test proves nothing if the native path never ran.

    This asserts the dispatch, not the numbers: without it, a call site that
    quietly returned `None` on every input would leave the test above comparing
    the fallback against itself and passing forever.
    """
    from goldenmatch.core._native_loader import (
        native_dispatch_report,
        reset_native_dispatch_log,
    )
    from goldenmatch.core.probabilistic import train_em_from_counts

    mk, patterns, u = _counted_case()
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    reset_native_dispatch_log()
    train_em_from_counts(mk, patterns, u, conditioned_fields=("city",))

    report = native_dispatch_report().get("fs_em", {})
    assert report.get("native", 0) >= 1, (
        f"fs_em did not dispatch native: {report}. The parity test above is "
        f"comparing the fallback with itself."
    )


@pytest.mark.skipif(not _native_em_available(),
                    reason="the native FS-EM kernel is not built here")
def test_the_kernel_refuses_a_level_outside_the_field_range():
    """A corrupt vector must raise, not index the wrong weight.

    The kernel range-checks rather than casting: a level of 7 on a 2-level field
    would otherwise read past the weight table or wrap, and both produce a
    number rather than an error.
    """
    from goldenmatch.core._native_loader import native_module

    fn = native_module().train_em_from_counts_native
    with pytest.raises(ValueError, match="outside"):
        fn([2, 2], [[1, 7]], [10.0], [[0.9, 0.1], [0.9, 0.1]], [False, False],
           20, 0.001)


@pytest.mark.skipif(not _native_em_available(),
                    reason="the native FS-EM kernel is not built here")
def test_the_kernel_refuses_a_non_positive_count():
    """A zero-count pattern is a row that should not have been emitted."""
    from goldenmatch.core._native_loader import native_module

    fn = native_module().train_em_from_counts_native
    with pytest.raises(ValueError, match="non-positive"):
        fn([2, 2], [[1, 1]], [0.0], [[0.9, 0.1], [0.9, 0.1]], [False, False],
           20, 0.001)
