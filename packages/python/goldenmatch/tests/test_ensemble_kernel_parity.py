"""Field-level parity for the opt-in ensemble bucket kernel.

The historical ensemble decline (`backends/score_buckets._resolve_score_pair_callable`)
said: "do NOT reintroduce a per-pair ensemble without a field-level parity test
against find_fuzzy_matches." This is that test.

`_ensemble_score_single_f32` quantizes the per-pair ensemble score to float32,
which is BYTE-IDENTICAL to the matrix path's per-field float32 storage because
the float32 cast is monotonic and therefore commutes with `max`:
``float32(max(jw, ts, sx)) == max(float32(jw), float32(ts), float32(sx))`` — the
latter being exactly what `_fuzzy_score_matrix('ensemble')` computes
(`np.maximum` over three `astype(np.float32)` component matrices). So the opt-in
`GOLDENMATCH_ENSEMBLE_KERNEL` (safe mode) cannot change any `>= threshold`
decision the matrix path makes.

The float64 variant (`_ensemble_score_single`) is the measurement A/B twin: it
diverges from the matrix by <= ~3e-8 (float32 epsilon near 1.0). We assert that
bound too, so a regression that made it diverge *more* (a real reimpl bug, the
kind that caused the old 0.922->0.782 Febrl3 drop) fails loudly.

See docs/superpowers/specs/2026-07-21-ensemble-kernel-measurement.md.
"""
from __future__ import annotations

import numpy as np
from goldenmatch.core.scorer import (
    _ensemble_score_single,
    _ensemble_score_single_f32,
    _fuzzy_score_matrix,
)

# Adversarial corpus: names/addresses that exercise all three ensemble
# components (jaro_winkler, token_sort, soundex) incl. token reorderings,
# case, punctuation, soundex-equal-but-spelled-differently, empties.
_CORPUS = [
    "John Smith", "Smith John", "john  smith", "Jon Smith", "J. Smith",
    "Robert", "Rupert", "Rubin", "Bob", "Bobby",
    "12 Main Street", "Main Street 12", "12 Main St", "Main St",
    "Catherine", "Katherine", "Kathryn", "Cathy",
    "Ashcraft", "Ashcroft", "", "a", "Z", "O'Brien", "OBrien",
    "New York Mets", "Mets New York", "cafe", "café",
    "Elizabeth", "Elisabeth", "Beth", "Liz",
]


def test_f32_kernel_is_byte_identical_to_matrix() -> None:
    """The safe (float32) per-pair kernel == the matrix path, exactly."""
    matrix = _fuzzy_score_matrix(_CORPUS, "ensemble")  # float32 NxN
    n = len(_CORPUS)
    mismatches = 0
    for i in range(n):
        for j in range(i + 1, n):
            per_pair = _ensemble_score_single_f32(_CORPUS[i], _CORPUS[j])
            # matrix stores float32; compare the exact float32 bit patterns.
            if np.float32(per_pair) != matrix[i][j]:
                mismatches += 1
    assert mismatches == 0, f"{mismatches} field-level mismatches vs the matrix"


def test_f64_variant_stays_within_float32_epsilon() -> None:
    """The float64 A/B twin may differ from the matrix, but only by ~f32 ULP.

    A real reimpl bug (wrong token_sort variant, wrong soundex bonus, missing
    /100 scaling) would blow this bound wide open — the failure mode behind the
    old Febrl3 recall regression. 1e-6 is ~30x the observed 2.98e-8 ceiling and
    still ~1e5x below any threshold that could flip a decision.
    """
    matrix = _fuzzy_score_matrix(_CORPUS, "ensemble")
    n = len(_CORPUS)
    max_diff = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            f64 = _ensemble_score_single(_CORPUS[i], _CORPUS[j])
            max_diff = max(max_diff, abs(f64 - float(matrix[i][j])))
    assert max_diff < 1e-6, f"float64 ensemble diverged from matrix by {max_diff:.2e}"


def test_ensemble_is_max_of_components() -> None:
    """Sanity: identical strings -> 1.0; soundex-equal floors at 0.8."""
    assert _ensemble_score_single("Robert", "Robert") == 1.0
    assert _ensemble_score_single_f32("Robert", "Robert") == 1.0
    # Robert/Rupert share soundex R163 -> >= 0.8 even with lowish jw/token_sort.
    assert _ensemble_score_single("Robert", "Rupert") >= 0.8
