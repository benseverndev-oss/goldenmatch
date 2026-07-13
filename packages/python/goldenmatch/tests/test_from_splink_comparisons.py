import pytest

from goldenmatch.config.from_splink import ConversionReport, convert_comparison


def _jw_comparison():
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": '"first_name_l" = "first_name_r"'},
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }


def _exact_only_comparison(column="surname"):
    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": f'"{column}_l" = "{column}_r"'},
            {"sql_condition": "ELSE"},
        ],
    }


def test_jw_comparison_with_exact_and_two_bands():
    report = ConversionReport()
    field = convert_comparison(_jw_comparison(), 0, report)

    assert field is not None
    assert field.field == "first_name"
    assert field.scorer == "jaro_winkler"
    assert field.levels == 4
    assert field.level_thresholds == [1.0, 0.92, 0.88]

    infos = [f for f in report.findings if f.severity == "info"]
    assert any("null" in f.message.lower() for f in infos)


def test_pure_exact_comparison_is_legacy_2_level():
    report = ConversionReport()
    field = convert_comparison(_exact_only_comparison(), 0, report)

    assert field is not None
    assert field.field == "surname"
    assert field.scorer == "exact"
    assert field.levels == 2
    assert field.level_thresholds is None


def test_exact_plus_one_jw_band_is_3_level():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": '"first_name_l" = "first_name_r"'},
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.scorer == "jaro_winkler"
    assert field.levels == 3
    assert field.level_thresholds == [1.0, 0.92]


def test_mixed_families_drops_comparison():
    comp = {
        "output_column_name": "dob",
        "comparison_levels": [
            {"sql_condition": '"dob_l" IS NULL OR "dob_r" IS NULL', "is_null_level": True},
            {
                "sql_condition": (
                    'jaro_winkler_similarity("dob_l", "dob_r") >= 0.92'
                )
            },
            {"sql_condition": 'levenshtein("dob_l", "dob_r") <= 1'},
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is None
    assert report.has_warnings
    assert any("mixed comparator families" in f.message for f in report.findings)


def test_one_unrecognized_level_dropped_thresholds_rederived():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            # cross-column condition between two JW bands, unrecognized
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
                )
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.80'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.scorer == "jaro_winkler"
    assert field.level_thresholds == [0.92, 0.80]
    assert field.levels == 3
    assert any("unrecognized sql_condition" in f.message for f in report.findings)


def test_column_inconsistency_drops_comparison():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("surname_l", "surname_r") >= 0.88'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is None
    assert any("inconsistent columns" in f.message for f in report.findings)


def test_tf_adjustment_column_sets_flag():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": '"first_name_l" = "first_name_r"',
                "tf_adjustment_column": "first_name",
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.tf_adjustment is True
    assert not report.has_warnings


def test_tf_adjustment_weight_dropped_with_warning():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": '"first_name_l" = "first_name_r"',
                "tf_adjustment_column": "first_name",
                "tf_adjustment_weight": 0.5,
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.tf_adjustment is True
    assert any("tf_adjustment_weight" in f.message for f in report.findings)


def test_duplicate_thresholds_deduped():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {
                "sql_condition": (
                    'jaro_winkler("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is not None
    assert field.levels == 2
    assert field.partial_threshold == 0.92
    assert field.level_thresholds is None


def test_all_levels_unrecognized_returns_none():
    comp = {
        "output_column_name": "amount",
        "comparison_levels": [
            {"sql_condition": 'abs("amount_l" - "amount_r") < 5'},
            {"sql_condition": '"amount_l" > "amount_r"'},
        ],
    }
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)

    assert field is None
    assert report.has_warnings
    assert any("unrecognized sql_condition" in f.message for f in report.findings)
