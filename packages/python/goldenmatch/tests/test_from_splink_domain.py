"""P2: Splink domain-comparator conversion -- date_diff recognizer +
domain-family-wins rule.

The SQL strings below are the REAL Splink 4 serializations (captured via
`cl.DateOfBirthComparison(...).get_comparison('duckdb'|'spark').as_dict()`),
not hand-written approximations -- see
docs/superpowers/plans/2026-07-25-splink-domain-comparator-conversion-plan.md
section 3. `AbsoluteTimeDifferenceAtThresholds` (bare) and
`DateOfBirthComparison` (with a `date(...)` wrapper on Spark + a leading
damerau_levenshtein level) are the two shapes that emit date-difference levels.
"""
import pytest
from goldenmatch.config.from_splink import (
    ConversionReport,
    convert_comparison,
    import_em,
    recognize_level,
)

# --- real ground-truth level SQL (dob column) --------------------------------

# DuckDB: EPOCH(try_strptime(...)) seconds; DObComparison cutoffs 1mo/1yr/10yr.
_DUCK_1MO = (
    'ABS(EPOCH(try_strptime("dob_l", \'%Y-%m-%d\')) - '
    'EPOCH(try_strptime("dob_r", \'%Y-%m-%d\'))) <= 2629800.0'
)
_DUCK_1YR = (
    'ABS(EPOCH(try_strptime("dob_l", \'%Y-%m-%d\')) - '
    'EPOCH(try_strptime("dob_r", \'%Y-%m-%d\'))) <= 31557600.0'
)
_DUCK_10YR = (
    'ABS(EPOCH(try_strptime("dob_l", \'%Y-%m-%d\')) - '
    'EPOCH(try_strptime("dob_r", \'%Y-%m-%d\'))) <= 315576000.0'
)
# Spark DateOfBirthComparison: date(try_to_timestamp(...)) wrapper.
_SPARK_DATE_1MO = (
    "ABS(UNIX_TIMESTAMP(date(try_to_timestamp(`dob_l`, 'yyyy-MM-dd'))) - "
    "UNIX_TIMESTAMP(date(try_to_timestamp(`dob_r`, 'yyyy-MM-dd')))) <= 2629800.0"
)
# Spark AbsoluteTimeDifferenceAtThresholds: no date() wrapper, integer seconds.
_SPARK_BARE_30D = (
    "ABS(UNIX_TIMESTAMP(try_to_timestamp(`dob_l`, '%Y-%m-%d')) - "
    "UNIX_TIMESTAMP(try_to_timestamp(`dob_r`, '%Y-%m-%d'))) <= 2592000"
)


@pytest.mark.parametrize(
    "sql,expected_band",
    [
        (_DUCK_1MO, 0.80),      # 2629800 s ~= 30.4 d -> <=31d band
        (_DUCK_1YR, 0.60),      # 31557600 s ~= 365.25 d -> <=366d band
        (_DUCK_10YR, 0.0),      # 315576000 s ~= 3652 d -> beyond 5y -> 0.0
        (_SPARK_DATE_1MO, 0.80),
        (_SPARK_BARE_30D, 0.80),  # 2592000 s == 30 d exactly
    ],
)
def test_date_diff_recognized_both_dialects(sql, expected_band):
    r = recognize_level(sql)
    assert r is not None
    assert r.kind == "date_diff"
    assert r.column == "dob"
    assert r.sim_threshold == pytest.approx(expected_band, abs=1e-9)
    assert r.approx is True


def test_non_date_abs_expression_not_recognized():
    # An ABS() numeric difference with no EPOCH/UNIX_TIMESTAMP date parse is
    # NOT a date_diff level -- must fall through to None (dropped + warned).
    assert recognize_level('ABS("age_l" - "age_r") <= 5') is None


def test_date_diff_mismatched_columns_dropped():
    sql = (
        'ABS(EPOCH(try_strptime("dob_l", \'%Y-%m-%d\')) - '
        'EPOCH(try_strptime("born_r", \'%Y-%m-%d\'))) <= 2629800.0'
    )
    assert recognize_level(sql) is None


# --- full-comparison conversion ----------------------------------------------


def _dob_comparison_duckdb():
    """Real DuckDB DateOfBirthComparison: null, exact, damerau_levenshtein,
    then three date-difference levels (1mo/1yr/10yr), ELSE."""
    return {
        "output_column_name": "dob",
        "comparison_levels": [
            {
                "sql_condition": (
                    'try_strptime("dob_l", \'%Y-%m-%d\') IS NULL OR '
                    'try_strptime("dob_r", \'%Y-%m-%d\') IS NULL'
                ),
                "is_null_level": True,
            },
            {"sql_condition": '"dob_l" = "dob_r"'},
            {"sql_condition": 'damerau_levenshtein("dob_l", "dob_r") <= 1'},
            {"sql_condition": _DUCK_1MO},
            {"sql_condition": _DUCK_1YR},
            {"sql_condition": _DUCK_10YR},
            {"sql_condition": "ELSE"},
        ],
    }


