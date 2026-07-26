"""Tests for the `goldenmatch migrate-splink` one-shot command.

migrate-splink converts a Splink model, (best-effort) verifies it against a
locally-installed Splink, then runs the dedupe and writes the golden records --
all in one command. splink is optional, so these run in the normal CI lane with
the verify step degrading to a skip notice.
"""
from __future__ import annotations

import json

import polars as pl
import pytest
from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

_SETTINGS = {
    "link_type": "dedupe_only",
    "unique_id_column_name": "unique_id",
    "comparisons": [
        {
            "output_column_name": "first_name",
            "comparison_levels": [
                {"sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                 "is_null_level": True},
                {"sql_condition": '"first_name_l" = "first_name_r"'},
                {"sql_condition": 'jaro_winkler_similarity("first_name_l","first_name_r") >= 0.9'},
                {"sql_condition": "ELSE"},
            ],
        },
        {
            "output_column_name": "surname",
            "comparison_levels": [
                {"sql_condition": '"surname_l" IS NULL OR "surname_r" IS NULL',
                 "is_null_level": True},
                {"sql_condition": '"surname_l" = "surname_r"'},
                {"sql_condition": "ELSE"},
            ],
        },
    ],
    "blocking_rules_to_generate_predictions": ["l.surname = r.surname"],
}


def _fixture(tmp_path):
    model = tmp_path / "model.json"
    model.write_text(json.dumps(_SETTINGS))
    data = tmp_path / "data.csv"
    pl.DataFrame(
        {
            "unique_id": list(range(8)),
            "first_name": ["john", "jon", "alice", "alicia", "bob", "bob", "carol", "karol"],
            "surname": ["smith", "smith", "jones", "jones", "lee", "lee", "wu", "wu"],
        }
    ).write_csv(data)
    return model, data


def test_migrate_end_to_end_writes_output(tmp_path) -> None:
    model, data = _fixture(tmp_path)
    out = tmp_path / "clusters.parquet"
    result = runner.invoke(
        app, ["migrate-splink", str(model), str(data), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    # coverage scorecard is printed
    assert "coverage" in result.output.lower()
    assert "Converted" in result.output
    # dedupe ran and wrote the output
    assert "Deduped" in result.output
    assert out.exists()


def test_migrate_writes_config_out(tmp_path) -> None:
    model, data = _fixture(tmp_path)
    out = tmp_path / "clusters.csv"
    config_out = tmp_path / "config.yaml"
    result = runner.invoke(
        app,
        [
            "migrate-splink",
            str(model),
            str(data),
            "-o",
            str(out),
            "--config-out",
            str(config_out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert config_out.exists()
    assert out.exists()


def test_migrate_no_verify_skips_verification(tmp_path) -> None:
    model, data = _fixture(tmp_path)
    out = tmp_path / "clusters.parquet"
    result = runner.invoke(
        app, ["migrate-splink", str(model), str(data), "-o", str(out), "--no-verify"]
    )
    assert result.exit_code == 0, result.output
    # with --no-verify there is no verification section at all
    assert "agreement" not in result.output.lower()
    assert "verification skipped" not in result.output.lower()
    assert out.exists()


def test_migrate_bad_model_exits_nonzero(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"comparisons": [], "blocking_rules_to_generate_predictions": []}))
    data = tmp_path / "data.csv"
    pl.DataFrame({"unique_id": [0, 1], "first_name": ["a", "b"]}).write_csv(data)
    result = runner.invoke(app, ["migrate-splink", str(bad), str(data)])
    assert result.exit_code == 1


def test_migrate_real_splink_shows_agreement(tmp_path) -> None:
    pytest.importorskip("splink")
    pytest.importorskip("pandas")
    # trained model so both engines score with the same imported weights
    trained = json.loads(json.dumps(_SETTINGS))
    fn = trained["comparisons"][0]["comparison_levels"]
    fn[1]["m_probability"], fn[1]["u_probability"] = 0.7, 0.02
    fn[2]["m_probability"], fn[2]["u_probability"] = 0.25, 0.05
    fn[3]["m_probability"], fn[3]["u_probability"] = 0.05, 0.93
    sn = trained["comparisons"][1]["comparison_levels"]
    sn[1]["m_probability"], sn[1]["u_probability"] = 0.9, 0.1
    sn[2]["m_probability"], sn[2]["u_probability"] = 0.1, 0.9
    trained["probability_two_random_records_match"] = 0.1

    model = tmp_path / "trained.json"
    model.write_text(json.dumps(trained))
    _, data = _fixture(tmp_path)
    out = tmp_path / "c.parquet"
    result = runner.invoke(
        app,
        ["migrate-splink", str(model), str(data), "-o", str(out), "--verify-sample", "1000"],
    )
    assert result.exit_code == 0, result.output
    assert "agreement" in result.output.lower()
    assert out.exists()
