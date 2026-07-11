"""Drives the reusable parity-oracle harness over every registered component.

Wave 0: all 5 kernels are byte/set-exact, so every component x seed case must
pass with an EMPTY accepted-divergence registry. This validates the harness
mechanics on known-exact code before a future wave adds a genuinely-divergent
kernel."""
from __future__ import annotations

import pytest
from goldencheck.core._native_loader import native_available

from tests.core import parity_harness as ph

native_only = pytest.mark.skipif(not native_available(), reason="native ext not built")


@native_only
@pytest.mark.parametrize("comp", ph.REGISTERED_COMPONENTS, ids=lambda c: c.name)
@pytest.mark.parametrize("seed", range(6))
def test_component_parity(comp: ph.Component, seed: int) -> None:
    problems = ph.compare(comp, seed)
    assert problems == [], "\n".join(problems)


def test_accepted_divergences_empty_in_wave0() -> None:
    # Wave 0 guarantee: nothing diverges yet. This is the tripwire a later wave
    # must consciously edit when it accepts its first divergence.
    assert ph.ACCEPTED_DIVERGENCES == ()
