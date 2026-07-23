"""Phase 2 of FS domain comparators (spec: docs/superpowers/specs/2026-07-23-
fs-domain-comparators-design.md): the ``numeric_diff`` and ``geo_haversine``
comparators.

Both map a DOMAIN distance (numeric magnitude / great-circle km) to a monotone
graded similarity in [0,1], so string similarity on numbers or coordinates (which
is meaningless) is replaced by the real signal. Like ``date_diff`` they are just
new scorers: they flow through the SAME score_field / _fuzzy_score_matrix routing
and the SAME level machinery, so they cannot affect blocking, the pair set,
memory, EM shape, or clustering (scale-neutral by construction, native-declined
-> numpy path like ``soundex_match``).

Parity discipline: the vectorized NxN matrix calls the SAME scalar function in an
O(n^2) loop, so scalar == vectorized by construction. On unparseable input both
fall back to exact-string equality (never None for non-null input), so the
missing-level decision is unchanged.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.core import scorer as S
from goldenmatch.core.scorer import (
    _geo_haversine_similarity_py,
    _numeric_diff_similarity_py,
    _parse_float,
    _parse_latlong,
    score_field,
)

# =========================== numeric_diff ===================================

def test_numeric_parse_rejects_non_finite():
    assert _parse_float("1.5") == 1.5
    for bad in (None, "", "abc", "nan", "inf", "-inf"):
        assert _parse_float(bad) is None


def test_numeric_string_similarity_is_meaningless_lever():
    # The whole point: "100" vs "900" are very different amounts but string-close.
    from goldenmatch.core.scorer import Levenshtein
    ed = Levenshtein.normalized_similarity("100", "900")
    nd = _numeric_diff_similarity_py("100", "900", "numeric_diff:abs:100")
    assert ed >= 0.6           # documents the string-blindness
    assert nd == 0.0           # 800 apart, band 100 -> disagree


def test_numeric_abs_band_is_a_monotone_ramp():
    s = "numeric_diff:abs:10"
    same = _numeric_diff_similarity_py("100", "100", s)   # 0
    near = _numeric_diff_similarity_py("100", "102", s)   # 2
    edge = _numeric_diff_similarity_py("100", "105", s)   # 5 (half band)
    far = _numeric_diff_similarity_py("100", "120", s)    # 20 (beyond band)
    assert same == 1.0
    assert same > near > edge > far
    assert far == 0.0
    assert edge == pytest.approx(0.5)


def test_numeric_pct_band_is_scale_relative():
    s = "numeric_diff:pct:0.1"  # within 10% relative (denom = max(|a|,|b|))
    # 1000 vs 1050 -> 50/1050 ~4.8% apart -> graded partial; 1000 vs 1200 = 20% -> disagree
    graded = _numeric_diff_similarity_py("1000", "1050", s)
    assert 0.0 < graded < 1.0
    assert _numeric_diff_similarity_py("1000", "1200", s) == 0.0
    # scale-relative: the SAME 5%-ish gap at a different magnitude scores the same
    assert _numeric_diff_similarity_py("10", "10.5", s) == pytest.approx(graded)


def test_numeric_bare_form_defaults_to_pct_10():
    # bare `numeric_diff` == `numeric_diff:pct:0.1`
    assert _numeric_diff_similarity_py("1000", "1050", "numeric_diff") == \
        _numeric_diff_similarity_py("1000", "1050", "numeric_diff:pct:0.1")


def test_numeric_unparseable_falls_back_to_exact():
    # non-numeric -> exact-string equality, never None
    assert _numeric_diff_similarity_py("N/A", "N/A", "numeric_diff") == 1.0
    assert _numeric_diff_similarity_py("N/A", "x", "numeric_diff") == 0.0


def test_numeric_registered_and_routed():
    from goldenmatch.config.schemas import VALID_SCORERS
    assert "numeric_diff" in VALID_SCORERS
    assert score_field("100", "100", "numeric_diff:abs:5") == 1.0
    assert score_field(None, "100", "numeric_diff") is None
    assert score_field("100", None, "numeric_diff:pct:0.1") is None


def test_numeric_suffix_form_validates_on_matchkey_field():
    from goldenmatch.config.schemas import MatchkeyField
    f = MatchkeyField(field="amount", scorer="numeric_diff:pct:0.05", levels=3,
                      partial_threshold=0.6)
    assert f.scorer == "numeric_diff:pct:0.05"
    with pytest.raises(ValueError):
        MatchkeyField(field="amount", scorer="numeric_diff:xyz:5", levels=3,
                      partial_threshold=0.6)


# =========================== geo_haversine ==================================

def test_geo_parse_rejects_out_of_range_and_garbage():
    assert _parse_latlong("40.7128,-74.0060") == (40.7128, -74.0060)
    assert _parse_latlong("40.7128;-74.0060") == (40.7128, -74.0060)
    for bad in (None, "", "notgeo", "40.7", "91.0,0.0", "0.0,181.0", "a,b"):
        assert _parse_latlong(bad) is None


def test_geo_bands_strictly_non_increasing():
    origin = "40.0000,-74.0000"
    same = _geo_haversine_similarity_py(origin, origin)               # 0 km
    close = _geo_haversine_similarity_py(origin, "40.0005,-74.0000")  # ~55 m
    near = _geo_haversine_similarity_py(origin, "40.0050,-74.0000")   # ~0.55 km
    mid = _geo_haversine_similarity_py(origin, "40.0500,-74.0000")    # ~5.5 km
    far = _geo_haversine_similarity_py(origin, "41.0000,-74.0000")    # ~111 km
    assert same == 1.0
    assert same >= close > near > mid > far
    assert far == 0.0


def test_geo_unparseable_falls_back_to_exact():
    assert _geo_haversine_similarity_py("unknown", "unknown") == 1.0
    assert _geo_haversine_similarity_py("unknown", "x") == 0.0


def test_geo_registered_and_routed():
    from goldenmatch.config.schemas import VALID_SCORERS
    assert "geo_haversine" in VALID_SCORERS
    assert score_field("40.0,-74.0", "40.0,-74.0", "geo_haversine") == 1.0
    assert score_field(None, "40.0,-74.0", "geo_haversine") is None


def test_geo_validates_on_matchkey_field():
    from goldenmatch.config.schemas import MatchkeyField
    f = MatchkeyField(field="coordinates", scorer="geo_haversine", levels=3,
                      partial_threshold=0.6)
    assert f.scorer == "geo_haversine"


# ================= vectorized matrix == scalar (parity) =====================

_NUM_VALS = ["100", "102", "105", "120", None, "abc"]
_GEO_VALS = ["40.0,-74.0", "40.0005,-74.0", "41.0,-74.0", None, "bad"]


@pytest.mark.parametrize("scorer,vals", [
    ("numeric_diff:abs:10", _NUM_VALS),
    ("numeric_diff:pct:0.1", _NUM_VALS),
    ("geo_haversine", _GEO_VALS),
])
def test_fuzzy_score_matrix_matches_scalar(scorer, vals):
    m = np.asarray(S._fuzzy_score_matrix(vals, scorer), dtype=np.float64)
    clean = [v if v is not None else "" for v in vals]
    n = len(clean)
    for i in range(n):
        for j in range(i + 1, n):
            if scorer.startswith("numeric_diff"):
                expect = _numeric_diff_similarity_py(clean[i], clean[j], scorer)
            else:
                expect = _geo_haversine_similarity_py(clean[i], clean[j])
            assert m[i, j] == pytest.approx(expect, abs=1e-6)
            assert m[j, i] == pytest.approx(expect, abs=1e-6)


@pytest.mark.parametrize("scorer,vals", [
    ("numeric_diff:abs:10", _NUM_VALS),
    ("geo_haversine", _GEO_VALS),
])
def test_field_score_matrix_dedup_matches_direct(scorer, vals):
    from goldenmatch.core.probabilistic import (
        _field_score_matrix,
        _field_score_matrix_dedup,
    )
    direct = np.asarray(_field_score_matrix(vals, scorer), dtype=np.float64)
    dedup = np.asarray(_field_score_matrix_dedup(vals, scorer), dtype=np.float64)
    n = len(vals)
    for i in range(n):
        for j in range(i + 1, n):
            assert direct[i, j] == pytest.approx(dedup[i, j], abs=1e-6)


@pytest.mark.parametrize("scorer,vals", [
    ("numeric_diff:pct:0.1", _NUM_VALS),
    ("geo_haversine", _GEO_VALS),
])
def test_deterministic(scorer, vals):
    a = np.asarray(S._fuzzy_score_matrix(vals, scorer))
    b = np.asarray(S._fuzzy_score_matrix(vals, scorer))
    assert np.array_equal(a, b)
