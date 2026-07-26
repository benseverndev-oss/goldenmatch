"""Splink numeric-magnitude comparison conversion -> `numeric_diff` scorer.

Splink's standard library has NO first-class numeric comparison; numeric
magnitude arrives via a `CustomComparison` whose canonical shape -- captured
live from splink 4, BYTE-IDENTICAL in DuckDB + Spark -- is
`ABS("c_l" - "c_r") <= <eps>`. The conversion maps that hard cutoff to
GoldenMatch's `numeric_diff:abs:<band>` LINEAR RAMP with band = 2*eps, so a pair
exactly at the cutoff scores 0.5; under the `>=` level semantics
"score >= 0.5 <=> dist <= eps" reproduces `<= eps` exactly (boundary inclusive),
mirroring date_diff's "threshold = the score a pair at the cutoff earns". The
mapping is deterministic per level, so build-time and m/u-import-time recognition
agree (parity is automatic).

Ground truth (captured, both dialects identical):
    ABS("amount_l" - "amount_r") <= 1
"""
import pytest
from goldenmatch.config.from_splink import (
    ConversionReport,
    convert_comparison,
    import_em,
    recognize_level,
)
from goldenmatch.config.schemas import _is_valid_scorer
from goldenmatch.core.scorer import score_field

# --- recognizer --------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,expected_scorer",
    [
        ('ABS("amount_l" - "amount_r") <= 1', "numeric_diff:abs:2"),       # DuckDB quoted
        ("ABS(`amount_l` - `amount_r`) <= 10", "numeric_diff:abs:20"),      # Spark backtick
        ("abs(amount_l - amount_r) <= 0.5", "numeric_diff:abs:1"),          # lowercase, bare
        ('ABS("amount_l" - "amount_r") <= 2.5', "numeric_diff:abs:5"),      # float eps
        ('ABS( "amount_l"  -  "amount_r" )  <=  3', "numeric_diff:abs:6"),  # loose whitespace
    ],
)
def test_numeric_diff_recognized_both_dialects(sql, expected_scorer):
    r = recognize_level(sql)
    assert r is not None
    assert r.kind == "numeric_diff"
    assert r.column == "amount"
    assert r.scorer == expected_scorer          # band = 2*eps
    assert r.sim_threshold == 0.5               # a pair AT the cutoff scores 0.5
    assert r.approx is True


def test_numeric_diff_mismatched_columns_dropped():
    # Different base columns on the two sides -> not a same-column comparison.
    assert recognize_level('ABS("amount_l" - "balance_r") <= 1') is None


def test_numeric_diff_zero_or_negative_eps_dropped():
    assert recognize_level('ABS("amount_l" - "amount_r") <= 0') is None


def test_numeric_diff_relative_pct_shape_not_recognized():
    # Only the ABSOLUTE form is recognized; a relative/pct CustomComparison is too
    # shape-variable to match and must fall through (dropped + warned elsewhere).
    assert recognize_level('ABS("amount_l" - "amount_r") / 2 <= 0.1') is None
    assert (
        recognize_level(
            'ABS("amount_l" - "amount_r") / GREATEST(ABS("amount_l"), '
            'ABS("amount_r")) <= 0.1'
        )
        is None
    )


def test_numeric_diff_is_not_a_date_diff():
    # A bare ABS() numeric difference has no EPOCH/UNIX_TIMESTAMP date parse, so it
    # is numeric_diff, NOT date_diff (the two share the ABS(...) <= n tail).
    r = recognize_level('ABS("age_l" - "age_r") <= 5')
    assert r is not None and r.kind == "numeric_diff"


# --- full-comparison conversion ----------------------------------------------


def _numeric_comparison(cutoffs, col="amount"):
    """A CustomComparison numeric shape: null, exact, one ABS(diff) <= c level per
    cutoff, ELSE."""
    levels = [
        {
            "sql_condition": f'"{col}_l" IS NULL OR "{col}_r" IS NULL',
            "is_null_level": True,
        },
        {"sql_condition": f'"{col}_l" = "{col}_r"'},
    ]
    levels += [{"sql_condition": f'ABS("{col}_l" - "{col}_r") <= {c}'} for c in cutoffs]
    levels.append({"sql_condition": "ELSE"})
    return {"output_column_name": col, "comparison_levels": levels}


