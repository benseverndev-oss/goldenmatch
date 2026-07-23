"""Phase 1 of FS domain comparators (spec: docs/superpowers/specs/2026-07-23-
fs-domain-comparators-design.md): the ``date_diff`` comparator.

``date_diff`` maps a DAY-DISTANCE between two dates to a monotone graded
similarity in [0,1] -- so a 1-year DOB gap reads as a weak partial (the existing
edit-distance ``date`` scorer scores it 0.90, magnitude-blind). It is just a new
scorer: it flows through the SAME score_field / _fuzzy_score_matrix routing and
the SAME level machinery, so it cannot affect blocking, the pair set, memory, EM
shape, or clustering (scale-neutral by construction).

Parity discipline: the vectorized NxN matrix calls the SAME scalar function in an
O(n^2) loop (like the ``date`` scorer), so scalar == vectorized by construction.
On unparseable input ``date_diff`` reuses the typo-robust ``date`` scorer (never
returns None for non-null strings), so it is a strict improvement and never
diverges the missing-level decision.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.core import scorer as S
from goldenmatch.core.scorer import (
    _date_diff_similarity_py,
    _date_similarity_py,
    _parse_date_ordinal,
    score_field,
)

# ---- parse -----------------------------------------------------------------

def test_parse_equivalent_forms():
    iso = _parse_date_ordinal("1990-01-02")
    assert iso is not None
    assert _parse_date_ordinal("1990/01/02") == iso
    assert _parse_date_ordinal("19900102") == iso
    assert _parse_date_ordinal("1990-1-2") == iso


def test_parse_year_only():
    # NCVR birth_year shape: a bare year parses to Jan-1 of that year, so
    # same-year pairs collapse to distance 0 and adjacent years to ~365 days.
    assert _parse_date_ordinal("1985") == _parse_date_ordinal("1985-01-01")


@pytest.mark.parametrize("bad", [None, "", "not a date", "1990-13-40", "abcd-ef-gh"])
def test_parse_rejects(bad):
    assert _parse_date_ordinal(bad) is None


# ---- banded similarity -----------------------------------------------------

def test_same_day_is_one():
    assert _date_diff_similarity_py("1990-01-02", "1990-01-02") == 1.0


def test_bands_strictly_non_increasing():
    same = _date_diff_similarity_py("1990-06-15", "1990-06-15")   # 0 d
    one = _date_diff_similarity_py("1990-06-15", "1990-06-16")    # 1 d
    month = _date_diff_similarity_py("1990-06-15", "1990-07-10")  # ~25 d
    year = _date_diff_similarity_py("1990-06-15", "1991-06-15")   # 365 d
    far = _date_diff_similarity_py("1990-06-15", "2000-06-15")    # 10 y
    assert same > one > month > year > far
    assert far == 0.0


def test_year_gap_is_a_weak_partial_not_a_near_match():
    # The whole point: a full-year DOB gap must NOT read as agreement, unlike the
    # edit-distance ``date`` scorer which scores it 0.90 (one digit changed).
    dd = _date_diff_similarity_py("1990-01-02", "1991-01-02")
    ed = _date_similarity_py("1990-01-02", "1991-01-02")
    assert dd < 0.75
    assert ed >= 0.90  # documents the magnitude-blindness date_diff fixes


def test_mm_dd_transposition_is_floored_to_partial():
    # 1990-01-02 vs 1990-02-01 is a month/day swap (~30 d apart) -- a common
    # data-entry slip, so it is a partial, not a disagree.
    s = _date_diff_similarity_py("1990-01-02", "1990-02-01")
    assert s >= 0.80


def test_unparseable_falls_back_to_edit_distance_scorer():
    # Non-ISO / garbage -> reuse the typo-robust ``date`` scorer verbatim, so
    # date_diff is a strict improvement and never returns None for non-null input.
    a, b = "Jan 2 1990", "Jan 3 1990"
    assert _date_diff_similarity_py(a, b) == _date_similarity_py(a, b)


# ---- scorer registration + scalar branch -----------------------------------

def test_registered_in_valid_scorers():
    from goldenmatch.config.schemas import VALID_SCORERS
    assert "date_diff" in VALID_SCORERS


def test_score_field_routes_date_diff():
    assert score_field("1990-01-02", "1990-01-02", "date_diff") == 1.0
    assert score_field(None, "1990-01-02", "date_diff") is None
    assert score_field("1990-01-02", None, "date_diff") is None


def test_matchkey_field_accepts_date_diff():
    from goldenmatch.config.schemas import MatchkeyField
    f = MatchkeyField(field="dob", scorer="date_diff", levels=3, partial_threshold=0.6)
    assert f.scorer == "date_diff"


# ---- vectorized matrix == scalar (parity by construction) ------------------

_VALS = ["1990-01-02", "1990-01-02", "1991-01-02", "1990-02-01", None, "1985"]


def test_fuzzy_score_matrix_matches_scalar():
    m = np.asarray(S._fuzzy_score_matrix(_VALS, "date_diff"), dtype=np.float64)
    clean = [v if v is not None else "" for v in _VALS]
    n = len(clean)
    for i in range(n):
        for j in range(i + 1, n):
            expect = _date_diff_similarity_py(clean[i], clean[j])
            assert m[i, j] == pytest.approx(expect, abs=1e-6)
            assert m[j, i] == pytest.approx(expect, abs=1e-6)


def test_field_score_matrix_routes_date_diff():
    from goldenmatch.core.probabilistic import _field_score_matrix, _field_score_matrix_dedup
    direct = np.asarray(_field_score_matrix(_VALS, "date_diff"), dtype=np.float64)
    # dedup path is bit-identical off-diagonal (diagonal pinned to 1.0 for the
    # gather; compare only i<j cells, which is what FS scores via triu).
    dedup = np.asarray(_field_score_matrix_dedup(_VALS, "date_diff"), dtype=np.float64)
    n = len(_VALS)
    for i in range(n):
        for j in range(i + 1, n):
            assert direct[i, j] == pytest.approx(dedup[i, j], abs=1e-6)


def test_deterministic():
    # Pure function of the two values -> no N-dependence (the scale-invariance
    # guarantee at the unit level; qis_gate proves it end-to-end).
    a = np.asarray(S._fuzzy_score_matrix(_VALS, "date_diff"))
    b = np.asarray(S._fuzzy_score_matrix(_VALS, "date_diff"))
    assert np.array_equal(a, b)