def test_dob_comparison_converts_to_date_diff_field():
    report = ConversionReport()
    field = convert_comparison(_dob_comparison_duckdb(), 0, report)

    assert field is not None
    assert field.field == "dob"
    assert field.scorer == "date_diff"
    # exact (1.0) + 1mo (0.80) + 1yr (0.60); the 10yr band snaps to 0.0 and is
    # dropped as out-of-range, so 4 levels not 5.
    assert field.levels == 4
    assert field.level_thresholds == [1.0, 0.80, 0.60]


def test_dob_comparison_drops_string_edit_level_with_warning():
    report = ConversionReport()
    convert_comparison(_dob_comparison_duckdb(), 0, report)
    warns = [f.message for f in report.findings if f.severity == "warning"]

    # domain-family-wins dropped the damerau_levenshtein level.
    assert any("takes precedence" in w and "damerau_levenshtein" in w for w in warns)
    # the 10yr band converted to 0.0 and was dropped as out-of-range.
    assert any("out of range" in w for w in warns)
    # the approximate snap is surfaced.
    assert any("date-difference cutoff snapped" in w for w in warns)


def test_spark_dob_comparison_converts_to_date_diff_field():
    """Spark uses backtick-quoted columns + a date() wrapper; the shared column
    atom now accepts backticks so exact + date levels both recognize."""
    comp = {
        "output_column_name": "dob",
        "comparison_levels": [
            {
                "sql_condition": (
                    "date(try_to_timestamp(`dob_l`, 'yyyy-MM-dd')) IS NULL OR "
                    "date(try_to_timestamp(`dob_r`, 'yyyy-MM-dd')) IS NULL"
                ),
                "is_null_level": True,
            },
            {"sql_condition": "`dob_l` = `dob_r`"},
            {"sql_condition": "damerau_levenshtein(`dob_l`, `dob_r`) <= 1"},
            {"sql_condition": _SPARK_DATE_1MO},
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.field == "dob"
    assert field.scorer == "date_diff"
    # exact (1.0) + 1mo (0.80)
    assert field.levels == 3
    assert field.level_thresholds == [1.0, 0.80]


def test_abs_time_diff_no_string_edit_level():
    """AbsoluteTimeDifferenceAtThresholds has NO damerau level -- pure date."""
    comp = {
        "output_column_name": "dob",
        "comparison_levels": [
            {
                "sql_condition": (
                    'try_strptime("dob_l", \'%Y-%m-%d\') IS NULL OR '
                    'try_strptime("dob_r", \'%Y-%m-%d\') IS NULL'
                ),
                "is_null_level": True,
            },
            {"sql_condition": '"dob_l" = "dob_r"'},
            {"sql_condition": _DUCK_1MO},
            {"sql_condition": _DUCK_1YR},
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.scorer == "date_diff"
    assert field.levels == 4
    # no string-edit level to drop.
    assert not any(
        "takes precedence" in f.message
        for f in report.findings
        if f.severity == "warning"
    )


def test_two_distinct_domain_families_drops_comparison():
    """A comparison mixing two DIFFERENT recognized domain families (date_diff +
    array_intersect -- an impossible real Splink shape, but the guard must hold)
    is dropped, not silently coerced onto one scorer."""
    comp = {
        "output_column_name": "dob",
        "comparison_levels": [
            {"sql_condition": '"dob_l" = "dob_r"'},
            {"sql_condition": _DUCK_1MO},
            {
                "sql_condition": (
                    'array_length(list_intersect("dob_l", "dob_r")) >= 2'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    assert field is None
    assert any(
        "multiple domain comparator families" in f.message
        for f in report.findings
        if f.severity == "warning"
    )


# --- trained model import ----------------------------------------------------


def _trained_dob_comparison():
    """DObComparison with m/u on every non-null level. The damerau_levenshtein
    level and the 10yr date level do NOT survive conversion, so their m/u mass
    must be dropped (not misapplied to a surviving index)."""
    comp = _dob_comparison_duckdb()
    mu = {
        1: (0.60, 0.02),   # exact
        2: (0.10, 0.05),   # damerau_levenshtein  <- dropped
        3: (0.15, 0.10),   # 1mo
        4: (0.10, 0.20),   # 1yr
        5: (0.02, 0.30),   # 10yr                 <- dropped
        6: (0.03, 0.33),   # ELSE
    }
    for idx, (m, u) in mu.items():
        comp["comparison_levels"][idx]["m_probability"] = m
        comp["comparison_levels"][idx]["u_probability"] = u
    return comp


def _trained_settings(comparisons):
    return {
        "comparisons": comparisons,
        "probability_two_random_records_match": 0.0002,
    }


def test_trained_dob_import_maps_bands_and_drops_string_edit_mass():
    comp = _trained_dob_comparison()
    settings = _trained_settings([comp])
    report = ConversionReport()

    field = convert_comparison(comp, 0, report)
    assert field is not None and field.levels == 4

    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    m = em.m_probs["dob"]
    assert len(m) == 4
    # exact is index 3 (top), ELSE index 0; monotone through the surviving
    # bands: exact > 1mo > 1yr for these m's.
    assert m[3] > m[2] > m[1]
    # the dropped damerau level's threshold does not match any converted level.
    warns = [f for f in report.findings if f.severity == "warning"]
    assert any(
        "does not match any converted threshold" in f.message
        and f.splink_path.endswith("comparison_levels[2]")  # damerau
        for f in warns
    )
    # and the 10yr level (index 5) is likewise dropped.
    assert any(
        "does not match any converted threshold" in f.message
        and f.splink_path.endswith("comparison_levels[5]")
        for f in warns
    )


# --- array_intersect (ArrayIntersectAtSizes) ---------------------------------
#
# Splink counts the intersection SIZE (`>= n`); GoldenMatch's array_intersect
# scorer returns a RATIO, so `>= n` is snapped to an overlap-coefficient
# threshold n / 10 (assumed set size, biased low for recall) + a warn. Decision:
# docs/superpowers/plans/2026-07-25-splink-domain-comparator-conversion-plan.md 7.

_DUCK_ARRAY_3 = 'array_length(list_intersect("skills_l", "skills_r")) >= 3'
_DUCK_ARRAY_1 = 'array_length(list_intersect("skills_l", "skills_r")) >= 1'
_SPARK_ARRAY_3 = "SIZE(ARRAY_INTERSECT(`skills_l`, `skills_r`)) >= 3"


@pytest.mark.parametrize(
    "sql,expected_ratio",
    [
        (_DUCK_ARRAY_3, 0.3),
        (_DUCK_ARRAY_1, 0.1),
        (_SPARK_ARRAY_3, 0.3),   # Spark SIZE(ARRAY_INTERSECT(...)) form
    ],
)
def test_array_intersect_recognized_both_dialects(sql, expected_ratio):
    r = recognize_level(sql)
    assert r is not None
    assert r.kind == "array_intersect"
    assert r.column == "skills"
    assert r.scorer == "array_intersect:overlap"
    assert r.sim_threshold == pytest.approx(expected_ratio, abs=1e-9)
    assert r.approx is True


def test_array_intersect_mismatched_columns_dropped():
    assert (
        recognize_level('array_length(list_intersect("skills_l", "tags_r")) >= 1')
        is None
    )


def _array_comparison():
    """Real DuckDB ArrayIntersectAtSizes([3, 1]): null, two size levels, ELSE."""
    return {
        "output_column_name": "skills",
        "comparison_levels": [
            {
                "sql_condition": '"skills_l" IS NULL OR "skills_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": _DUCK_ARRAY_3},
            {"sql_condition": _DUCK_ARRAY_1},
            {"sql_condition": "ELSE"},
        ],
    }


def test_array_comparison_converts_to_overlap_field():
    report = ConversionReport()
    field = convert_comparison(_array_comparison(), 0, report)

    assert field is not None
    assert field.field == "skills"
    # the ":overlap" scorer string (not the bare "array_intersect" family kind).
    assert field.scorer == "array_intersect:overlap"
    assert field.levels == 3
    assert field.level_thresholds == [0.3, 0.1]


def test_array_comparison_warns_on_count_to_ratio_snap():
    report = ConversionReport()
    convert_comparison(_array_comparison(), 0, report)
    warns = [f.message for f in report.findings if f.severity == "warning"]

    assert any("count >= 3" in w and "overlap-ratio" in w for w in warns)
    assert any("count >= 1" in w for w in warns)
    assert any("biased low for recall" in w for w in warns)


def test_trained_array_import_maps_ratio_bands():
    comp = _array_comparison()
    comp["comparison_levels"][1]["m_probability"] = 0.55
    comp["comparison_levels"][1]["u_probability"] = 0.05
    comp["comparison_levels"][2]["m_probability"] = 0.30
    comp["comparison_levels"][2]["u_probability"] = 0.15
    comp["comparison_levels"][3]["m_probability"] = 0.15
    comp["comparison_levels"][3]["u_probability"] = 0.80
    settings = _trained_settings([comp])

    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    m = em.m_probs["skills"]
    assert len(m) == 3
    # >=3 band (ratio 0.3) is the top level (index 2); >=1 band index 1; ELSE 0.
    assert m[2] > m[1] > m[0]
