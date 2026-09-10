"""GOLDENMATCH_FS_TF_CLAMP: override the bit clamp on the Winkler TF adjustment.

A measurement knob. TF under the default calibration regresses historical_50k
(-0.0125) and cotenant_hardneg (-0.0209) while inflating predicted pairs, and the
inflation shrinks as the evidence bar rises. Lowering the clamp tests whether the
size of the bump is the cause. The native kernel carries its own
``FS_TF_CLAMP = 10.0`` and takes no clamp argument, so an override has to route
off the kernel or it silently would not apply on the default route.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from goldenmatch.config.schemas import MatchkeyField
from goldenmatch.core import probabilistic as P
from goldenmatch.core.probabilistic import EMResult

ENV = "GOLDENMATCH_FS_TF_CLAMP"
RAW_BITS = math.log2(0.01 / 1e-4)  # ~6.64: under the 10-bit default, over a 3-bit override


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


@pytest.mark.parametrize("val", [None, "", "   ", "abc", "0", "-3"])
def test_unset_empty_invalid_or_nonpositive_means_default(monkeypatch, val):
    """Empty in particular: GitHub renders an unset workflow input as ""."""
    if val is not None:
        monkeypatch.setenv(ENV, val)
    assert P._fs_tf_clamp_override() is None
    assert P._tf_clamp_bits() == P._TF_CLAMP


def test_a_positive_value_overrides(monkeypatch):
    monkeypatch.setenv(ENV, " 4 ")
    assert P._fs_tf_clamp_override() == 4.0
    assert P._tf_clamp_bits() == 4.0


def _field():
    return MatchkeyField(field="surname", scorer="exact", levels=2, tf_adjustment=True)


def _em():
    return EMResult(
        m_probs={"surname": [0.1, 0.9]},
        u_probs={"surname": [0.9, 0.1]},
        match_weights={"surname": [-3.17, 3.17]},
        converged=True,
        iterations=1,
        proportion_matched=0.1,
        tf_freqs={"surname": {"zyx": 1e-4}},
        tf_collision={"surname": 0.01},
    )


@pytest.mark.parametrize("override,expected", [(None, RAW_BITS), ("3", 3.0)])
def test_scalar_site_honours_the_clamp(monkeypatch, override, expected):
    """The None case is the KNOWN-POSITIVE: the raw bump survives the default
    clamp intact, so the override has something real to cut."""
    if override is not None:
        monkeypatch.setenv(ENV, override)
    got = P._scalar_tf_contribution("zyx", "zyx", 1, _field(), _em())
    assert got == pytest.approx(expected)


@pytest.mark.parametrize("override,expected", [(None, RAW_BITS), ("3", 3.0)])
def test_vectorized_site_honours_the_clamp(monkeypatch, override, expected):
    """Both Python sites must agree, or the scalar and matrix routes would score
    the same pair differently under the same override."""
    if override is not None:
        monkeypatch.setenv(ENV, override)
    tw = np.zeros((2, 2))
    lvl = np.ones((2, 2), dtype=np.int64)
    P._apply_tf_adjustment(tw, ["zyx", "zyx"], lvl, _field(), _em(), 2)
    assert tw[0, 1] == pytest.approx(expected)


class _TfKernel:
    """A current wheel: TF crosses the FFI. Mirrors the fake in
    test_fs_name_scorers_native.py so eligibility is decided by the override,
    not by whatever wheel happens to be installed."""

    FS_SUPPORTS_MISSING_NEUTRAL = True
    FS_SUPPORTS_LEVEL_THRESHOLDS = True
    FS_SUPPORTS_TF_ADJUSTMENT = True

    def score_block_pairs_fs(self, *a, **kw):  # pragma: no cover - not invoked
        raise NotImplementedError


def _tf_mk():
    from goldenmatch.config.schemas import MatchkeyConfig

    return MatchkeyConfig(
        name="t", type="probabilistic", link_threshold=0.9, fields=[_field()],
    )


@pytest.mark.parametrize("override,eligible", [(None, True), ("10", False), ("3", False)])
def test_clamp_override_declines_the_kernel_route(monkeypatch, override, eligible):
    """The None case is the KNOWN-POSITIVE: a TF field IS kernel-eligible on a
    current wheel. Without it, a False under override could just mean this box
    has no usable kernel. An explicit 10 declines too -- the kernel's clamp is
    hardcoded, and routing every override off it is what makes =10 a control."""
    monkeypatch.setattr(P, "_fs_native_enabled", lambda: True)
    monkeypatch.setattr(
        "goldenmatch.core._native_loader.native_module", lambda: _TfKernel(),
    )
    if override is not None:
        monkeypatch.setenv(ENV, override)
    assert P._fs_native_eligible(_tf_mk()) is eligible
