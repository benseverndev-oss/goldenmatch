"""R1 parity gate -- the native `score_field_pairwise` Arrow UDF backend must
equal the pure-Python rapidfuzz floor for the Sail tier scorers.

R1 of ``docs/superpowers/specs/2026-06-13-sail-tier-past-one-box-roadmap.md``:
the Sail scorer ships a pure-Python rapidfuzz `pandas_udf` FLOOR; benching it
measures Python-UDF overhead, not the engine. This test locks the native
backend (`goldenmatch.sail.scorers._native_scores`, via the score-core kernel)
to the floor at f32 epsilon, so the throughput win (proved in
`scripts/bench_sail_scorer_native.py`) is taken on a faithful number.

Gates on the native kernel, NOT the `sail` extra -- it exercises the scorer
backend directly (no Spark needed), so it runs in any lane where the native
wheel is built and skips elsewhere.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("rapidfuzz")
pytest.importorskip("pyarrow")

from goldenmatch.core._native_loader import native_module  # noqa: E402
from goldenmatch.sail import scorers  # noqa: E402

_HAS_KERNEL = (
    native_module() is not None
    and hasattr(native_module(), "score_field_pairwise")
)
pytestmark = pytest.mark.skipif(
    not _HAS_KERNEL,
    reason="native score_field_pairwise kernel not built (pure-Python-only env)",
)

# Diverse pair fixture: identical, disjoint, transposed tokens, case, empty,
# None, unicode, length-skew -- every shape the floor handles.
_A = ["Jonathan", "Jonathan", "alice smith", "ABC", "", None, "café", "x", "Smith"]
_B = ["Jonathan", "Jonothan", "smith alice", "abc", "", "x", "cafe", "xyzzy", None]


@pytest.mark.parametrize("scorer_name", scorers._SUPPORTED)
def test_native_matches_pure_floor(scorer_name, monkeypatch):
    """Native backend == pure rapidfuzz floor at f32 epsilon."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = scorers._native_scores(scorer_name, _A, _B)
    assert native is not None, "native backend returned None under GOLDENMATCH_NATIVE=1"
    pure = np.asarray(scorers._pure_scores(scorer_name, _A, _B), dtype=np.float64)
    native = np.asarray(native, dtype=np.float64)
    assert native.shape == pure.shape
    # f32 kernel vs f64 floor: epsilon, not bit-identical (the documented contract).
    assert np.max(np.abs(native - pure)) < 1e-6
    # Scores stay in range.
    assert native.min() >= 0.0 and native.max() <= 1.0


@pytest.mark.parametrize("scorer_name", scorers._SUPPORTED)
def test_score_batch_flag_routing(scorer_name, monkeypatch):
    """score_batch uses native under =1, the exact pure floor under =0."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    off = scorers.score_batch(scorer_name, _A, _B)
    # =0 must be the pure floor verbatim (list of f64, bit-identical).
    assert off == scorers._pure_scores(scorer_name, _A, _B)

    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    on = np.asarray(scorers.score_batch(scorer_name, _A, _B), dtype=np.float64)
    assert np.max(np.abs(on - np.asarray(off, dtype=np.float64))) < 1e-6


def test_identical_strings_score_one(monkeypatch):
    """Identical non-empty strings score exactly 1.0 on every scorer + backend."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    same_a = ["hello", "world", "12345"]
    for scorer_name in scorers._SUPPORTED:
        native = np.asarray(scorers._native_scores(scorer_name, same_a, same_a))
        assert np.allclose(native, 1.0, atol=1e-6)


def test_length_mismatch_is_caught(monkeypatch):
    """The kernel rejects unequal-length inputs (a real bug, not silent)."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    import pyarrow as pa

    native = native_module()
    with pytest.raises(Exception):
        native.score_field_pairwise(
            pa.array(["a", "b"], type=pa.large_string()),
            pa.array(["a"], type=pa.large_string()),
            0,
        )
