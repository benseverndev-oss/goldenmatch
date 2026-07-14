"""Tests for goldenmatch.config.splink_upgrade -- Task U1 scaffold:
dataclasses, sampling, upfront column validation, and lever dispatch/skip
semantics. The three lever bodies are stubs until Tasks U2-U4; these tests
only exercise paths that don't hit the stubs (bare-settings fixtures,
``levers=set()``/subset selection, ``measure=False``).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.from_splink import from_splink
from goldenmatch.config.splink_upgrade import (
    MigrationResult,
    SplinkUpgradeError,
    upgrade_splink_conversion,
)


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


def _trained_jw_comparison():
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


def _bare_settings():
    return {
        "comparisons": [_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }


def _trained_settings():
    return {
        "comparisons": [_trained_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
        "probability_two_random_records_match": 0.0002,
    }


def _sample_df(n=30):
    first_names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    surnames = ["smith", "jones", "brown", "davis", "wilson", "moore"]
    return pl.DataFrame(
        {
            "first_name": [first_names[i % len(first_names)] for i in range(n)],
            "surname": [surnames[i % len(surnames)] for i in range(n)],
        }
    )


# ── Basic shape / copy-on-write ──────────────────────────────────────────────


def test_returns_migration_result_and_conversion_unmutated():
    conversion = from_splink(_bare_settings())
    baseline_dump_before = conversion.config.model_dump()
    findings_len_before = len(conversion.report.findings)

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers={"tf_tables", "calibration"}, measure=False
    )

    assert isinstance(result, MigrationResult)
    assert result.baseline_config.model_dump() == baseline_dump_before
    # Input conversion itself must be untouched.
    assert conversion.config.model_dump() == baseline_dump_before
    assert len(conversion.report.findings) == findings_len_before


def test_upgraded_config_is_distinct_object():
    conversion = from_splink(_bare_settings())
    baseline_dump_before = conversion.config.model_dump()

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers={"tf_tables", "calibration"}, measure=False
    )

    assert result.upgraded_config is not result.baseline_config
    # Mutate a field list on the upgraded copy; baseline must be unaffected.
    result.upgraded_config.get_matchkeys()[0].fields.append(
        result.upgraded_config.get_matchkeys()[0].fields[0].model_copy()
    )
    assert conversion.config.model_dump() == baseline_dump_before
    assert len(result.upgraded_config.get_matchkeys()[0].fields) != len(
        conversion.config.get_matchkeys()[0].fields
    )


def test_em_model_copy_is_deep():
    """EMResult.to_dict()/from_dict() passes nested dicts/lists by reference,
    so the orchestrator must deepcopy -- an in-place mutation of the returned
    model's nested containers must never leak into the input conversion's."""
    conversion = from_splink(_trained_settings())
    assert conversion.em_model is not None
    original_m_probs = [*conversion.em_model.m_probs["first_name"]]

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers=set(), measure=False
    )

    assert result.em_model is not None
    assert result.em_model is not conversion.em_model
    # Nested containers must be distinct objects, not shared references.
    assert result.em_model.m_probs is not conversion.em_model.m_probs
    assert (
        result.em_model.m_probs["first_name"]
        is not conversion.em_model.m_probs["first_name"]
    )
    assert (
        result.em_model.match_weights["first_name"]
        is not conversion.em_model.match_weights["first_name"]
    )
    # In-place mutation of the copy must not alter the original.
    result.em_model.m_probs["first_name"][0] = 0.999
    result.em_model.match_weights["injected"] = [1.0]
    assert conversion.em_model.m_probs["first_name"] == original_m_probs
    assert "injected" not in conversion.em_model.match_weights


# ── Column validation ─────────────────────────────────────────────────────────


def test_missing_column_raises_naming_it():
    conversion = from_splink(_bare_settings())
    df = _sample_df().drop("surname")

    with pytest.raises(SplinkUpgradeError) as exc_info:
        upgrade_splink_conversion(conversion, df, measure=False, levers=set())

    assert "surname" in str(exc_info.value)


# ── Data loading ──────────────────────────────────────────────────────────────


def test_parquet_path_input_works(tmp_path):
    conversion = from_splink(_bare_settings())
    p = tmp_path / "data.parquet"
    _sample_df().write_parquet(p)

    result = upgrade_splink_conversion(conversion, p, measure=False, levers=set())
    assert isinstance(result, MigrationResult)


# ── Sampling determinism ──────────────────────────────────────────────────────


def test_sampling_deterministic_across_calls():
    conversion = from_splink(_bare_settings())
    df = _sample_df(30)

    result1 = upgrade_splink_conversion(
        conversion, df, sample_cap=10, seed=42, measure=False, levers=set()
    )
    result2 = upgrade_splink_conversion(
        conversion, df, sample_cap=10, seed=42, measure=False, levers=set()
    )

    sample_findings_1 = [f for f in result1.report.findings if f.splink_path == "upgrade:sample"]
    sample_findings_2 = [f for f in result2.report.findings if f.splink_path == "upgrade:sample"]
    assert sample_findings_1 and sample_findings_2
    assert sample_findings_1[0].message == sample_findings_2[0].message


def test_sampling_note_only_when_over_cap():
    conversion = from_splink(_bare_settings())
    df = _sample_df(30)

    result = upgrade_splink_conversion(
        conversion, df, sample_cap=1000, measure=False, levers=set()
    )
    assert not any(f.splink_path == "upgrade:sample" for f in result.report.findings)


# ── Bare-settings skip semantics ──────────────────────────────────────────────


def test_bare_settings_skips_tf_tables_and_calibration():
    conversion = from_splink(_bare_settings())
    assert conversion.em_model is None

    result = upgrade_splink_conversion(
        conversion,
        _sample_df(),
        levers={"tf_tables", "calibration"},
        measure=False,
    )

    tf_findings = [f for f in result.report.findings if f.splink_path == "upgrade:tf_tables"]
    calib_findings = [f for f in result.report.findings if f.splink_path == "upgrade:calibration"]
    assert tf_findings and tf_findings[0].severity == "info"
    assert "skipped" in tf_findings[0].message.lower()
    assert calib_findings and calib_findings[0].severity == "info"
    assert "skipped" in calib_findings[0].message.lower()


def test_bare_settings_distance_thresholds_lever_reaches_stub():
    conversion = from_splink(_bare_settings())
    assert conversion.em_model is None

    with pytest.raises(NotImplementedError):
        upgrade_splink_conversion(
            conversion,
            _sample_df(),
            levers={"distance_thresholds"},
            measure=False,
        )


# ── Trained settings: calibration lever body not yet implemented ─────────────


def test_trained_settings_calibration_lever_reaches_stub():
    conversion = from_splink(_trained_settings())
    assert conversion.em_model is not None

    with pytest.raises(NotImplementedError):
        upgrade_splink_conversion(
            conversion, _sample_df(), levers={"calibration"}, measure=False
        )


# ── Lever 1: TF tables ────────────────────────────────────────────────────────


def _trained_settings_with_tf_adjustment():
    """Trained settings whose first_name comparison's exact-match level
    carries ``tf_adjustment_column`` -- converts to a field with
    ``tf_adjustment=True`` (surname stays plain, no tf_adjustment)."""
    comp = _trained_jw_comparison()
    comp["comparison_levels"][1]["tf_adjustment_column"] = "first_name"
    return {
        "comparisons": [comp, _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
        "probability_two_random_records_match": 0.0002,
    }


def _skewed_tf_df(n=40):
    """40 rows with a skewed first_name distribution (alice dominates) and a
    surname column that never gets a TF table (no tf_adjustment on it)."""
    first_names = ["alice"] * 20 + ["bob"] * 10 + ["carol"] * 6 + ["dave"] * 4
    surnames = ["smith", "jones", "brown", "davis"] * (n // 4)
    return pl.DataFrame({"first_name": first_names[:n], "surname": surnames[:n]})


def test_tf_tables_lever_builds_table_for_tf_adjustment_field():
    conversion = from_splink(_trained_settings_with_tf_adjustment())
    assert conversion.em_model is not None
    assert conversion.em_model.tf_freqs is None

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    assert result.em_model is not None
    freqs = result.em_model.tf_freqs["first_name"]
    assert freqs.keys() == {"alice", "bob", "carol", "dave"}
    assert freqs["alice"] == pytest.approx(20 / 40)
    assert sum(freqs.values()) == pytest.approx(1.0)
    assert result.em_model.tf_collision["first_name"] == pytest.approx(
        sum(f**2 for f in freqs.values())
    )

    # Baseline untouched (copy-on-write).
    assert conversion.em_model.tf_freqs is None


def test_tf_tables_lever_skips_field_without_tf_adjustment():
    conversion = from_splink(_trained_settings_with_tf_adjustment())

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    assert result.em_model.tf_freqs is not None
    assert "surname" not in result.em_model.tf_freqs
    per_field_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and "surname" in f.message
    ]
    assert per_field_findings == []


def test_tf_tables_lever_finding_has_field_name_and_distinct_count():
    conversion = from_splink(_trained_settings_with_tf_adjustment())

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    tf_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and f.severity == "info"
        and "first_name" in f.message
    ]
    assert len(tf_findings) == 1
    assert "4" in tf_findings[0].message  # distinct-value count


def test_tf_tables_lever_skips_field_already_present():
    conversion = from_splink(_trained_settings_with_tf_adjustment())
    # Pre-populate the copy scenario: mutate a from_dict-built EMResult fixture
    # so the imported model already carries a TF table for first_name.
    conversion.em_model.tf_freqs = {"first_name": {"alice": 1.0}}
    conversion.em_model.tf_collision = {"first_name": 1.0}

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    # Untouched -- the pre-existing table wins, no rebuild.
    assert result.em_model.tf_freqs["first_name"] == {"alice": 1.0}
    skip_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and "already" in f.message.lower()
    ]
    assert len(skip_findings) == 1
    assert "first_name" in skip_findings[0].message


def test_tf_tables_lever_null_column_warns_and_skips():
    conversion = from_splink(_trained_settings_with_tf_adjustment())
    df = pl.DataFrame(
        {"first_name": [None] * 10, "surname": ["smith"] * 10}
    )

    result = upgrade_splink_conversion(
        conversion, df, levers={"tf_tables"}, measure=False
    )

    assert result.em_model.tf_freqs is None
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and f.severity == "warning"
    ]
    assert len(warn_findings) == 1
    assert "first_name" in warn_findings[0].message


# ── Unknown lever name ────────────────────────────────────────────────────────


def test_unknown_lever_name_raises():
    conversion = from_splink(_bare_settings())

    with pytest.raises(SplinkUpgradeError):
        upgrade_splink_conversion(
            conversion, _sample_df(), levers={"nope"}, measure=False
        )


# ── measure=True placeholder (U5 not yet wired) ───────────────────────────────


def test_measure_true_records_not_yet_wired_and_measurement_none():
    conversion = from_splink(_bare_settings())

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers=set(), measure=True
    )

    assert result.measurement is None
    assert any(
        f.splink_path == "upgrade:measure" and "not yet wired" in f.message
        for f in result.report.findings
    )
