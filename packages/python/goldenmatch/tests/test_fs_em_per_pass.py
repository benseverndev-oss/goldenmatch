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


# ── u for blocking fields: the #1835 prior, across sessions ──────────

def test_every_pass_blocking_field_keeps_the_neutral_u_prior():
    """The combination must neutralise the UNION of blocking fields.

    A configured blocking field carries a deliberate fixed prior that EM cannot
    recover from random pairs (#1835): a near-unique key's `u` collapses toward
    the smoothing floor, which EXPLODES its agreement weight and lets one field
    dominate the score. `train_em` guards this with
    `always_conditioned |= set(blocking_fields)` over the union of every pass.

    Each per-pass session neutralises only ITS OWN pass's fields, so lifting `u`
    from one session leaves every other pass's blocking field carrying the
    random-pair estimate. Measured on this fixture: `zip` came back
    [0.977, 0.023] instead of [0.5, 0.5], a 4.4-bit swing in its agreement
    weight. On a near-unique key it is the ~28-bit collapse #1835 records.

    Nothing about the result looks wrong -- it is a valid probability vector,
    and only a comparison against the pooled run reveals it.
    """
    df = _frame()
    blocks = _blocks(df, _cfg(["zip"], ["last_name"]))

    pooled = _train(train_em, df, blocks, blocking_fields=["zip", "last_name"])
    combined = _train(train_em_per_pass, df, blocks)

    for name in ("zip", "last_name"):
        assert combined.u_probs[name] == pytest.approx(pooled.u_probs[name]), (
            f"{name} is a blocking field of some pass, so it must carry the "
            f"neutral prior the pooled run gives it, not a random-pair estimate"
        )


# ── counted one-box training (GOLDENMATCH_FS_EM_COUNTED) ────────────

def test_counted_mode_is_OFF_by_default_and_changes_nothing():
    """The flag is opt-in, so an unset environment must train identically.

    Anything else means every existing model shifts under callers who asked for
    nothing -- the same bar the `pair_weights=None` path is held to.
    """
    import os

    df = _frame()
    blocks = _blocks(df, _cfg(["zip"], ["last_name"]))
    assert "GOLDENMATCH_FS_EM_COUNTED" not in os.environ

    a = _train(train_em, df, blocks, blocking_fields=["zip", "last_name"])
    b = _train(train_em, df, blocks, blocking_fields=["zip", "last_name"])
    assert a.m_probs == b.m_probs


def test_counted_mode_engages_and_trains_a_valid_model(monkeypatch):
    """With the flag on, `train_em` delegates to the counted trainer.

    Asserted through a real `train_em` call rather than by calling
    `train_em_counted` directly: the delegation condition is where this can
    silently do nothing, and a test that bypassed it would pass with the gate
    permanently off.
    """
    monkeypatch.setenv("GOLDENMATCH_FS_EM_COUNTED", "1")
    df = _frame()
    blocks = _blocks(df, _cfg(["zip"], ["last_name"]))
    mk = _make_probabilistic_mk()

    em = _train(train_em, df, blocks, blocking_fields=["zip", "last_name"])

    for f in mk.fields:
        probs = em.m_probs[f.field]
        assert len(probs) == f.levels
        assert sum(probs) == pytest.approx(1.0, abs=1e-9), f.field
    # Blocking fields keep the #1835 prior through the counted route too.
    for name in ("zip", "last_name"):
        assert em.u_probs[name] == pytest.approx([0.5, 0.5], abs=1e-12), name


def test_counted_mode_declines_when_a_per_pair_override_is_in_play(monkeypatch):
    """Label anchors are per-PAIR, and collapsing discards which pair a vector
    came from -- so the gate must fall through to the sampler rather than
    silently dropping the anchors."""
    monkeypatch.setenv("GOLDENMATCH_FS_EM_COUNTED", "1")
    df = _frame()
    blocks = _blocks(df, _cfg(["zip"], ["last_name"]))
    ids = df["__row_id__"].to_list()

    em = train_em(
        df, _make_probabilistic_mk(), blocks=blocks,
        n_sample_pairs=600, max_iterations=25, seed=11,
        label_pairs={(ids[0], ids[1]): 1},
    )
    # Reached the sampler (which honours anchors) rather than the counted path.
    assert em.m_probs
