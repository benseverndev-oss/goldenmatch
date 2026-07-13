import math

import pytest

from goldenmatch.config.from_splink import (
    ConversionReport,
    convert_comparison,
    convert_scalars,
    detect_trained,
    import_em,
)


def _trained_jw_comparison():
    """Same shape as test_from_splink_comparisons._jw_comparison(), but each
    non-null level carries m/u probabilities. m's already sum to 1.0 across
    the non-null levels (0.5 + 0.3 + 0.15 + 0.05), likewise u's (0.02 + 0.08
    + 0.20 + 0.70), so re-normalization should be a no-op.
    """
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": '"first_name_l" = "first_name_r"',
                "m_probability": 0.5,
                "u_probability": 0.02,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                ),
                "m_probability": 0.3,
                "u_probability": 0.08,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88'
                ),
                "m_probability": 0.15,
                "u_probability": 0.20,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.05,
                "u_probability": 0.70,
            },
        ],
    }


def _trained_settings(comparisons):
    return {
        "comparisons": comparisons,
        "probability_two_random_records_match": 0.0002,
    }


def test_level_order_reversal_and_exact_copy():
    comp = _trained_jw_comparison()
    settings = _trained_settings([comp])
    report = ConversionReport()

    field = convert_comparison(comp, 0, report)
    assert field is not None
    assert field.levels == 4

    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    m = em.m_probs["first_name"]
    assert m[3] == pytest.approx(0.5, abs=1e-9)   # exact
    assert m[2] == pytest.approx(0.3, abs=1e-9)   # jw >= 0.92
    assert m[1] == pytest.approx(0.15, abs=1e-9)  # jw >= 0.88
    assert m[0] == pytest.approx(0.05, abs=1e-9)  # ELSE

    u = em.u_probs["first_name"]
    assert u[3] == pytest.approx(0.02, abs=1e-9)
    assert u[2] == pytest.approx(0.08, abs=1e-9)
    assert u[1] == pytest.approx(0.20, abs=1e-9)
    assert u[0] == pytest.approx(0.70, abs=1e-9)


def test_match_weights_are_log2_m_over_u():
    comp = _trained_jw_comparison()
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    em = import_em([(comp, 0, field)], settings, report)

    m = em.m_probs["first_name"]
    u = em.u_probs["first_name"]
    w = em.match_weights["first_name"]
    for i in range(4):
        assert w[i] == pytest.approx(math.log2(m[i] / u[i]), abs=1e-9)


def test_proportion_matched_from_settings():
    comp = _trained_jw_comparison()
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    em = import_em([(comp, 0, field)], settings, report)

    assert em.proportion_matched == pytest.approx(0.0002)


def test_import_marks_converged_zero_iterations_no_tf():
    comp = _trained_jw_comparison()
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    em = import_em([(comp, 0, field)], settings, report)

    assert em.converged is True
    assert em.iterations == 0
    assert em.tf_freqs is None


def test_dropped_level_renormalizes_and_warns():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": '"first_name_l" = "first_name_r"',
                "m_probability": 0.5,
                "u_probability": 0.02,
            },
            # unrecognized cross-column condition, but carries m/u anyway
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
                ),
                "m_probability": 0.2,
                "u_probability": 0.10,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88'
                ),
                "m_probability": 0.2,
                "u_probability": 0.18,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.1,
                "u_probability": 0.70,
            },
        ],
    }
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    assert field is not None
    assert field.levels == 3  # exact + one jw band + ELSE (bad level dropped)

    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    m = em.m_probs["first_name"]
    assert sum(m) == pytest.approx(1.0, abs=1e-9)
    u = em.u_probs["first_name"]
    assert sum(u) == pytest.approx(1.0, abs=1e-9)

    # surviving mass: m = 0.5 (exact) + 0.2 (jw 0.88) + 0.1 (ELSE) = 0.8
    assert m[2] == pytest.approx(0.5 / 0.8, abs=1e-9)
    assert m[1] == pytest.approx(0.2 / 0.8, abs=1e-9)
    assert m[0] == pytest.approx(0.1 / 0.8, abs=1e-9)

    assert any(
        "re-normaliz" in f.message.lower() for f in report.findings
    )


def test_null_levels_carry_no_m_u():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
                # Splink doesn't normally put m/u on a null level, but assert
                # that even if present, it's ignored.
                "m_probability": 0.9,
                "u_probability": 0.9,
            },
            {
                "sql_condition": '"first_name_l" = "first_name_r"',
                "m_probability": 0.8,
                "u_probability": 0.1,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.2,
                "u_probability": 0.9,
            },
        ],
    }
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    assert field is not None
    assert field.levels == 2

    em = import_em([(comp, 0, field)], settings, report)
    m = em.m_probs["first_name"]
    u = em.u_probs["first_name"]
    # Only the exact + ELSE levels' 0.8/0.2 and 0.1/0.9 participate; the null
    # level's 0.9/0.9 must not leak in.
    assert m[1] == pytest.approx(0.8, abs=1e-9)
    assert m[0] == pytest.approx(0.2, abs=1e-9)
    assert u[1] == pytest.approx(0.1, abs=1e-9)
    assert u[0] == pytest.approx(0.9, abs=1e-9)


