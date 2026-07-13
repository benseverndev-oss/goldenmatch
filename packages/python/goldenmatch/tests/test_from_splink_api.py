"""Tests for the public goldenmatch.config.from_splink.from_splink() entry
point: strict mode, error paths, and the top-level goldenmatch exports.
"""
import json

import pytest

from goldenmatch.config.from_splink import (
    SplinkConversion,
    SplinkConversionError,
    from_splink,
)
from goldenmatch.config.schemas import GoldenMatchConfig


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
    """Same shape as _jw_comparison() but each non-null level carries m/u
    that already sums to 1.0 (no re-normalization needed)."""
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


def _full_settings(comparisons=None, blocking_rules=None):
    return {
        "comparisons": comparisons if comparisons is not None else [
            _jw_comparison(),
            _exact_only_comparison("surname"),
        ],
        "blocking_rules_to_generate_predictions": blocking_rules if blocking_rules is not None else [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }


# ── 1. Full settings dict ────────────────────────────────────────────────────


def test_full_settings_dict_produces_valid_config():
    settings = _full_settings()
    conversion = from_splink(settings)

    assert isinstance(conversion, SplinkConversion)
    assert isinstance(conversion.config, GoldenMatchConfig)

    mks = conversion.config.get_matchkeys()
    assert len(mks) == 1
    mk = mks[0]
    assert mk.name == "splink_import"
    assert mk.type == "probabilistic"
    field_names = {f.field for f in mk.fields}
    assert field_names == {"first_name", "surname"}

    assert conversion.config.blocking is not None
    assert conversion.config.blocking.strategy == "multi_pass"
    assert len(conversion.config.blocking.passes) == 2

    # Round-trips through Pydantic validation again.
    dumped = conversion.config.model_dump(exclude_none=True)
    round_tripped = GoldenMatchConfig(**dumped)
    assert round_tripped.get_matchkeys()[0].name == "splink_import"


def test_full_settings_dict_not_mutated():
    settings = _full_settings()
    original = json.loads(json.dumps(settings))
    from_splink(settings)
    assert settings == original


# ── 2. JSON file / Path input ────────────────────────────────────────────────


def test_json_file_path_input(tmp_path):
    settings = _full_settings()
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(settings), encoding="utf-8")

    conversion = from_splink(str(p))
    assert isinstance(conversion.config, GoldenMatchConfig)
    field_names = {f.field for f in conversion.config.get_matchkeys()[0].fields}
    assert field_names == {"first_name", "surname"}


def test_path_object_input(tmp_path):
    settings = _full_settings()
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(settings), encoding="utf-8")

    conversion = from_splink(p)
    assert isinstance(conversion.config, GoldenMatchConfig)


# ── 3. Trained vs bare -> em_model ────────────────────────────────────────────


def test_trained_settings_produce_em_model():
    settings = _full_settings(comparisons=[_trained_jw_comparison()])
    settings["probability_two_random_records_match"] = 0.0002
    conversion = from_splink(settings)

    assert conversion.em_model is not None
    assert "first_name" in conversion.em_model.m_probs


def test_bare_settings_em_model_is_none():
    settings = _full_settings()
    conversion = from_splink(settings)

    assert conversion.em_model is None


# ── 4. strict=True on lossy input ────────────────────────────────────────────


def test_strict_true_raises_on_unmappable_level():
    comp = _jw_comparison()
    # Cross-column condition: recognize_level() rejects it -> warning finding,
    # level dropped, but the comparison overall still converts.
    comp["comparison_levels"].insert(
        4,
        {
            "sql_condition": (
                'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
            )
        },
    )
    settings = _full_settings(comparisons=[comp, _exact_only_comparison("surname")])

    with pytest.raises(SplinkConversionError) as exc_info:
        from_splink(settings, strict=True)

    msg = str(exc_info.value)
    assert "warning" in msg.lower() or "error" in msg.lower()
    assert "error(s)" in msg and "warning(s)" in msg


def test_strict_false_does_not_raise_on_same_lossy_input():
    comp = _jw_comparison()
    comp["comparison_levels"].insert(
        4,
        {
            "sql_condition": (
                'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
            )
        },
    )
    settings = _full_settings(comparisons=[comp, _exact_only_comparison("surname")])

    conversion = from_splink(settings, strict=False)
    assert conversion.report.has_warnings


# ── 5. Zero convertible comparisons / blocking rules ────────────────────────


def test_zero_convertible_comparisons_raises_in_default_mode():
    settings = _full_settings(comparisons=[])
    with pytest.raises(SplinkConversionError):
        from_splink(settings, strict=False)


def test_all_comparisons_unrecognized_raises():
    bad_comp = {
        "output_column_name": "first_name",
        "comparison_levels": [
            {"sql_condition": "some_weird_udf(first_name_l, first_name_r) > 3"},
        ],
    }
    settings = _full_settings(comparisons=[bad_comp])
    with pytest.raises(SplinkConversionError):
        from_splink(settings, strict=False)


def test_zero_convertible_blocking_rules_raises_in_default_mode():
    settings = _full_settings(blocking_rules=["l.a > r.a OR l.b < r.b"])
    with pytest.raises(SplinkConversionError):
        from_splink(settings, strict=False)


# ── 6. Top-level exports ─────────────────────────────────────────────────────


def test_top_level_from_splink_import():
    import goldenmatch

    assert goldenmatch.from_splink is from_splink or callable(goldenmatch.from_splink)
    conversion = goldenmatch.from_splink(_full_settings())
    assert isinstance(conversion, SplinkConversion)


def test_top_level_splink_conversion_import():
    from goldenmatch import SplinkConversion as TopSplinkConversion

    assert TopSplinkConversion is SplinkConversion


# ── 7. Scalars land on the matchkey ──────────────────────────────────────────


def test_em_iterations_and_convergence_threshold_on_matchkey():
    settings = _full_settings()
    settings["em_convergence"] = 0.0005
    settings["max_iterations"] = 12

    conversion = from_splink(settings)
    mk = conversion.config.get_matchkeys()[0]
    assert mk.convergence_threshold == pytest.approx(0.0005)
    assert mk.em_iterations == 12
