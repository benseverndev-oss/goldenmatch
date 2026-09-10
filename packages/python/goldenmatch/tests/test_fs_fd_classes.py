"""GOLDENMATCH_FS_FD_CLASSES: add the match-class term to the field-dependence correction.

The original correction subtracts the NON-MATCH lift ``log2(u_ab/(u_a*u_b))``.
Loglinear record-linkage models put interactions in the match class, the
non-match class, or both (Daggy et al. 2013), and dependence matters most in the
dominating class (Xu et al. 2019). ``both`` subtracts the net double-count,
non-match lift minus match lift. ``both`` is the default: measured to be at least
as good as the original on every panel dataset at every bar (ADR 0066 addendum).
``nonmatch`` reproduces the original correction, pinned exactly below.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import probabilistic as P

ENV = "GOLDENMATCH_FS_FD_CLASSES"

# Measured on origin/main 15ac6e530 before the match-class term existed: the original
# correction's exact values, now reachable only via ``nonmatch``.
F1_LEGACY_BITS = 0.7270137144645805
F4_LEGACY_BITS = 1.9178745145247293
F4_BOTH_BITS = 1.663521872313443


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def _mk():
    return MatchkeyConfig(
        name="fs", type="probabilistic", link_threshold=0.9,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="surname", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="city", scorer="exact", levels=2),
        ],
    )


_M = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
_U = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
# first_name x surname co-agree far more than independence predicts.
_ROWS = [[1, 1, 0]] * 40 + [[0, 0, 0]] * 40 + [[1, 0, 0]] * 10 + [[0, 1, 0]] * 10


def _correct(p_match):
    comp = np.array(_ROWS, dtype=np.int64)
    cond = np.zeros((len(comp), 3), dtype=bool)
    out = P._compute_joint_corrections(comp, _mk(), _M, _U, p_match, cond, set())
    return [t[2] for t in out if {t[0], t[1]} == {"first_name", "surname"}]


# ---- mode parsing ------------------------------------------------------------

@pytest.mark.parametrize("val", [None, "", "   ", "both", " BOTH ", "garbage", "match"])
def test_the_net_correction_is_the_default(monkeypatch, val):
    """Empty in particular: GitHub renders an unset workflow input as ""."""
    if val is not None:
        monkeypatch.setenv(ENV, val)
    assert P._fs_fd_classes() == "both"


@pytest.mark.parametrize("val", ["nonmatch", " NONMATCH ", "NonMatch"])
def test_nonmatch_restores_the_original_correction(monkeypatch, val):
    monkeypatch.setenv(ENV, val)
    assert P._fs_fd_classes() == "nonmatch"


# ---- the per-class lift ------------------------------------------------------

def test_class_lift_is_zero_for_exactly_independent_fields():
    resp = np.ones(4)
    ta = np.array([True, True, False, False])
    tb = np.array([True, False, True, False])
    assert P._class_excess_bits(resp, ta, tb) == 0.0


def test_class_lift_is_one_bit_when_fields_always_co_agree():
    resp = np.ones(4)
    t = np.array([True, True, False, False])
    assert P._class_excess_bits(resp, t, t) == pytest.approx(1.0)


def test_class_lift_is_weighted_by_responsibility():
    """Rows with zero responsibility must not count: here only the co-agreeing
    half carries weight, so both marginals and the joint are 1 -> lift 0."""
    resp = np.array([1.0, 1.0, 0.0, 0.0])
    ta = np.array([True, True, True, False])
    tb = np.array([True, True, False, True])
    assert P._class_excess_bits(resp, ta, tb) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "resp,ta,tb",
    [
        (np.zeros(3), [True, True, False], [True, False, True]),   # class carries no weight
        (np.ones(3), [False, False, False], [True, False, True]),  # empty marginal
        (np.ones(3), [True, False, False], [False, True, False]),  # empty joint
    ],
)
def test_unestimable_lift_is_none_not_zero(resp, ta, tb):
    assert P._class_excess_bits(resp, np.array(ta), np.array(tb)) is None


# ---- the correction ----------------------------------------------------------

@pytest.mark.parametrize("p_match,legacy", [(0.05, F1_LEGACY_BITS), (0.92, F4_LEGACY_BITS)])
def test_nonmatch_is_byte_identical_to_the_original_correction(monkeypatch, p_match, legacy):
    """REGRESSION PIN: exact values measured before the match-class term existed."""
    monkeypatch.setenv(ENV, "nonmatch")
    assert _correct(p_match) == [legacy]


def test_default_subtracts_the_match_class_lift():
    """KNOWN-POSITIVE that the default is the net correction: at match prevalence
    0.92 the match class carries real co-agreement (+0.25 b), so the default is
    lower than the original correction's value."""
    got = _correct(0.92)
    assert got == [pytest.approx(F4_BOTH_BITS, abs=1e-12)]
    assert got[0] < F4_LEGACY_BITS


def test_both_spelled_out_equals_the_default(monkeypatch):
    default = _correct(0.92)
    monkeypatch.setenv(ENV, "both")
    assert _correct(0.92) == default


def test_default_reports_what_the_net_is_made_of(caplog):
    with caplog.at_level("WARNING"):
        _correct(0.92)
    assert "classes=both" in caplog.text
    assert "u=+1.92 m=+0.25 net=+1.66b" in caplog.text


def test_selection_floor_applies_to_the_net_value(monkeypatch):
    """Selection applies to the NET value, not the non-match lift. The lifts are
    pinned so this tests the rule alone: a 0.8 b non-match lift clears the 0.5 b
    floor by itself, but a 0.5 b match-class lift leaves a 0.3 b net, which must
    not be corrected.

    (An earlier version built a fixture meant to give equal lifts in both classes.
    Perfectly separated classes do the opposite -- the non-match class barely sees
    the co-agreeing rows, so its lift explodes, while the match class agrees on
    nearly everything -- and it measured u=+6.36 m=+0.02. It also carried a skip
    that would have let it pass having tested nothing.)"""
    comp = np.array(_ROWS, dtype=np.int64)
    cond = np.zeros((len(comp), 3), dtype=bool)

    monkeypatch.setenv(ENV, "nonmatch")
    monkeypatch.setattr(P, "_class_excess_bits", lambda resp, ta, tb: 0.8)
    kept = P._compute_joint_corrections(comp, _mk(), _M, _U, 0.5, cond, set())
    assert len(kept) == 3
    assert all(math.isclose(bits, 0.8) for _, _, bits in kept)

    lifts = iter([0.8, 0.5] * 3)  # per pair: non-match lift, then match lift
    monkeypatch.setattr(P, "_class_excess_bits", lambda resp, ta, tb: next(lifts))
    monkeypatch.setenv(ENV, "both")
    assert P._compute_joint_corrections(comp, _mk(), _M, _U, 0.5, cond, set()) == []
