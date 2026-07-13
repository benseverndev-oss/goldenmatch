"""Tests for the `goldenmatch import-splink` CLI command."""
from __future__ import annotations

import json

import yaml
from goldenmatch.cli.main import app
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.probabilistic import EMResult
from typer.testing import CliRunner

runner = CliRunner()


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


def _write_settings(tmp_path, settings, name="settings.json"):
    p = tmp_path / name
    p.write_text(json.dumps(settings), encoding="utf-8")
    return p


# ── 1. Happy path ─────────────────────────────────────────────────────────


def test_happy_path_writes_valid_config(tmp_path):
    settings_path = _write_settings(tmp_path, _full_settings())
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app, ["import-splink", str(settings_path), "-o", str(out_path)]
    )

    assert result.exit_code == 0, result.stdout
    assert out_path.exists()

    loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    cfg = GoldenMatchConfig(**loaded)
    assert cfg.get_matchkeys()[0].name == "splink_import"

    assert "error(s)" in result.stdout and "warning(s)" in result.stdout


# ── 2. Trained model + --model-out ────────────────────────────────────────


def test_trained_model_with_model_out(tmp_path):
    settings = _full_settings(comparisons=[_trained_jw_comparison()])
    settings_path = _write_settings(tmp_path, settings)
    out_path = tmp_path / "out.yaml"
    model_path = tmp_path / "model.json"

    result = runner.invoke(
        app,
        [
            "import-splink",
            str(settings_path),
            "-o", str(out_path),
            "--model-out", str(model_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert model_path.exists()

    em = EMResult.load_json(str(model_path))
    assert "first_name" in em.m_probs

    loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert loaded["matchkeys"][0]["model_path"] == str(model_path)


# ── 3. Trained model WITHOUT --model-out ──────────────────────────────────


def test_trained_model_without_model_out_warns(tmp_path):
    settings = _full_settings(comparisons=[_trained_jw_comparison()])
    settings_path = _write_settings(tmp_path, settings)
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app, ["import-splink", str(settings_path), "-o", str(out_path)]
    )

    assert result.exit_code == 0, result.stdout
    assert "--model-out" in result.stdout
    assert "not" in result.stdout.lower() and "persist" in result.stdout.lower()


# ── 4. --strict on lossy input ────────────────────────────────────────────


def test_strict_fails_on_lossy_input(tmp_path):
    comp = _jw_comparison()
    # Cross-column condition -- recognize_level() rejects it, producing a
    # warning finding (level dropped) even though the comparison as a whole
    # still converts.
    comp["comparison_levels"].insert(
        4,
        {
            "sql_condition": (
                'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
            )
        },
    )
    settings = _full_settings(comparisons=[comp, _exact_only_comparison("surname")])
    settings_path = _write_settings(tmp_path, settings)
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app, ["import-splink", str(settings_path), "-o", str(out_path), "--strict"]
    )

    assert result.exit_code == 1
    assert not out_path.exists()


# ── 5. Error-severity conversion ──────────────────────────────────────────


def test_no_convertible_blocking_rules_fails(tmp_path):
    settings = _full_settings(blocking_rules=["l.a > r.a OR l.b < r.b"])
    settings_path = _write_settings(tmp_path, settings)
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app, ["import-splink", str(settings_path), "-o", str(out_path)]
    )

    assert result.exit_code == 1
    assert not out_path.exists()


# ── 6. Unwritable output path -> clean CLI error, no traceback ────────────


def test_output_into_nonexistent_directory_fails_cleanly(tmp_path):
    settings_path = _write_settings(tmp_path, _full_settings())
    out_path = tmp_path / "no_such_dir" / "out.yaml"

    result = runner.invoke(
        app, ["import-splink", str(settings_path), "-o", str(out_path)]
    )

    assert result.exit_code == 1
    assert not out_path.exists()
    # A clean typer.Exit, not a raw OSError traceback.
    assert not isinstance(result.exception, OSError)
    try:
        stderr = result.stderr
    except ValueError:  # stderr not separately captured -> already in output
        stderr = ""
    combined = result.output + stderr
    assert "Traceback" not in combined
    assert "Could not write config" in combined


# ── 7. Default output path ────────────────────────────────────────────────


def test_default_output_path_is_cwd_goldenmatch_yaml(tmp_path):
    settings_path = _write_settings(tmp_path, _full_settings())

    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        import shutil
        local_settings = "settings.json"
        shutil.copy(str(settings_path), local_settings)

        result = runner.invoke(app, ["import-splink", local_settings])

        assert result.exit_code == 0, result.stdout
        from pathlib import Path
        assert (Path(cwd) / "goldenmatch.yaml").exists()
