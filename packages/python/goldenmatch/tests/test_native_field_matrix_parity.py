"""Parity: native `score_field_matrix` vs the rapidfuzz / jellyfish slow path.

The native kernel is a low-level cdist-shaped primitive used by
`_fuzzy_score_matrix` and `_soundex_score_matrix` to drop the per-row Python
overhead at scale. These tests assert it agrees with the Python path
within float tolerance on a representative input mix (ASCII / Unicode /
null / empty), exercising every scorer ID the slow path currently routes.

The tests are skipped when the native module isn't built (pure-Python
install of goldenmatch); the fallback path is exercised by the rest of
the slow-path test surface unchanged.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.core import scorer
from goldenmatch.core.scorer import (
    _NATIVE_FIELD_SCORER_IDS,
    _native_field_matrix,
)

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "score_field_matrix"):
    pytest.skip("native module loaded but score_field_matrix not exposed", allow_module_level=True)


_VALUES = [
    "Alice Smith",
    "Alice  Smith",  # double space
    "alice smith",
    "ALICE SMITH",
    "Bob Jones",
    "bob jones",
    "Zelda Maximilian",
    "Zoë Café",  # non-ASCII
    "",  # empty
    "Alice Smyth",  # near-miss
]


def _python_fuzzy_matrix(values: list, scorer_name: str) -> np.ndarray:
    """Force the rapidfuzz path by temporarily hiding the native kernel."""
    sentinel = scorer._native_field_matrix
    scorer._native_field_matrix = lambda *_a, **_k: None  # type: ignore[assignment]
    try:
        return scorer._fuzzy_score_matrix(values, scorer_name)
    finally:
        scorer._native_field_matrix = sentinel  # type: ignore[assignment]


@pytest.mark.parametrize("scorer_name", ["jaro_winkler", "levenshtein", "token_sort"])
def test_native_field_matrix_matches_rapidfuzz(scorer_name: str):
    py_mat = _python_fuzzy_matrix(_VALUES, scorer_name)
    rs_mat = _native_field_matrix(_VALUES, scorer_name)
    assert rs_mat is not None, f"native kernel must handle {scorer_name!r}"
    assert rs_mat.shape == py_mat.shape
    # 1e-4 absolute: rapidfuzz returns f64, kernel returns f32, and
    # token_sort_ratio's /100.0 rescale introduces another rounding step.
    np.testing.assert_allclose(rs_mat, py_mat, atol=1e-4)


def _offdiag(m: np.ndarray) -> np.ndarray:
    """Zero the diagonal so parity is asserted only on cells the pipeline reads.

    The exact/soundex match matrices diverge ONLY on the diagonal: the native
    kernel reports self-match = 1.0, while Python's `_exact_score_matrix` only
    sets 1.0 for hash-groups of size > 1, leaving a singleton value's diagonal
    at 0.0. That divergence is a documented don't-care -- every consumer
    extracts pairs via `np.triu(combined, k=1)` (scorer.py), which discards the
    diagonal before any pair is emitted. Compare the off-diagonal contract.
    """
    out = m.astype(np.float64).copy()
    np.fill_diagonal(out, 0.0)
    return out


def test_native_soundex_matches_jellyfish():
    py_mat = scorer._exact_score_matrix(
        [scorer.jellyfish.soundex(v) if v else None for v in _VALUES]
    )
    rs_mat = _native_field_matrix(_VALUES, "soundex_match")
    assert rs_mat is not None
    assert rs_mat.shape == py_mat.shape
    # soundex is binary 0/1; no tolerance band needed. Diagonal excluded
    # (self-match don't-care; see _offdiag).
    np.testing.assert_array_equal(_offdiag(rs_mat), _offdiag(py_mat))


def test_native_exact_matches_hash_path():
    py_mat = scorer._exact_score_matrix(_VALUES)
    rs_mat = _native_field_matrix(_VALUES, "exact")
    assert rs_mat is not None
    np.testing.assert_array_equal(_offdiag(rs_mat), _offdiag(py_mat))


def test_symmetric_self_cdist():
    """All scorer IDs return a symmetric matrix on a self-cdist."""
    for name in _NATIVE_FIELD_SCORER_IDS:
        mat = _native_field_matrix(_VALUES, name)
        assert mat is not None
        np.testing.assert_allclose(mat, mat.T, atol=1e-6, err_msg=f"{name} not symmetric")


def test_diagonal_is_one_for_non_null():
    """Diagonal == 1.0 for every non-empty value across fuzzy scorers."""
    for name in ("jaro_winkler", "levenshtein", "token_sort", "exact", "soundex_match"):
        mat = _native_field_matrix(_VALUES, name)
        assert mat is not None
        for i, v in enumerate(_VALUES):
            if v:  # non-empty
                assert mat[i, i] == pytest.approx(1.0, abs=1e-6), (
                    f"{name}: diagonal at i={i} (v={v!r}) was {mat[i, i]}, expected 1.0"
                )


def test_unknown_scorer_returns_none():
    """Unsupported names fall through to None so the caller stays on rapidfuzz."""
    assert _native_field_matrix(_VALUES, "embedding") is None
    assert _native_field_matrix(_VALUES, "totally_made_up") is None
