"""P1 of the Splink domain-comparator conversion work (spec: docs/superpowers/
specs/2026-07-25-splink-domain-comparator-conversion-design.md): the
``array_intersect`` FS domain comparator.

Splink's ``ArrayIntersectAtSizesComparison`` compares two array-valued fields by
intersection size. GoldenMatch's FS scoring path is string-only, so
``array_intersect`` operates on a DELIMITED-STRING representation ("a|b|c"): it
splits both sides into token SETS and returns a monotone set-overlap similarity
in [0,1] (Jaccard by default, overlap-coefficient via ``array_intersect:overlap``).

Like the other domain comparators (date_diff / numeric_diff / geo_haversine) it is
just a new scorer: it flows through the SAME score_field / _field_score_matrix
routing and the SAME level machinery, so it cannot affect blocking, the pair set,
memory, EM shape, or clustering (scale-neutral by construction). On empty/unparseable
input it falls back to exact-string equality (never None for non-null input), so the
vectorized NxN matrix (which calls the SAME scalar fn) equals the scalar path by
construction and the missing-level decision is unchanged.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.scorer import (
    _array_intersect_similarity_py,
    _parse_token_set,
    score_field,
)

# =========================== token-set parsing ==============================

def test_parse_token_set_delimiters_autodetect():
    # pipe preferred, then semicolon, then comma
    assert _parse_token_set("a|b|c") == frozenset({"a", "b", "c"})
    assert _parse_token_set("a;b;c") == frozenset({"a", "b", "c"})
    assert _parse_token_set("a,b,c") == frozenset({"a", "b", "c"})


def test_parse_token_set_strips_and_drops_empties():
    assert _parse_token_set(" a | b |") == frozenset({"a", "b"})
    assert _parse_token_set("") == frozenset()
    assert _parse_token_set("   ") == frozenset()


def test_parse_token_set_single_token_no_delim():
    assert _parse_token_set("solo") == frozenset({"solo"})


# =========================== jaccard (default) =============================

def test_jaccard_partial_overlap():
    # {a,b,c} vs {a,b,d} -> inter 2, union 4 -> 0.5
    assert _array_intersect_similarity_py("a|b|c", "a|b|d", "array_intersect") == 0.5


def test_jaccard_exact_arrays_is_one():
    assert _array_intersect_similarity_py("a|b|c", "c|b|a", "array_intersect") == 1.0


def test_jaccard_disjoint_is_zero():
    assert _array_intersect_similarity_py("a|b", "c|d", "array_intersect") == 0.0


def test_jaccard_is_monotone_in_overlap():
    base = "a|b|c|d"
    more = _array_intersect_similarity_py(base, "a|b|c|e", "array_intersect")   # inter 3
    less = _array_intersect_similarity_py(base, "a|x|y|z", "array_intersect")   # inter 1
    none = _array_intersect_similarity_py(base, "w|x|y|z", "array_intersect")   # inter 0
    assert more > less > none == 0.0


# =========================== overlap coefficient ==========================

def test_overlap_mode_uses_min_denominator():
    # {a,b,c} vs {a,b} -> inter 2; jaccard 2/3, overlap 2/min(3,2)=1.0
    j = _array_intersect_similarity_py("a|b|c", "a|b", "array_intersect")
    o = _array_intersect_similarity_py("a|b|c", "a|b", "array_intersect:overlap")
    assert j == pytest.approx(2 / 3)
    assert o == 1.0


# =========================== fallback / missing ===========================

def test_empty_side_falls_back_to_exact_never_none():
    # empty token set on either side -> exact-string equality, NEVER None
    assert _array_intersect_similarity_py("", "", "array_intersect") == 1.0
    assert _array_intersect_similarity_py("", "a|b", "array_intersect") == 0.0


def test_score_field_none_on_null():
    assert score_field(None, "a|b", "array_intersect") is None
    assert score_field("a|b", None, "array_intersect") is None


def test_score_field_routes_array_intersect():
    assert score_field("a|b|c", "a|b|d", "array_intersect") == 0.5
    assert score_field("a|b|c", "a|b", "array_intersect:overlap") == 1.0


# =========================== scalar == vectorized ==========================

def test_matrix_equals_scalar():
    from goldenmatch.core.scorer import _fuzzy_score_matrix

    vals = ["a|b|c", "a|b|d", "c|b|a", "x|y|z", "a|b"]
    for scorer in ("array_intersect", "array_intersect:overlap"):
        m = _fuzzy_score_matrix(vals, scorer)
        n = len(vals)
        assert m.shape == (n, n)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                expected = _array_intersect_similarity_py(vals[i], vals[j], scorer)
                assert m[i, j] == pytest.approx(expected), (i, j, scorer)


# =========================== schema validation ============================

def test_schema_accepts_array_intersect_forms():
    from goldenmatch.config.schemas import MatchkeyField

    MatchkeyField(field="skills", scorer="array_intersect", levels=2, partial_threshold=0.5)
    MatchkeyField(field="skills", scorer="array_intersect:overlap", levels=2, partial_threshold=0.5)
    MatchkeyField(field="skills", scorer="array_intersect:jaccard", levels=2, partial_threshold=0.5)


def test_schema_rejects_bad_array_intersect_form():
    from goldenmatch.config.schemas import MatchkeyField

    with pytest.raises(ValueError):
        MatchkeyField(field="skills", scorer="array_intersect:bogus", levels=2, partial_threshold=0.5)
