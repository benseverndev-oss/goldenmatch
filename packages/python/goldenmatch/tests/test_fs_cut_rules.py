"""Link-cut rules as evidence cutoffs in bits (spec 2026-09-11-fs-cut-rule-routing-design)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.fs_cut_rules import (
    CUT_RULES,
    Envelope,
    bits_to_normalized,
    prior_bits,
    rule_bits,
    weight_envelope,
)


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _em(lam: float, **extra):
    # lo = -6 + -4 = -10, hi = 10 + 4 = 14; midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=None,
        **extra,
    )


def test_rule_names_are_fixed():
    assert CUT_RULES == (
        "midpoint", "prior", "prior_mid", "posterior_099",
        "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu",
    )


def test_envelope_sums_field_extremes():
    env = weight_envelope(_mk(), _em(0.01))
    assert env == Envelope(lo=-10.0, hi=14.0)
    assert env.midpoint == 2.0


def test_envelope_is_none_without_weights_or_range():
    assert weight_envelope(_mk(), SimpleNamespace(proportion_matched=0.01)) is None
    flat = SimpleNamespace(match_weights={"name": [1.0, 1.0, 1.0], "zip": [0.0, 0.0]},
                           proportion_matched=0.01)
    assert weight_envelope(_mk(), flat) is None


def test_prior_bits_is_log_odds_against_a_match():
    assert math.isclose(prior_bits(0.01), math.log2(99.0), rel_tol=1e-12)
    assert prior_bits(0.9) < 0


@pytest.mark.parametrize(
    "rule, lam, expected",
    [
        ("midpoint", 0.01, 2.0),
        ("prior", 0.01, math.log2(99.0)),
        ("prior", 0.9, 0.0),
        ("prior_mid", 0.4, 2.0),
        ("prior_mid", 0.01, math.log2(99.0)),
        ("posterior_099", 0.01, math.log2(99.0) + math.log2(99.0)),
        ("evidence_3", 0.01, 3.0),
        ("evidence_5", 0.01, 5.0),
        ("evidence_9", 0.01, 9.0),
        ("evidence_12", 0.01, 12.0),
    ],
)
def test_rule_bits(rule, lam, expected):
    env = weight_envelope(_mk(), _em(lam))
    assert math.isclose(rule_bits(rule, env, _em(lam)), expected, rel_tol=1e-12, abs_tol=1e-12)


def test_otsu_needs_a_training_histogram():
    env = weight_envelope(_mk(), _em(0.01))
    assert rule_bits("otsu", env, _em(0.01)) is None


def test_unknown_rule_raises():
    env = weight_envelope(_mk(), _em(0.01))
    with pytest.raises(ValueError, match="unknown link cut rule"):
        rule_bits("median", env, _em(0.01))


def test_bits_to_normalized_is_affine_and_clamped():
    env = Envelope(lo=-10.0, hi=14.0)
    assert bits_to_normalized(2.0, env) == 0.5
    assert bits_to_normalized(-100.0, env) == 0.0
    assert bits_to_normalized(100.0, env) == 1.0
