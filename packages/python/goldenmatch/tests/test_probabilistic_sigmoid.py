"""Splink-style sigmoid match-probability normalization for Fellegi-Sunter.

The probabilistic scorers convert a summed log2-Bayes match weight W to a
match PROBABILITY via a sigmoid: P = 1 / (1 + 2^(-W)). Default ON; the
GOLDENMATCH_FS_SIGMOID=0 kill-switch restores the legacy min-max
normalization byte-identically.

These tests pin:
- the sigmoid math on a known total_weight,
- the default-on behaviour of the kill-switch helper,
- byte-identical legacy min-max under the kill-switch,
- the sigmoid-aware default thresholds from compute_thresholds,
- fast == slow parity under sigmoid (the fast path is the scale path).
"""
from __future__ import annotations

import os

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.matchkey import precompute_matchkey_transforms
from goldenmatch.core.probabilistic import (
    EMResult,
    _fs_sigmoid_enabled,
    comparison_vector,
    compute_thresholds,
    score_pair_probabilistic,
    score_probabilistic,
)
from goldenmatch.core.probabilistic_fast import (
    _resolve_probabilistic_fast_path,
    score_probabilistic_fast,
)


def _mk(levels: int = 2) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="prob",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=levels, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=levels, partial_threshold=0.8),
        ],
        link_threshold=0.5,
    )


def _em_2level() -> EMResult:
    return EMResult(
        m_probs={"first_name": [0.1, 0.9], "last_name": [0.05, 0.95]},
        u_probs={"first_name": [0.95, 0.05], "last_name": [0.97, 0.03]},
        match_weights={"first_name": [-3.25, 4.17], "last_name": [-4.27, 4.98]},
        converged=True,
        iterations=5,
        proportion_matched=0.1,
    )


def _em_simple() -> EMResult:
    """Tiny single-field EM with hand-computable weights."""
    return EMResult(
        m_probs={"name": [0.1, 0.9]},
        u_probs={"name": [0.9, 0.1]},
        match_weights={"name": [-3.0, 3.0]},
        converged=True,
        iterations=5,
        proportion_matched=0.05,
    )


def _set_killswitch(value: str | None):
    """Set or clear GOLDENMATCH_FS_SIGMOID, returning the prior value."""
    prior = os.environ.get("GOLDENMATCH_FS_SIGMOID")
    if value is None:
        os.environ.pop("GOLDENMATCH_FS_SIGMOID", None)
    else:
        os.environ["GOLDENMATCH_FS_SIGMOID"] = value
    return prior


def _restore_killswitch(prior: str | None):
    if prior is None:
        os.environ.pop("GOLDENMATCH_FS_SIGMOID", None)
    else:
        os.environ["GOLDENMATCH_FS_SIGMOID"] = prior


def test_sigmoid_default_on():
    """Helper returns True when env is unset."""
    prior = _set_killswitch(None)
    try:
        assert _fs_sigmoid_enabled() is True
    finally:
        _restore_killswitch(prior)


def test_sigmoid_killswitch_values():
    """0/false/disabled/no disable; anything else (incl. 1) enables."""
    prior = os.environ.get("GOLDENMATCH_FS_SIGMOID")
    try:
        for off in ("0", "false", "False", "DISABLED", "no", "  0  "):
            os.environ["GOLDENMATCH_FS_SIGMOID"] = off
            assert _fs_sigmoid_enabled() is False, off
        for on in ("1", "true", "yes", "on", "anything"):
            os.environ["GOLDENMATCH_FS_SIGMOID"] = on
            assert _fs_sigmoid_enabled() is True, on
    finally:
        _restore_killswitch(prior)


def test_sigmoid_math():
    """Emitted score == 1/(1+2^-W) for a hand-computed total_weight W."""
    prior = _set_killswitch(None)  # sigmoid ON
    try:
        mk = _mk(levels=2)
        em = _em_2level()
        row_a = {"first_name": "alice", "last_name": "smith"}
        row_b = {"first_name": "alice", "last_name": "smith"}
        # Comparison vector for identical names: both agree -> level 1.
        vec = comparison_vector(row_a, row_b, mk)
        assert vec == [1, 1]
        # W = match_weights[first_name][1] + match_weights[last_name][1]
        W = em.match_weights["first_name"][vec[0]] + em.match_weights["last_name"][vec[1]]
        expected = 1.0 / (1.0 + 2.0 ** (-W))
        score = score_pair_probabilistic(row_a, row_b, mk, em)
        assert score == pytest.approx(expected, abs=1e-9)
    finally:
        _restore_killswitch(prior)


