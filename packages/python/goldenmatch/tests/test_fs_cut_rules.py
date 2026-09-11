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


def test_schema_literal_matches_cut_rules():
    import typing

    ann = MatchkeyConfig.model_fields["link_cut_rule"].annotation
    literal = next(a for a in typing.get_args(ann) if typing.get_origin(a) is typing.Literal)
    assert typing.get_args(literal) == CUT_RULES


def test_link_cut_rule_accepts_rule_names_and_defaults_to_none():
    assert _mk().link_cut_rule is None
    pinned = _mk().model_copy(update={"link_cut_rule": "evidence_9"})
    assert pinned.link_cut_rule == "evidence_9"


def test_link_cut_rule_rejects_unknown_names():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MatchkeyConfig(
            name="fs", type="probabilistic", link_cut_rule="median",
            fields=[MatchkeyField(field="zip", scorer="exact", levels=2)],
        )


def test_otsu_splits_the_training_histogram_with_the_calibrator_clamp():
    """KNOWN-POSITIVE for the clamp: two point masses make every split between them tie,
    argmax takes the first (0.11), and the calibrator's 0.40 floor lifts it."""
    from goldenmatch.core.fs_cut_rules import otsu_bits

    counts = [0] * 100
    counts[10], counts[80] = 500, 200
    em = _em(0.01, training_score_histogram={"lo": -10.0, "hi": 14.0, "counts": counts})
    env = weight_envelope(_mk(), em)
    bits = otsu_bits(em, env)
    assert math.isclose(bits_to_normalized(bits, env), 0.40, abs_tol=1e-12)
    assert rule_bits("otsu", env, em) == bits


def test_otsu_needs_more_than_50_training_pairs():
    """Same floor as _calibrate_link_threshold: 50 pairs is too few to split."""
    from goldenmatch.core.fs_cut_rules import otsu_bits

    env = Envelope(lo=-10.0, hi=14.0)
    few = [0] * 100
    few[10], few[80] = 25, 25
    assert otsu_bits(
        _em(0.01, training_score_histogram={"lo": -10.0, "hi": 14.0, "counts": few}), env
    ) is None
    enough = list(few)
    enough[80] = 26
    assert otsu_bits(
        _em(0.01, training_score_histogram={"lo": -10.0, "hi": 14.0, "counts": enough}), env
    ) is not None


def test_cut_diagnostics_without_a_histogram():
    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    d = cut_diagnostics(_mk(), _em(0.01))
    assert (d.lo, d.hi, d.midpoint_bits) == (-10.0, 14.0, 2.0)
    assert math.isclose(d.prior_bits, math.log2(99.0), rel_tol=1e-12)
    assert d.n_fields == 2 and d.has_negative_evidence is False
    assert d.field_weight_spans == {"name": 16.0, "zip": 8.0}
    assert d.admitted_fraction is None


def test_cut_diagnostics_admitted_fraction():
    """Uniform counts over [-10, 14] bits: a cut at c bits admits the bins from
    ceil(100 * (c + 10) / 24) up."""
    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    em = _em(0.01, training_score_histogram={"lo": -10.0, "hi": 14.0, "counts": [10] * 100})
    adm = cut_diagnostics(_mk(), em).admitted_fraction
    assert math.isclose(adm["midpoint"], 0.50, abs_tol=1e-12)
    assert math.isclose(adm["evidence_3"], 0.45, abs_tol=1e-12)
    assert math.isclose(adm["evidence_12"], 0.08, abs_tol=1e-12)
    assert "otsu" in adm


def test_cut_diagnostics_is_none_without_weights():
    from goldenmatch.core.fs_cut_rules import cut_diagnostics

    assert cut_diagnostics(_mk(), SimpleNamespace(proportion_matched=0.01)) is None
