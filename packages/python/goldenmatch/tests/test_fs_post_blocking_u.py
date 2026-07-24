"""FS post-blocking-u bias correction (GOLDENMATCH_FS_POST_BLOCKING_U).

Default-OFF lever: u is estimated from RANDOM pairs (global non-matches), but FS
only scores BLOCKED pairs where blocking-correlated fields agree far more often —
so a candidate-common field gets a random-inflated log2(m/u) weight and drives
false merges. When on, the final weights use u DECONVOLVED from the blocked-pair
level distribution: blocked_rate = p_match*m + (1-p_match)*u_blocked, and EM gives
m + p_match, so u_blocked = (blocked_rate - p_match*m)/(1-p_match). Deflates
candidate-common fields toward weight ~0; keeps discriminative fields.

OFF is byte-identical (empty correction; random-pair u is used).
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    _deconvolve_post_blocking_u,
    _fs_post_blocking_u_enabled,
)

FLAG = "GOLDENMATCH_FS_POST_BLOCKING_U"


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)


def _mk():
    return MatchkeyConfig(name="fs", type="probabilistic", threshold=0.8, fields=[
        MatchkeyField(field="authors", scorer="jaro_winkler", levels=2),
        MatchkeyField(field="title", scorer="jaro_winkler", levels=2),
    ])


def test_flag_default_off():
    assert _fs_post_blocking_u_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled"])
def test_flag_truthy(monkeypatch, truthy):
    monkeypatch.setenv(FLAG, truthy)
    assert _fs_post_blocking_u_enabled() is True


def _comp(authors_agree, title_agree, n=1000):
    au = np.array([1] * int(n * authors_agree) + [0] * (n - int(n * authors_agree)))
    ti = np.array([1] * int(n * title_agree) + [0] * (n - int(n * title_agree)))
    rng = np.random.default_rng(0)
    rng.shuffle(au)
    rng.shuffle(ti)
    return np.stack([au, ti], axis=1)


def test_deflates_candidate_common_field():
    # authors agrees 65% among blocked pairs (both match+non-match); title 35%.
    comp = _comp(0.65, 0.35)
    m = {"authors": [0.34, 0.66], "title": [0.02, 0.98]}
    u = _deconvolve_post_blocking_u(comp, _mk(), m, p_match=0.10, always_conditioned=set())
    w_authors = math.log2(m["authors"][1] / u["authors"][1])
    w_title = math.log2(m["title"][1] / u["title"][1])
    assert w_authors < 0.5   # candidate-common field deflated toward 0
    assert w_title > 1.0     # discriminative field kept positive


def test_u_is_a_distribution():
    comp = _comp(0.65, 0.35)
    m = {"authors": [0.34, 0.66], "title": [0.02, 0.98]}
    u = _deconvolve_post_blocking_u(comp, _mk(), m, p_match=0.10, always_conditioned=set())
    for f in ("authors", "title"):
        assert abs(sum(u[f]) - 1.0) < 1e-9
        assert all(x > 0 for x in u[f])


def test_blocking_fields_skipped():
    comp = _comp(0.65, 0.35)
    m = {"authors": [0.34, 0.66], "title": [0.02, 0.98]}
    u = _deconvolve_post_blocking_u(comp, _mk(), m, p_match=0.10,
                                    always_conditioned={"authors"})
    assert "authors" not in u   # blocking fields keep their fixed prior
    assert "title" in u


def test_high_p_match_clamped():
    # p_match near 1 must not divide-by-zero; clamped to 0.99.
    comp = _comp(0.65, 0.35)
    m = {"authors": [0.34, 0.66], "title": [0.02, 0.98]}
    u = _deconvolve_post_blocking_u(comp, _mk(), m, p_match=1.0, always_conditioned=set())
    assert all(all(x > 0 for x in v) for v in u.values())
