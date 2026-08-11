"""R1 parity gate -- the native `score_field_pairwise` Arrow UDF backend must
equal the pure-Python rapidfuzz floor for the Sail tier scorers.

R1 of ``docs/superpowers/specs/2026-06-13-sail-tier-past-one-box-roadmap.md``:
the Sail scorer ships a pure-Python rapidfuzz `pandas_udf` FLOOR; benching it
measures Python-UDF overhead, not the engine. This test locks the native
backend (`goldenmatch.spark.scorers._native_scores`, via the score-core kernel)
to the floor, so the throughput win (proved in
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
from goldenmatch.spark import scorers  # noqa: E402

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
    """Native backend == the pure floor, within the stated tolerance.

    NOT bit-equality: the floor is goldenmatch.core.strsim (Python) and the
    kernel is score_one (Rust) -- two implementations of the same algorithm, so
    they agree to rounding rather than to the bit. Both are f64 as of the spec
    section 6 reversal; the tolerance stays because the implementations differ,
    not because the widths do."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = scorers._native_scores(scorer_name, _A, _B)
    assert native is not None, "native backend returned None under GOLDENMATCH_NATIVE=1"
    pure = np.asarray(scorers._pure_scores(scorer_name, _A, _B), dtype=np.float64)
    native = np.asarray(native, dtype=np.float64)
    assert native.shape == pure.shape
    # Two f64 implementations of one algorithm: epsilon, not bit-identical.
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


# ---------------------------------------------------------------------------
# P3 condition 2 (#2480 / spark-native-execution spec §6): DECISION stability
# ---------------------------------------------------------------------------
#
# The battery above proves SCORE equality within 1e-6. That is not the same as
# DECISION stability, and only the second is user-visible: what matters is
# whether a pair crosses a threshold and changes cluster membership, not whether
# its score moved in the 7th decimal.
#
# This test REVERSED spec §6 from option A (accept f32) to option B (f64
# kernel) on its first CI run, and is now option B's acceptance gate. The
# spec's reversal criterion is explicit: if membership moves at REALISTIC
# thresholds on realistic data, f32 buys throughput at the cost of
# reproducibility and option B (an f64 kernel) becomes correct instead.
#
# Realistic thresholds on purpose. Rigging a threshold to sit exactly on an
# observed score would manufacture a flip and prove nothing about shipped
# behaviour.

_REALISTIC_THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


def _boundary_corpus() -> tuple[list[str], list[str]]:
    """Name-shaped pairs spanning the whole similarity range, with many landing
    in the 0.7-0.95 band where thresholds actually sit."""
    first = ["Jonathan", "Jonathon", "Johnathan", "Jon", "Katherine", "Catherine",
             "Kathryn", "Stephen", "Steven", "Steffen", "Michelle", "Michele",
             "Rebecca", "Rebekah", "Geoffrey", "Jeffrey", "Elisabeth", "Elizabeth"]
    surn = ["Smith", "Smyth", "Smithe", "Anderson", "Andersen", "Andersson",
            "MacDonald", "McDonald", "Macdonald", "Thompson", "Thomson", "Tomson"]
    a: list[str] = []
    b: list[str] = []
    for i, x in enumerate(first):
        for y in first[i:]:
            a.append(x)
            b.append(y)
    for i, x in enumerate(surn):
        for y in surn[i:]:
            a.append(x)
            b.append(y)
    return a, b


@pytest.mark.parametrize("scorer_name", ["jaro_winkler", "levenshtein", "token_sort"])
def test_threshold_decisions_are_stable_between_native_and_pure(scorer_name, monkeypatch):
    """P3 condition 2: the SAME pairs clear each realistic threshold.

    A failure here is the spec's documented reversal trigger, not a flaky test.
    Do NOT relax it by widening a tolerance -- that converts a stated tolerance
    back into a silent one, which is the thing §6 exists to avoid.
    """
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    a, b = _boundary_corpus()

    pure = np.asarray(scorers._pure_scores(scorer_name, a, b), dtype=np.float64)
    native_raw = scorers._native_scores(scorer_name, a, b)
    assert native_raw is not None, "native backend returned None under GOLDENMATCH_NATIVE=1"
    native = np.asarray(native_raw, dtype=np.float64)

    for t in _REALISTIC_THRESHOLDS:
        pure_hits = set(np.flatnonzero(pure >= t).tolist())
        native_hits = set(np.flatnonzero(native >= t).tolist())
        if pure_hits != native_hits:
            flipped = sorted(pure_hits ^ native_hits)
            detail = [
                f"({a[i]!r},{b[i]!r}) pure={pure[i]:.9f} native={native[i]:.9f}"
                for i in flipped[:5]
            ]
            raise AssertionError(
                f"{scorer_name} @ threshold {t}: {len(flipped)} pair(s) changed "
                f"decision between the f32 native kernel and the f64 floor. "
                f"This is spec §6's reversal trigger -- consider option B (an f64 "
                f"kernel), do NOT widen a tolerance. Examples: {detail}"
            )


@pytest.mark.parametrize("scorer_name", ["jaro_winkler", "levenshtein", "token_sort"])
def test_boundary_margin_is_reported_not_assumed(scorer_name, monkeypatch):
    """How much headroom the f32 decision actually has.

    Decision stability above is necessary but says nothing about how CLOSE it
    came. If the nearest score to a realistic threshold is within the f32/f64
    delta, stability today is luck rather than margin, and that should be visible
    rather than inferred.
    """
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    a, b = _boundary_corpus()
    pure = np.asarray(scorers._pure_scores(scorer_name, a, b), dtype=np.float64)
    native = np.asarray(scorers._native_scores(scorer_name, a, b), dtype=np.float64)

    max_delta = float(np.max(np.abs(native - pure)))
    for t in _REALISTIC_THRESHOLDS:
        margin = float(np.min(np.abs(pure - t)))
        print(
            f"  {scorer_name} @ {t}: nearest score is {margin:.9f} away; "
            f"max |native-pure| = {max_delta:.9f}; "
            f"{'AT RISK' if margin <= max_delta else 'safe'}"
        )
    # The kernel must stay inside the tolerance §6 states.
    assert max_delta < 1e-6, f"{scorer_name}: max delta {max_delta} exceeds the stated 1e-6"
