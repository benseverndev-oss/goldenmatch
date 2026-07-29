"""Byte-parity gate for the owned Hartigan dip kernel (``core._dip``).

Asserts ``goldenmatch.core._dip.hartigan_dip`` reproduces ``diptest.dipstat``
EXACTLY across uniform / unimodal / multimodal / heavily-tied / degenerate
inputs. ``diptest`` is a test-only oracle (workspace dev group); the runtime is
diptest-free. Inputs are kept modest (<= a few thousand) so the pure-Python
reference stays fast under xdist.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core._dip import hartigan_dip

diptest = pytest.importorskip("diptest")
import numpy as np  # noqa: E402  (only needed once diptest is present)


def _cases() -> list[list[float]]:
    rng = random.Random(20260729)
    npr = np.random.default_rng(20260729)
    cases: list[list[float]] = []
    # Structured shapes.
    for n in (2, 3, 4, 5, 10, 50, 200, 1000):
        cases.append(list(npr.random(n)))
    for n in (20, 100, 500):  # bimodal
        cases.append(list(np.concatenate([npr.normal(0, 1, n // 2), npr.normal(6, 1, n - n // 2)])))
    for n in (30, 300):  # unimodal
        cases.append(list(npr.normal(0, 1, n)))
    for n in (10, 50, 200):  # heavy integer ties
        cases.append(list(npr.integers(0, 5, n).astype(float)))
    for n in (50, 300):  # score-like [0,1] clusters near 0 and 1
        cases.append(list(np.concatenate([npr.beta(2, 8, n // 2), npr.beta(8, 2, n - n // 2)])))
    # Random small (the bulk of the coverage).
    for _ in range(400):
        n = rng.randint(2, 60)
        cases.append([rng.random() for _ in range(n)])
    # Two-unique-value samples (exercise the tie/degenerate branches).
    for _ in range(60):
        n = rng.randint(3, 40)
        a, b = rng.random(), rng.random()
        cases.append([rng.choice((a, b)) for _ in range(n)])
    return cases


@pytest.mark.parametrize("scores", _cases(), ids=lambda s: f"n{len(s)}")
def test_owned_dip_matches_diptest_exactly(scores: list[float]) -> None:
    ref = float(diptest.dipstat(np.asarray(scores, dtype=float)))
    assert hartigan_dip(scores) == ref


@pytest.mark.parametrize(
    "scores,expected",
    [([], 0.0), ([0.5], 0.0), ([1.0, 1.0, 1.0], 0.0), ([0.3, 0.3], 0.0)],
)
def test_owned_dip_degenerate_contract(scores: list[float], expected: float) -> None:
    # Empty / single / all-equal -> 0.0 (matches the prior diptest-backed path).
    assert hartigan_dip(scores) == expected


def test_owned_dip_range() -> None:
    rng = random.Random(1)
    for _ in range(50):
        n = rng.randint(2, 100)
        d = hartigan_dip([rng.random() for _ in range(n)])
        assert 0.0 <= d <= 0.25
