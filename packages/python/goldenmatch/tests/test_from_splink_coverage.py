"""Coverage scorecard on the Splink conversion (SplinkConversion.coverage).

A one-line summary of how faithful the conversion was: comparisons + blocking
rules converted, how many fields are approximate, how many were dropped.
"""
from __future__ import annotations

from goldenmatch.config.from_splink import CoverageSummary, from_splink


def _null(col: str) -> dict:
    return {"sql_condition": f'"{col}_l" IS NULL OR "{col}_r" IS NULL', "is_null_level": True}


def _comp_exact(col: str) -> dict:
    return {
        "output_column_name": col,
        "comparison_levels": [
            _null(col),
            {"sql_condition": f'"{col}_l" = "{col}_r"'},
            {"sql_condition": "ELSE"},
        ],
    }


def _comp_levenshtein(col: str) -> dict:
    # levenshtein -> an APPROXIMATE mapping (distance -> similarity snap)
    return {
        "output_column_name": col,
        "comparison_levels": [
            _null(col),
            {"sql_condition": f'levenshtein("{col}_l", "{col}_r") <= 1'},
            {"sql_condition": "ELSE"},
        ],
    }


def _comp_arbitrary(col: str) -> dict:
    # unrecognized SQL on every non-null level -> comparison dropped
    return {
        "output_column_name": col,
        "comparison_levels": [
            _null(col),
            {"sql_condition": f'weird_udf("{col}_l", "{col}_r") > 3'},
            {"sql_condition": "ELSE"},
        ],
    }


def test_full_coverage_all_exact() -> None:
    settings = {
        "comparisons": [_comp_exact("first_name"), _comp_exact("surname")],
        "blocking_rules_to_generate_predictions": ["l.surname = r.surname"],
    }
    cov = from_splink(settings).coverage
    assert isinstance(cov, CoverageSummary)
    assert cov.total_comparisons == 2
    assert cov.converted_comparisons == 2
    assert cov.approximate_fields == 0
    assert cov.total_blocking_rules == 1
    assert cov.converted_blocking_rules == 1
    assert cov.is_complete
    assert "2/2 comparisons (2 exact, 0 approximate)" in cov.line()
    assert "100% coverage" in cov.line()


def test_approximate_field_counted() -> None:
    settings = {
        "comparisons": [_comp_exact("first_name"), _comp_levenshtein("dob")],
        "blocking_rules_to_generate_predictions": ["l.surname = r.surname"],
    }
    cov = from_splink(settings).coverage
    assert cov.converted_comparisons == 2
    assert cov.approximate_fields == 1  # the levenshtein field
    assert cov.is_complete  # nothing dropped
    assert "(1 exact, 1 approximate)" in cov.line()


def test_dropped_comparison_lowers_coverage() -> None:
    settings = {
        "comparisons": [_comp_exact("first_name"), _comp_arbitrary("junk")],
        "blocking_rules_to_generate_predictions": ["l.surname = r.surname"],
    }
    cov = from_splink(settings).coverage
    assert cov.total_comparisons == 2
    assert cov.converted_comparisons == 1
    assert cov.dropped_comparisons == 1
    assert not cov.is_complete
    assert "1 comparison(s) dropped" in cov.line()
    assert "partial coverage" in cov.line()


def test_dropped_blocking_rule_counted() -> None:
    settings = {
        "comparisons": [_comp_exact("first_name")],
        # second rule is a cross-column / unrecognized shape -> dropped
        "blocking_rules_to_generate_predictions": [
            "l.surname = r.surname",
            "l.a > r.b",
        ],
    }
    cov = from_splink(settings).coverage
    assert cov.total_blocking_rules == 2
    assert cov.converted_blocking_rules == 1
    assert cov.dropped_blocking_rules == 1
    assert not cov.is_complete
    assert "1 blocking rule(s) dropped" in cov.line()


def test_coverage_properties_are_derived() -> None:
    cov = CoverageSummary(
        total_comparisons=5,
        converted_comparisons=4,
        approximate_fields=2,
        total_blocking_rules=3,
        converted_blocking_rules=3,
    )
    assert cov.dropped_comparisons == 1
    assert cov.dropped_blocking_rules == 0
    assert not cov.is_complete  # a dropped comparison