def test_single_cutoff_converts_to_numeric_diff_field():
    report = ConversionReport()
    field = convert_comparison(_numeric_comparison([1]), 0, report)

    assert field is not None
    assert field.field == "amount"
    assert field.scorer == "numeric_diff:abs:2"       # band = 2 * eps(1)
    # exact (1.0) + within-eps (0.5) -> 3 levels: exact / within-eps / else.
    assert field.levels == 3
    assert field.level_thresholds == [1.0, 0.5]


def test_single_cutoff_field_scorer_is_schema_valid():
    report = ConversionReport()
    field = convert_comparison(_numeric_comparison([2.5]), 0, report)
    assert field is not None
    assert _is_valid_scorer(field.scorer)             # numeric_diff:abs:5


def test_multi_cutoff_collapses_to_loosest_band_with_warn():
    report = ConversionReport()
    field = convert_comparison(_numeric_comparison([1, 10]), 0, report)

    assert field is not None
    # numeric_diff's single-band ramp can't hold two cutoffs; keep the LOOSEST
    # (band = 2 * 10) for recall, and warn that the finer cutoff collapses.
    assert field.scorer == "numeric_diff:abs:20"
    assert field.levels == 3
    assert field.level_thresholds == [1.0, 0.5]
    warns = [f.message for f in report.findings if f.severity == "warning"]
    assert any("collapsed to the loosest band" in w for w in warns)


def test_approximate_mapping_is_surfaced():
    report = ConversionReport()
    convert_comparison(_numeric_comparison([1]), 0, report)
    warns = [f.message for f in report.findings if f.severity == "warning"]
    assert any("numeric-distance cutoff" in w and "band = 2*eps" in w for w in warns)


# --- the crux invariant (the whole design rests on this) ---------------------


def test_pair_at_cutoff_scores_exactly_half():
    # With band = 2*eps, a pair exactly eps apart scores 1 - eps/(2*eps) = 0.5, so
    # the `>= 0.5` level fires iff dist <= eps -- reproducing Splink's `<= eps`.
    # scalar score_field IS the reference the vectorized path also calls.
    assert score_field("10", "11", "numeric_diff:abs:2") == 0.5      # dist 1 == eps
    assert score_field("10", "10.5", "numeric_diff:abs:2") == 0.75   # dist 0.5 < eps
    assert score_field("10", "10", "numeric_diff:abs:2") == 1.0      # exact
    assert score_field("10", "12", "numeric_diff:abs:2") == 0.0      # dist 2 == band


# --- trained model import (build == import parity) ---------------------------


def _trained_numeric_comparison():
    comp = _numeric_comparison([1])
    # levels: 0 null, 1 exact, 2 within-eps, 3 ELSE
    comp["comparison_levels"][1]["m_probability"] = 0.70
    comp["comparison_levels"][1]["u_probability"] = 0.02
    comp["comparison_levels"][2]["m_probability"] = 0.20
    comp["comparison_levels"][2]["u_probability"] = 0.10
    comp["comparison_levels"][3]["m_probability"] = 0.10
    comp["comparison_levels"][3]["u_probability"] = 0.88
    return comp


def test_trained_import_maps_every_numeric_level():
    comp = _trained_numeric_comparison()
    settings = {"comparisons": [comp], "probability_two_random_records_match": 0.0002}
    report = ConversionReport()

    field = convert_comparison(comp, 0, report)
    assert field is not None and field.levels == 3

    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    m = em.m_probs["amount"]
    assert len(m) == 3
    # index 2 = exact (top), 1 = within-eps, 0 = else; monotone for these m's.
    assert m[2] > m[1] > m[0]
    assert m == pytest.approx([0.10, 0.20, 0.70])
    # every level survived: no m/u mass dropped (build threshold == import
    # threshold because recognition is deterministic per level).
    warns = [f.message for f in report.findings if f.severity == "warning"]
    assert not any("does not match any converted threshold" in w for w in warns)