def test_killswitch_restores_minmax():
    """GOLDENMATCH_FS_SIGMOID=0 returns the legacy min-max value exactly."""
    prior = os.environ.get("GOLDENMATCH_FS_SIGMOID")
    try:
        mk = _mk(levels=2)
        em = _em_2level()
        # Mixed pair: first_name agrees, last_name disagrees -> moderate W,
        # so min-max and sigmoid scores diverge clearly.
        row_a = {"first_name": "alice", "last_name": "smith"}
        row_b = {"first_name": "alice", "last_name": "jones"}
        vec = comparison_vector(row_a, row_b, mk)
        assert vec == [1, 0]
        W = em.match_weights["first_name"][vec[0]] + em.match_weights["last_name"][vec[1]]
        max_w = sum(max(em.match_weights[f.field]) for f in mk.fields)
        min_w = sum(min(em.match_weights[f.field]) for f in mk.fields)
        expected_minmax = (W - min_w) / (max_w - min_w)
        expected_sigmoid = 1.0 / (1.0 + 2.0 ** (-W))

        os.environ["GOLDENMATCH_FS_SIGMOID"] = "0"
        score_off = score_pair_probabilistic(row_a, row_b, mk, em)
        assert score_off == pytest.approx(expected_minmax, abs=1e-9)
        # And it genuinely differs from the sigmoid value.
        assert abs(expected_minmax - expected_sigmoid) > 0.01

        # Sanity: with sigmoid ON the same pair returns the sigmoid value.
        _set_killswitch(None)
        score_on = score_pair_probabilistic(row_a, row_b, mk, em)
        assert score_on == pytest.approx(expected_sigmoid, abs=1e-9)
    finally:
        _restore_killswitch(prior)


def test_compute_thresholds_sigmoid_default():
    """Fixed default is sigmoid-aware: (0.9, 0.5) on; (0.5, 0.35) off."""
    em = _em_simple()
    prior = os.environ.get("GOLDENMATCH_FS_SIGMOID")
    try:
        _set_killswitch(None)
        link, review = compute_thresholds(em)
        assert (link, review) == (0.9, 0.5)

        os.environ["GOLDENMATCH_FS_SIGMOID"] = "0"
        link_off, review_off = compute_thresholds(em)
        assert (link_off, review_off) == (0.5, 0.35)
    finally:
        _restore_killswitch(prior)


def _prepared(first_names: list[str], last_names: list[str], mk: MatchkeyConfig) -> pl.DataFrame:
    df = pl.DataFrame({
        "__row_id__": list(range(len(first_names))),
        "first_name": first_names,
        "last_name": last_names,
    })
    return precompute_matchkey_transforms(df, [mk])


def test_fast_slow_parity_under_sigmoid():
    """Fast path == slow path (pairs + scores) with sigmoid ON."""
    prior = _set_killswitch(None)  # sigmoid ON
    try:
        mk = _mk(levels=2)
        em = _em_2level()
        df = _prepared(
            ["alice", "alice", "bob", "alicia"],
            ["smith", "smith", "jones", "smyth"],
            mk,
        )
        fast_spec = _resolve_probabilistic_fast_path(mk, df, em)
        assert fast_spec is not None
        fast_pairs = score_probabilistic_fast(df, fast_spec)
        slow_pairs = score_probabilistic(df, mk, em)

        fast_keys = sorted((a, b) for a, b, _ in fast_pairs)
        slow_keys = sorted((a, b) for a, b, _ in slow_pairs)
        assert fast_keys == slow_keys, f"fast={fast_pairs} slow={slow_pairs}"
        for (fa, fb, fs), (sa, sb, ss) in zip(sorted(fast_pairs), sorted(slow_pairs)):
            assert (fa, fb) == (sa, sb)
            assert fs == pytest.approx(ss, abs=1e-6), f"({fa},{fb}) fast={fs} slow={ss}"
    finally:
        _restore_killswitch(prior)


def test_sigmoid_raises_recall_vs_minmax():
    """The motivating pathology: a high-variance EM weight (postcode +31.63)
    inflates max_weight, so a pair that agrees on the names but disagrees on
    postcode normalizes below the min-max link threshold and is rejected --
    even though its net evidence (W > 0) is positive. Sigmoid keys off W
    directly and accepts it at the same computed default threshold."""
    prior = os.environ.get("GOLDENMATCH_FS_SIGMOID")
    try:
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2, partial_threshold=0.8),
                MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.8),
                MatchkeyField(field="postcode", scorer="exact", levels=2, partial_threshold=0.99),
            ],
            link_threshold=None,  # use the computed default on each scale
        )
        em = EMResult(
            m_probs={
                "first_name": [0.1, 0.9],
                "last_name": [0.05, 0.95],
                "postcode": [0.3, 0.7],
            },
            u_probs={
                "first_name": [0.95, 0.05],
                "last_name": [0.97, 0.03],
                "postcode": [0.4, 0.6],
            },
            match_weights={
                # postcode agree weight (+31.63) is huge -> inflates max_weight;
                # the disagree weight (-0.5) is small (weak negative evidence).
                "first_name": [-3.25, 4.17],
                "last_name": [-4.27, 4.98],
                "postcode": [-0.5, 31.63],
            },
            converged=True,
            iterations=5,
            proportion_matched=0.05,
        )
        # Pair: names agree, postcode disagrees. Net W = 4.17 + 4.98 - 0.5 = 8.65 > 0.
        df = pl.DataFrame({
            "__row_id__": [0, 1],
            "first_name": ["alice", "alice"],
            "last_name": ["smith", "smith"],
            "postcode": ["10001", "99999"],
        })

        os.environ["GOLDENMATCH_FS_SIGMOID"] = "0"
        minmax_pairs = score_probabilistic(df, mk, em)

        _set_killswitch(None)
        sigmoid_pairs = score_probabilistic(df, mk, em)

        # min-max rejects the name-agreeing pair; sigmoid accepts it.
        assert minmax_pairs == []
        assert len(sigmoid_pairs) == 1
        assert sigmoid_pairs[0][2] > 0.9
    finally:
        _restore_killswitch(prior)
