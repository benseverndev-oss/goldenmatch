"""Quality-gated review routing (GoldenCheck -> GoldenMatch doors #5/#6).

A confident-score pair built on GoldenCheck-flagged cells is downgraded from
auto-merge to review (downgrade-only, fail-open, opt-in).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.quality import _goldencheck_available, row_quality_floor
from goldenmatch.core.review_queue import gate_pairs

# --- gate_pairs downgrade (pure; no goldencheck needed) ---------------------

def test_low_quality_pair_downgraded_to_review() -> None:
    pairs = [(1, 2, 0.98)]                      # would auto-merge
    rq = {2: 0.6}                               # row 2 is low quality
    reasons: dict = {}
    auto, review, rejected = gate_pairs(pairs, row_quality=rq, quality_floor=0.7, reasons=reasons)
    assert auto == []
    assert review == [(1, 2, 0.98)]             # held for review
    assert (1, 2) in reasons                    # door #6: provenance recorded


def test_clean_high_score_still_auto_merges() -> None:
    auto, review, _ = gate_pairs([(1, 2, 0.98)], row_quality={2: 0.95}, quality_floor=0.7)
    assert auto == [(1, 2, 0.98)]               # quality above floor -> unchanged
    assert review == []


def test_review_and_reject_bands_untouched() -> None:
    # row_quality must NEVER upgrade a review/reject (downgrade-only).
    pairs = [(1, 2, 0.80), (3, 4, 0.50)]
    rq = {2: 0.1, 4: 0.1}
    auto, review, rejected = gate_pairs(pairs, row_quality=rq, quality_floor=0.7)
    assert auto == []
    assert review == [(1, 2, 0.80)]
    assert rejected == [(3, 4, 0.50)]


def test_no_row_quality_is_byte_identical() -> None:
    pairs = [(1, 2, 0.98), (3, 4, 0.80), (5, 6, 0.50)]
    assert gate_pairs(pairs) == gate_pairs(pairs, row_quality=None)
    auto, review, rejected = gate_pairs(pairs)
    assert auto == [(1, 2, 0.98)] and review == [(3, 4, 0.80)] and rejected == [(5, 6, 0.50)]


def test_missing_row_in_quality_map_treated_as_clean() -> None:
    # row 2 not in the (sparse) map -> defaults to 1.0 -> auto-merges.
    auto, review, _ = gate_pairs([(1, 2, 0.98)], row_quality={99: 0.1}, quality_floor=0.7)
    assert auto == [(1, 2, 0.98)]


# --- row_quality_floor bridge -----------------------------------------------

needs_gc = pytest.mark.skipif(not _goldencheck_available(), reason="goldencheck not installed")


@needs_gc
def test_row_quality_floor_flags_fuzzy_rows() -> None:
    states = ["California"] * 40 + ["Californa"] * 4 + ["Texas"] * 46
    df = pl.DataFrame({"__row_id__": list(range(len(states))), "state": states})
    floor = row_quality_floor(df)
    assert floor is not None
    # the 4 'Californa' rows (__row_id__ 40..43) are penalized; clean rows absent.
    assert all(0 < floor[r] < 1.0 for r in (40, 41, 42, 43))
    assert 0 not in floor


@needs_gc
def test_row_quality_floor_clean_is_none() -> None:
    df = pl.DataFrame({"__row_id__": list(range(90)), "name": ["a", "b", "c"] * 30})
    assert row_quality_floor(df) is None


@needs_gc
def test_row_quality_floor_no_row_id_is_none() -> None:
    states = ["California"] * 40 + ["Californa"] * 4 + ["Texas"] * 46
    df = pl.DataFrame({"state": states})  # no __row_id__
    assert row_quality_floor(df) is None