def test_collapsed_duplicate_levels_sum_m_u_and_warn():
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.9'
                ),
                "m_probability": 0.5,
                "u_probability": 0.05,
            },
            # Same threshold via the spark-dialect alias: Task 8's threshold
            # dedupe collapses this onto the same GoldenMatch level.
            {
                "sql_condition": (
                    'jaro_winkler("first_name_l", "first_name_r") >= 0.9'
                ),
                "m_probability": 0.3,
                "u_probability": 0.15,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.2,
                "u_probability": 0.80,
            },
        ],
    }
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    assert field is not None
    assert field.levels == 2  # duplicate threshold deduped

    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    m = em.m_probs["first_name"]
    u = em.u_probs["first_name"]
    # summed: agree level m = 0.5 + 0.3 = 0.8, u = 0.05 + 0.15 = 0.20
    assert m[1] == pytest.approx(0.8, abs=1e-9)
    assert m[0] == pytest.approx(0.2, abs=1e-9)
    assert u[1] == pytest.approx(0.20, abs=1e-9)
    assert u[0] == pytest.approx(0.80, abs=1e-9)

    collapse_warns = [
        f
        for f in report.findings
        if f.severity == "warning" and "collapsed" in f.message and "summed" in f.message
    ]
    assert len(collapse_warns) == 1


@pytest.mark.parametrize("missing_side", ["m_probability", "u_probability"])
def test_partial_probability_data_filled_with_epsilon_and_warned(missing_side):
    exact_level = {
        "sql_condition": '"first_name_l" = "first_name_r"',
        "m_probability": 0.8,
        "u_probability": 0.1,
    }
    del exact_level[missing_side]
    comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            exact_level,
            {
                "sql_condition": "ELSE",
                "m_probability": 0.2,
                "u_probability": 0.9,
            },
        ],
    }
    settings = _trained_settings([comp])
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    assert field is not None

    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None

    # The missing side got the epsilon floor (1e-6), then re-normalized --
    # so the exact level's value on that side is tiny but nonzero.
    epsilon = 1e-6
    if missing_side == "m_probability":
        vals = em.m_probs["first_name"]
        assert vals[1] == pytest.approx(epsilon / (epsilon + 0.2), rel=1e-6)
        assert vals[1] > 0.0
    else:
        vals = em.u_probs["first_name"]
        assert vals[1] == pytest.approx(epsilon / (epsilon + 0.9), rel=1e-6)
        assert vals[1] > 0.0

    partial_warns = [
        f
        for f in report.findings
        if f.severity == "warning"
        and "partial trained data" in f.message
        and missing_side in f.message
    ]
    assert len(partial_warns) == 1
    assert "comparison_levels[1]" in partial_warns[0].splink_path


def test_bare_settings_no_m_probability_returns_none():
    comp = {
        "output_column_name": "surname",
        "comparison_levels": [
            {
                "sql_condition": '"surname_l" IS NULL OR "surname_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": '"surname_l" = "surname_r"'},
            {"sql_condition": "ELSE"},
        ],
    }
    settings = {"comparisons": [comp]}
    report = ConversionReport()
    field = convert_comparison(comp, 0, report)
    assert field is not None

    assert detect_trained(settings) is False
    assert import_em([(comp, 0, field)], settings, report) is None


def test_detect_trained_true_when_any_level_has_m_probability():
    settings = _trained_settings([_trained_jw_comparison()])
    assert detect_trained(settings) is True


# ── convert_scalars ──────────────────────────────────────────────────────────


def test_convert_scalars_em_convergence_and_max_iterations():
    settings = {"em_convergence": 0.0001, "max_iterations": 15}
    report = ConversionReport()
    kwargs = convert_scalars(settings, report)

    assert kwargs == {"convergence_threshold": 0.0001, "em_iterations": 15}
    infos = [f for f in report.findings if f.severity == "info"]
    assert any("em_convergence" in f.message for f in infos)
    assert any("max_iterations" in f.message for f in infos)


def test_convert_scalars_unique_id_column_name_is_advisory_only():
    settings = {"unique_id_column_name": "record_id"}
    report = ConversionReport()
    kwargs = convert_scalars(settings, report)

    assert "unique_id_column_name" not in kwargs
    assert not kwargs
    assert any(
        "record_id" in f.message and "id_column" in f.message
        for f in report.findings
    )


def test_convert_scalars_link_and_dedupe_warns():
    settings = {"link_type": "link_and_dedupe"}
    report = ConversionReport()
    convert_scalars(settings, report)

    assert report.has_warnings
    assert any("link_and_dedupe" in f.message for f in report.findings)


@pytest.mark.parametrize(
    "link_type,expected_entry_point",
    [("dedupe_only", "dedupe()"), ("link_only", "match()")],
)
def test_convert_scalars_dedupe_and_link_only_info(link_type, expected_entry_point):
    settings = {"link_type": link_type}
    report = ConversionReport()
    convert_scalars(settings, report)

    assert not report.has_warnings
    assert any(expected_entry_point in f.message for f in report.findings)


def test_convert_scalars_infra_keys_ignored():
    settings = {
        "sql_dialect": "duckdb",
        "retain_matching_columns": True,
        "retain_intermediate_calculation_columns": False,
        "bayes_factor_column_prefix": "bf_",
    }
    report = ConversionReport()
    kwargs = convert_scalars(settings, report)

    assert kwargs == {}
    infos = [f for f in report.findings if f.severity == "info"]
    assert len(infos) == 4
    for msg_key in (
        "sql_dialect",
        "retain_matching_columns",
        "retain_intermediate_calculation_columns",
        "bayes_factor_column_prefix",
    ):
        assert any(
            msg_key in f.message and "ignored" in f.message for f in infos
        )
