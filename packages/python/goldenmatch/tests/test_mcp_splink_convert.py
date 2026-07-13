"""Tests for the convert_splink_config MCP tool (Task 13).

Third surface for the Splink->GoldenMatch converter (Task 11 =
goldenmatch.config.from_splink.from_splink(), Task 12 = the CLI). This tool
takes settings inline as a JSON string (no filesystem assumptions for the
remote MCP caller) and returns the config + report inline, following the
same registration/dispatch/error conventions as `suggest_config` /
`review_config` in goldenmatch/mcp/server.py.
"""
from __future__ import annotations

import json

import yaml

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
                "m_probability": 0.7,
                "u_probability": 0.05,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                ),
                "m_probability": 0.2,
                "u_probability": 0.15,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.1,
                "u_probability": 0.80,
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
        "comparisons": [_trained_jw_comparison()],
        "blocking_rules_to_generate_predictions": ['l."first_name" = r."first_name"'],
        "probability_two_random_records_match": 0.001,
    }


# ── Registration ──────────────────────────────────────────────────────────────


def test_tool_registered():
    from goldenmatch.mcp.server import TOOLS

    names = {t.name for t in TOOLS}
    assert "convert_splink_config" in names


def test_tool_schema_shape():
    from goldenmatch.mcp.server import _BASE_TOOLS

    tool = next(t for t in _BASE_TOOLS if t.name == "convert_splink_config")
    props = tool.inputSchema["properties"]
    assert "settings_json" in props
    assert "strict" in props
    assert tool.inputSchema["required"] == ["settings_json"]


# ── 1. Valid bare settings -> yaml + findings + summary ──────────────────────


def test_bare_settings_returns_valid_yaml_config():
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool(
        "convert_splink_config", {"settings_json": json.dumps(_bare_settings())}
    )

    assert "error" not in result
    assert isinstance(result["config_yaml"], str)

    loaded = yaml.safe_load(result["config_yaml"])
    cfg = GoldenMatchConfig(**loaded)
    mk = cfg.get_matchkeys()[0]
    assert mk.name == "splink_import"
    field_names = {f.field for f in mk.fields}
    assert field_names == {"first_name", "surname"}

    assert isinstance(result["findings"], list)
    for f in result["findings"]:
        assert set(f.keys()) == {"severity", "splink_path", "message", "mapped_to"}
    assert isinstance(result["summary"], str)
    assert "error(s)" in result["summary"]


# ── 2. Trained vs bare -> em_model ────────────────────────────────────────────


def test_trained_settings_include_em_model_dict():
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool(
        "convert_splink_config", {"settings_json": json.dumps(_trained_settings())}
    )

    assert "error" not in result
    assert result["em_model"] is not None
    assert result["em_model"]["__type__"] == "goldenmatch.EMResult"
    assert "first_name" in result["em_model"]["m_probs"]
    assert "model_path" in result["usage_note"] or "save em_model" in result["usage_note"]


def test_bare_settings_em_model_absent():
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool(
        "convert_splink_config", {"settings_json": json.dumps(_bare_settings())}
    )

    assert "error" not in result
    assert result["em_model"] is None


# ── 3. strict=True on lossy input -> clean error convention ──────────────────


def test_strict_true_on_lossy_input_returns_error_dict():
    from goldenmatch.mcp.server import _handle_tool

    comp = _jw_comparison()
    # Cross-column condition is unrecognized -> dropped with a warning finding,
    # which strict=True gates on.
    comp["comparison_levels"].insert(
        3,
        {
            "sql_condition": (
                'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
            )
        },
    )
    settings = {
        "comparisons": [comp, _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }

    result = _handle_tool(
        "convert_splink_config",
        {"settings_json": json.dumps(settings), "strict": True},
    )

    assert "error" in result
    assert "config_yaml" not in result
    assert isinstance(result["error"], str)


def test_strict_false_on_same_lossy_input_succeeds():
    from goldenmatch.mcp.server import _handle_tool

    comp = _jw_comparison()
    comp["comparison_levels"].insert(
        3,
        {
            "sql_condition": (
                'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
            )
        },
    )
    settings = {
        "comparisons": [comp, _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }

    result = _handle_tool(
        "convert_splink_config",
        {"settings_json": json.dumps(settings), "strict": False},
    )

    assert "error" not in result
    assert any(f["severity"] == "warning" for f in result["findings"])


# ── 4. Malformed settings_json -> clean error convention ─────────────────────


def test_malformed_json_returns_error_dict():
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool(
        "convert_splink_config", {"settings_json": "{not valid json"}
    )

    assert "error" in result
    assert "config_yaml" not in result


def test_non_dict_json_returns_error_dict():
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool(
        "convert_splink_config", {"settings_json": json.dumps([1, 2, 3])}
    )

    assert "error" in result
    assert "config_yaml" not in result


def test_zero_convertible_comparisons_returns_error_dict():
    """from_splink() raises SplinkConversionError on zero convertible
    comparisons even outside strict mode; the tool must surface that as the
    clean error dict too, not let it propagate as an exception."""
    from goldenmatch.mcp.server import _handle_tool

    settings = {
        "comparisons": [],
        "blocking_rules_to_generate_predictions": ['l."first_name" = r."first_name"'],
    }
    result = _handle_tool(
        "convert_splink_config", {"settings_json": json.dumps(settings)}
    )

    assert "error" in result
    assert "config_yaml" not in result
