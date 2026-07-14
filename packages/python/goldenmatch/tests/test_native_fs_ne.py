"""Direct-kernel tests for the score_block_pairs_fs negative-evidence kwargs.

Calls goldenmatch._native.score_block_pairs_fs directly (no Python scoring
path) to pin the NE firing rule ported from ``_ne_fired``
(core/probabilistic.py:466): fires iff BOTH values present AND non-empty AND
similarity STRICTLY below the threshold; contributes exactly 0 otherwise.
Skipped when the native extension isn't built.
"""

from __future__ import annotations

import pytest
from goldenmatch.core import _native_loader

pytestmark = pytest.mark.skipif(
    not _native_loader.native_available(),
    reason="goldenmatch._native not built",
)

# _NATIVE_FS_SCORER_IDS (core/probabilistic.py): jaro_winkler=0, levenshtein=1,
# token_sort=2, exact=3.
_EXACT = 3


def _base_args():
    """3-row single block; one regular exact field with identical values.

    Every pair gets the full agreement weight 2.0 (weights [[-2.0, 2.0]],
    levels [2], partials [0.5]). calibrated=False, prior_w=0.0, threshold=0.0
    (emit everything). min/max weights are hand-set NE-aware:
    min = -2.0 + -4.0 = -6.0, max = 2.0, range = 8.0.
    """
    row_ids = [1, 2, 3]
    field_values = [["same", "same", "same"]]
    scorer_ids = [_EXACT]
    levels = [2]
    partials = [0.5]
    weights = [[-2.0, 2.0]]
    return (
        row_ids,
        [3],
        field_values,
        scorer_ids,
        levels,
        partials,
        weights,
        False,
        0.0,
        -6.0,
        8.0,
        0.0,
        [],
    )


def test_kernel_exports_fs_supports_ne():
    mod = _native_loader.native_module()
    assert getattr(mod, "FS_SUPPORTS_NE", False) is True


def test_kernel_ne_fires_strictly_below_threshold():
    mod = _native_loader.native_module()
    # Rows 1/2 share the NE value (sim 1.0 >= 0.5 -> no fire); rows 1/3 and
    # 2/3 differ (sim 0.0 < 0.5 -> fires, adds -4.0).
    pairs = mod.score_block_pairs_fs(
        *_base_args(),
        ne_values=[["X", "X", "Y"]],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[0.5],
        ne_weights=[-4.0],
    )
    scores = {(a, b): s for a, b, s in pairs}
    # no-fire pair: (2.0 - (-6.0)) / 8.0 = 1.0
    assert scores[(1, 2)] == pytest.approx(1.0)
    # fired pairs: (2.0 - 4.0 - (-6.0)) / 8.0 = 0.5
    assert scores[(1, 3)] == pytest.approx(0.5)
    assert scores[(2, 3)] == pytest.approx(0.5)
    assert len(scores) == 3


def test_kernel_ne_null_and_empty_never_fire():
    mod = _native_loader.native_module()
    # None on one side (pairs with row 1) and "" on one side (pairs with
    # row 3) are inconclusive: NE must contribute exactly 0, so the result
    # matches a call WITHOUT the ne kwargs entirely.
    without_ne = mod.score_block_pairs_fs(*_base_args())
    with_ne = mod.score_block_pairs_fs(
        *_base_args(),
        ne_values=[[None, "X", ""]],
        ne_scorer_ids=[_EXACT],
        ne_thresholds=[0.5],
        ne_weights=[-4.0],
    )
    assert with_ne == without_ne


def test_kernel_ne_validation_errors():
    mod = _native_loader.native_module()

    # Partial kwarg group: ne_values without the other three.
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(*_base_args(), ne_values=[["X", "X", "Y"]])
    # Partial kwarg group: ne_weights without ne_values.
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(*_base_args(), ne_weights=[-4.0])

    # Mismatched lengths across the four (2 values vs 1 of the rest).
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(
            *_base_args(),
            ne_values=[["X", "X", "Y"], ["A", "B", "C"]],
            ne_scorer_ids=[_EXACT],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )

    # ne_values[k] row count != len(row_ids).
    with pytest.raises(ValueError, match="score_block_pairs_fs"):
        mod.score_block_pairs_fs(
            *_base_args(),
            ne_values=[["X", "X"]],
            ne_scorer_ids=[_EXACT],
            ne_thresholds=[0.5],
            ne_weights=[-4.0],
        )
