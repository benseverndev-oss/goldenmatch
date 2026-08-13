"""One EM session per blocking pass, combined -- Splink's decomposition.

`train_em` pools every pass into one run and masks per (pair, field). That is
correct, and it couples every pass into a single convergence loop with an
aggregation key that has to carry the pass.

Splink runs a separate session per `blocking_rule_for_training`, records which
comparisons `cannot_be_estimated` under that rule, and combines. Two things
follow: sessions are independent (trainable anywhere), and WITHIN a session the
conditioning is constant -- so counting comparison vectors is `GROUP BY <gammas>`
with no pass column, which is the shape `count_agreement_patterns_sql` produces
and the reason the counts can be computed by a cluster.

These tests need no Spark. If the decomposition is wrong here, no amount of
distributed plumbing makes the trained model right.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.probabilistic import train_em, train_em_per_pass

from tests.test_probabilistic import _make_probabilistic_mk


def _frame(n_blocks: int = 14) -> pl.DataFrame:
    """Rows that block usefully on BOTH `zip` and `last_name`.

    Two passes are the point: a pass keyed on `zip` cannot estimate `zip`, and a
    pass keyed on `last_name` cannot estimate `last_name`, so each field is
    estimable in exactly one of them. That is the situation the decomposition
    exists for, and a fixture where both passes block on the same field would
    prove nothing.
    """
    first, last, zips, ids = [], [], [], []
    for b in range(n_blocks):
        for firstname, lastname, z in (
            (f"ann{b}", f"lee{b}", f"{b:02d}"),
            (f"ann{b}", f"lee{b}", f"{b:02d}"),
            (f"anna{b}", f"lee{b}", f"{b:02d}"),
            (f"bob{b}", f"lee{b}", f"{(b + 50):02d}"),
        ):
            ids.append(len(ids) + 1)
            first.append(firstname)
            last.append(lastname)
            zips.append(z)
    return pl.DataFrame(
        {"__row_id__": ids, "first_name": first, "last_name": last, "zip": zips}
    )


def _cfg(*passes: list[str]) -> BlockingConfig:
    """`build_blocks` takes a BlockingConfig; `passes` are BlockingKeyConfigs."""
    if len(passes) == 1:
        return BlockingConfig(keys=[BlockingKeyConfig(fields=list(passes[0]))])
    return BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=list(fs)) for fs in passes],
    )


def _blocks(df, cfg):
    return build_blocks(df, cfg)


def _train(fn, df, blocks, **kw):
    return fn(
        df, _make_probabilistic_mk(), blocks,
        n_sample_pairs=600, max_iterations=25, seed=11, **kw
    ) if fn is train_em_per_pass else fn(
        df, _make_probabilistic_mk(), blocks=blocks,
        n_sample_pairs=600, max_iterations=25, seed=11, **kw
    )


def test_a_single_pass_is_bit_identical_to_the_pooled_run():
    """The degenerate case must DELEGATE, not merely agree closely.

    With one pass there is nothing to decompose, and routing it through a
    combination step would need that step proven a no-op. Delegating makes it
    one by construction -- and it means adopting this function cannot move a
    single-pass model at all.
    """
    df = _frame()
    cfg = _cfg(["zip"])
    blocks = _blocks(df, cfg)

    pooled = _train(train_em, df, blocks)
    per_pass = _train(train_em_per_pass, df, blocks)

    assert per_pass.m_probs == pooled.m_probs
    assert per_pass.u_probs == pooled.u_probs
    assert per_pass.proportion_matched == pooled.proportion_matched


def test_two_passes_produce_two_sessions_and_a_combined_model():
    """The real case: each field estimable in the pass that does not block it."""
    df = _frame()
    cfg = _cfg(["zip"], ["last_name"])
    blocks = _blocks(df, cfg)
    passes = {tuple(b.blocking_fields) for b in blocks}
    assert len(passes) == 2, (
        f"the fixture produced {passes}; the decomposition needs two distinct "
        f"passes or this test is measuring the single-pass delegation"
    )

    em = _train(train_em_per_pass, df, blocks)

    for f in _make_probabilistic_mk().fields:
        probs = em.m_probs[f.field]
        assert len(probs) == f.levels
        assert all(p >= 0.0 for p in probs)
        assert sum(probs) == pytest.approx(1.0, abs=1e-9), (
            f"{f.field} m does not sum to 1 after combining sessions: {probs}"
        )


def test_a_field_blocked_by_ONE_pass_is_still_learned():
    """`zip` is conditioned out of the zip pass and free in the last_name pass.

    The pooled run gets this right by masking per pair; the decomposition has to
    get it right by EXCLUDING the session that cannot estimate it. If the
    combination naively averaged both sessions, the zip pass's fixed prior would
    be dragged into the answer -- which is the specific way this design fails,
    and it fails quietly because the result is still a valid probability vector.
    """
    df = _frame()
    blocks = _blocks(df, _cfg(["zip"], ["last_name"]))
    combined = _train(train_em_per_pass, df, blocks)

    only_lastname_pass = [b for b in blocks if tuple(b.blocking_fields) == ("last_name",)]
    assert only_lastname_pass, "fixture produced no last_name-keyed blocks"
    solo = _train(train_em, df, only_lastname_pass, blocking_fields=["last_name"])

    # `zip` is estimable in exactly one session, so the weighted mean over
    # estimable sessions IS that session -- exactly, not approximately.
    assert combined.m_probs["zip"] == pytest.approx(solo.m_probs["zip"], abs=1e-12), (
        "combined `zip` is not the estimate from the only pass that could make "
        "one, so a session that cannot estimate it is being averaged in"
    )


def test_the_combination_is_deterministic():
    """Sessions are combined in sorted pass order, so two runs agree.

    Iterating a dict of passes in insertion order would make the model depend on
    block discovery order -- reproducible on one machine and not on another.
    """
    df = _frame()
    blocks = _blocks(df, _cfg(["zip"], ["last_name"]))

    a = _train(train_em_per_pass, df, blocks)
    b = _train(train_em_per_pass, df, list(reversed(blocks)))

    for field in a.m_probs:
        assert a.m_probs[field] == pytest.approx(b.m_probs[field], abs=1e-12), field


def test_blocks_without_provenance_fall_back_to_the_pooled_run():
    """Custom/legacy `BlockResult` producers carry no `blocking_fields`.

    They must keep working rather than being silently treated as one anonymous
    pass whose conditioning is empty -- which would let every field be
    'estimable' and quietly change models that used to be conditioned.
    """
    df = _frame()
    blocks = _blocks(df, _cfg(["zip"]))
    for b in blocks:
        b.blocking_fields = ()

    pooled = _train(train_em, df, blocks, blocking_fields=["zip"])
    fell_back = _train(train_em_per_pass, df, blocks, blocking_fields=["zip"])

    assert fell_back.m_probs == pooled.m_probs
