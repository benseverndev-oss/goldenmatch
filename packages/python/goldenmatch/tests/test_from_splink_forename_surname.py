"""ForenameSurname converter recognizer (Splink -> GoldenMatch).

Splink's ``ForenameSurnameComparison`` is a COMPOUND cross-column comparator: its
levels AND two per-part conditions over separate forename + surname columns. There
is no per-part-AND scorer in GoldenMatch, so the converter synthesizes a single
combined ``forename__surname`` field (via ``derive_from`` + a space separator) and
scores it with ``token_sort`` -- word-order robust, so both-exact AND the
forename/surname transposition score 1.0, and each per-part fuzzy level maps to a
token_sort threshold. The two single-part levels (surname-only / forename-only)
are subsumed by the synthesized field and dropped.

SQL fixtures are the shapes captured live from splink 4
(``ForenameSurnameComparison('forename','surname')``) in both the DuckDB
(double-quoted) and Spark (backtick-quoted) dialects.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.from_splink import (
    from_splink,
    recognize_level,
)

# ── Captured splink 4 SQL (DuckDB dialect) ───────────────────────────────────
_NULL = (
    '("forename_l" IS NULL OR "forename_r" IS NULL) AND '
    '("surname_l" IS NULL OR "surname_r" IS NULL)'
)
_BOTH_EXACT = '("forename_l" = "forename_r") AND ("surname_l" = "surname_r")'
_TRANSPOSE = '"forename_l" = "surname_r" AND "forename_r" = "surname_l"'
_BOTH_JW92 = (
    '(jaro_winkler_similarity("forename_l", "forename_r") >= 0.92) AND '
    '(jaro_winkler_similarity("surname_l", "surname_r") >= 0.92)'
)
_BOTH_JW88 = (
    '(jaro_winkler_similarity("forename_l", "forename_r") >= 0.88) AND '
    '(jaro_winkler_similarity("surname_l", "surname_r") >= 0.88)'
)
_SURNAME_ONLY = '"surname_l" = "surname_r"'
_FORENAME_ONLY = '"forename_l" = "forename_r"'


def _level(sql: str, *, is_null: bool = False, m=None, u=None) -> dict:
    d: dict = {"sql_condition": sql}
    if is_null:
        d["is_null_level"] = True
    if m is not None:
        d["m_probability"] = m
    if u is not None:
        d["u_probability"] = u
    return d


def _fs_levels(*, trained: bool = False) -> list[dict]:
    """The eight ForenameSurname comparison levels (null .. ELSE)."""
    if trained:
        return [
            _level(_NULL, is_null=True),
            _level(_BOTH_EXACT, m=0.50, u=0.001),
            _level(_TRANSPOSE, m=0.02, u=0.001),
            _level(_BOTH_JW92, m=0.20, u=0.01),
            _level(_BOTH_JW88, m=0.10, u=0.02),
            _level(_SURNAME_ONLY, m=0.08, u=0.05),
            _level(_FORENAME_ONLY, m=0.05, u=0.05),
            _level("ELSE", m=0.05, u=0.878),
        ]
    return [
        _level(_NULL, is_null=True),
        _level(_BOTH_EXACT),
        _level(_TRANSPOSE),
        _level(_BOTH_JW92),
        _level(_BOTH_JW88),
        _level(_SURNAME_ONLY),
        _level(_FORENAME_ONLY),
        _level("ELSE"),
    ]


def _settings(*, trained: bool = False) -> dict:
    return {
        "comparisons": [
            {
                "output_column_name": "forename_surname",
                "comparison_levels": _fs_levels(trained=trained),
            }
        ],
        "blocking_rules_to_generate_predictions": ['l."surname" = r."surname"'],
    }


# ── recognize_level: the compound shapes ─────────────────────────────────────


def test_both_exact_recognized_as_token_sort_1() -> None:
    r = recognize_level(_BOTH_EXACT)
    assert r is not None
    assert r.kind == "token_sort"
    assert r.column == "forename__surname"
    assert r.sim_threshold == 1.0
    assert r.approx is False
    assert r.derive_from == ["forename", "surname"]
    assert r.derive_separator == " "


def test_transposition_recognized_as_token_sort_1() -> None:
    r = recognize_level(_TRANSPOSE)
    assert r is not None
    assert r.kind == "token_sort"
    assert r.column == "forename__surname"
    assert r.sim_threshold == 1.0
    assert r.approx is False  # token_sort is word-order robust -> exact, not approx
    assert r.derive_from == ["forename", "surname"]


@pytest.mark.parametrize("sql,thr", [(_BOTH_JW92, 0.92), (_BOTH_JW88, 0.88)])
def test_both_fuzzy_recognized_as_token_sort_threshold(sql: str, thr: float) -> None:
    r = recognize_level(sql)
    assert r is not None
    assert r.kind == "token_sort"
    assert r.column == "forename__surname"
    assert r.sim_threshold == thr
    assert r.approx is True  # per-part jaro_winkler -> token_sort is approximate
    assert r.derive_from == ["forename", "surname"]


def test_spark_backtick_dialect_recognized() -> None:
    sql = (
        "(jaro_winkler(`forename_l`, `forename_r`) >= 0.92) AND "
        "(jaro_winkler(`surname_l`, `surname_r`) >= 0.92)"
    )
    r = recognize_level(sql)
    assert r is not None
    assert r.kind == "token_sort"
    assert r.column == "forename__surname"
    assert r.sim_threshold == 0.92
    assert r.derive_from == ["forename", "surname"]


def test_single_part_levels_are_plain_exact() -> None:
    # A single-part level is NOT a compound shape -- it recognizes as a normal
    # single-column exact (subsumption happens later, in convert_comparison).
    assert recognize_level(_SURNAME_ONLY).kind == "exact"
    assert recognize_level(_SURNAME_ONLY).column == "surname"
    assert recognize_level(_FORENAME_ONLY).column == "forename"


def test_combined_column_name_is_deterministic_across_shapes() -> None:
    # All compound shapes must synthesize the SAME combined field name so the
    # levels align on one field (and m/u import aligns build vs import time).
    names = {
        recognize_level(sql).column
        for sql in (_BOTH_EXACT, _TRANSPOSE, _BOTH_JW92, _BOTH_JW88)
    }
    assert names == {"forename__surname"}


@pytest.mark.parametrize(
    "sql",
    [
        # single-column shapes must NOT be captured by the compound recognizer
        'jaro_winkler_similarity("name_l", "name_r") >= 0.9',
        '"name_l" = "name_r"',
        # a mixed exact+fuzzy AND is not a recognized ForenameSurname shape
        '("forename_l" = "forename_r") AND '
        '(jaro_winkler_similarity("surname_l", "surname_r") >= 0.9)',
        # three-conjunct AND is not the two-part shape
        '("a_l" = "a_r") AND ("b_l" = "b_r") AND ("c_l" = "c_r")',
        # same base column on both conjuncts -> only one source column
        '("forename_l" = "forename_r") AND ("forename_l" = "forename_r")',
    ],
)
def test_non_forename_surname_and_shapes_rejected(sql: str) -> None:
    r = recognize_level(sql)
    # Either None (dropped) or a non-token_sort single-column recognition, but
    # never a spurious synthesized combined field.
    if r is not None:
        assert not (r.kind == "token_sort" and r.derive_from is not None)


# ── convert_comparison: full bare conversion ─────────────────────────────────


def test_bare_conversion_builds_combined_token_sort_field() -> None:
    res = from_splink(_settings())
    fields = res.config.matchkeys[0].fields
    assert len(fields) == 1
    f = fields[0]
    assert f.field == "forename__surname"
    assert f.scorer == "token_sort"
    assert f.levels == 4
    assert f.level_thresholds == [1.0, 0.92, 0.88]
    assert f.derive_from == ["forename", "surname"]
    assert f.derive_separator == " "
    assert res.em_model is None


def test_single_part_levels_dropped_with_warning() -> None:
    res = from_splink(_settings())
    subsumed = [
        fnd
        for fnd in res.report.findings
        if fnd.severity == "warning" and "subsumed" in fnd.message
    ]
    # exactly the surname-only + forename-only levels are subsumed
    assert len(subsumed) == 2
    cols = {"surname", "forename"}
    assert all(any(c in fnd.message for c in cols) for fnd in subsumed)


def test_fuzzy_levels_flagged_approximate() -> None:
    res = from_splink(_settings())
    approx = [
        fnd
        for fnd in res.report.findings
        if fnd.severity == "warning" and "token_sort threshold" in fnd.message
    ]
    assert len(approx) == 2  # the two per-part jaro_winkler levels


def test_conversion_is_polars_free() -> None:
    # from_splink must not import polars (the package is arrow-native).
    import sys

    sys.modules.pop("polars", None)
    from_splink(_settings())
    assert "polars" not in sys.modules


# ── import_em: trained m/u round-trip ────────────────────────────────────────


def test_trained_import_round_trips_probabilities() -> None:
    res = from_splink(_settings(trained=True))
    em = res.em_model
    assert em is not None
    f = "forename__surname"
    assert f in em.m_probs
    m, u = em.m_probs[f], em.u_probs[f]
    assert len(m) == 4 and len(u) == 4
    # normalized
    assert abs(sum(m) - 1.0) < 1e-9
    assert abs(sum(u) - 1.0) < 1e-9
    # match weights strictly increase from disagree -> strongest agree
    assert em.match_weights[f] == sorted(em.match_weights[f])


def test_trained_import_collapses_exact_and_transposition() -> None:
    # both-exact (m=0.50) + transposition (m=0.02) both map to token_sort 1.0
    # (the strongest level) and their m mass is SUMMED.
    res = from_splink(_settings(trained=True))
    em = res.em_model
    f = "forename__surname"
    # surname-only(0.08) + forename-only(0.05) dropped; kept sum = 0.87
    # strongest level raw m = 0.50 + 0.02 = 0.52 -> 0.52/0.87
    assert em.m_probs[f][3] == pytest.approx(0.52 / 0.87, abs=1e-6)
    collapse = [
        fnd
        for fnd in res.report.findings
        if fnd.severity == "warning" and "collapsed onto" in fnd.message
    ]
    assert len(collapse) == 1


def test_trained_import_drops_single_part_mu() -> None:
    res = from_splink(_settings(trained=True))
    dropped = [
        fnd
        for fnd in res.report.findings
        if fnd.severity == "warning"
        and "subsumed" in fnd.message
        and "m/u dropped" in fnd.message
    ]
    assert len(dropped) == 2  # surname-only + forename-only m/u dropped
