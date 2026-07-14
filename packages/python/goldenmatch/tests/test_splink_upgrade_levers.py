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


# ── Trained settings: tf_tables/calibration lever bodies not yet implemented ──


def test_trained_settings_tf_tables_lever_reaches_stub():
    conversion = from_splink(_trained_settings())
    assert conversion.em_model is not None

    with pytest.raises(NotImplementedError):
        upgrade_splink_conversion(
            conversion, _sample_df(), levers={"tf_tables"}, measure=False
        )


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
