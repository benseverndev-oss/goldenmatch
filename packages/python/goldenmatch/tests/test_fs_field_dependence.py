"""FS field-dependence correction (GOLDENMATCH_FS_FIELD_DEPENDENCE).

FS sums per-field weights assuming conditional independence. When two fields
co-agree at their top level more than independence predicts among non-matches
(e.g. first_name x surname namesakes), FS over-counts ``log2(lift)`` bits and
over-merges. EM estimates the excess-lift per correlated pair; scoring subtracts
it when both agree. Default OFF = byte-identical.
"""
from __future__ import annotations

import numpy as np
import pytest

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import probabilistic as P
from goldenmatch.core.probabilistic import (
    EMResult,
    _compute_joint_corrections,
    _fs_field_dependence_enabled,
    _joint_correction_scalar,
)

FLAG = "GOLDENMATCH_FS_FIELD_DEPENDENCE"


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)


def _mk():
    return MatchkeyConfig(
        name="fs", type="probabilistic", threshold=0.8,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="surname", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="city", scorer="exact", levels=2),
        ],
    )


def test_flag_default_off():
    assert _fs_field_dependence_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled"])
def test_flag_truthy(monkeypatch, truthy):
    monkeypatch.setenv(FLAG, truthy)
    assert _fs_field_dependence_enabled() is True


def test_detects_correlated_pair():
    # first & surname co-agree at top level correlated among non-matches.
    rows = [[1, 1, 0]] * 40 + [[0, 0, 0]] * 40 + [[1, 0, 0]] * 10 + [[0, 1, 0]] * 10
    comp = np.array(rows, dtype=np.int64)
    cond = np.zeros((len(comp), 3), dtype=bool)
    m = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
    u = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
    jc = _compute_joint_corrections(comp, _mk(), m, u, 0.05, cond, set())
    pair = [t for t in jc if {t[0], t[1]} == {"first_name", "surname"}]
    assert pair, "should detect first_name x surname correlation"
    assert pair[0][2] >= P._FD_MIN_BITS


def test_no_correction_when_independent():
    # Fields agree independently -> no excess -> no correction.
    rng = np.random.default_rng(0)
    a = rng.integers(0, 2, 200)
    b = rng.integers(0, 2, 200)  # independent of a
    comp = np.stack([a, b, np.zeros(200, dtype=np.int64)], axis=1)
    cond = np.zeros((200, 3), dtype=bool)
    m = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
    u = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
    jc = _compute_joint_corrections(comp, _mk(), m, u, 0.05, cond, set())
    assert not any({t[0], t[1]} == {"first_name", "surname"} for t in jc)


def _em(joint=None):
    return EMResult(
        m_probs={}, u_probs={},
        match_weights={"first_name": [-1.0, 2.0], "surname": [-1.0, 2.0],
                       "city": [-1.0, 2.0]},
        converged=True, iterations=1, proportion_matched=0.05,
        joint_corrections=joint,
    )


def test_scoring_subtracts_when_both_top():
    em = _em([("first_name", "surname", 1.5)])
    assert _joint_correction_scalar([1, 1, 0], _mk(), em) == -1.5   # both top
    assert _joint_correction_scalar([1, 0, 0], _mk(), em) == 0.0    # partial
    assert _joint_correction_scalar([0, 0, 0], _mk(), em) == 0.0    # neither


def test_scoring_noop_when_off():
    assert _joint_correction_scalar([1, 1, 0], _mk(), _em(None)) == 0.0


def test_emresult_roundtrip_carries_corrections():
    em = _em([("first_name", "surname", 1.5)])
    back = EMResult.from_dict(em.to_dict())
    assert back.joint_corrections == [("first_name", "surname", 1.5)]
    # off -> not serialized
    assert "joint_corrections" not in _em(None).to_dict()
