"""Tests for the `goldenmatch import-splink --upgrade` CLI flag (Task U6).

Follows the CliRunner harness from tests/test_cli_import_splink.py. No
--help scraping -- assertions read stdout content / files-on-disk directly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import polars as pl
import yaml
from goldenmatch.cli.main import app
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.probabilistic import EMResult
from typer.testing import CliRunner

runner = CliRunner()


# ── Splink settings fixtures ─────────────────────────────────────────────────


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
                "tf_adjustment_column": "first_name",
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                ),
                "m_probability": 0.3,
                "u_probability": 0.08,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.2,
                "u_probability": 0.90,
            },
        ],
    }


def _trained_exact_comparison(column="surname"):
    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "m_probability": 0.9,
                "u_probability": 0.05,
            },
            {"sql_condition": "ELSE", "m_probability": 0.1, "u_probability": 0.95},
        ],
    }


def _bare_jw_comparison():
    comp = _trained_jw_comparison()
    for level in comp["comparison_levels"]:
        level.pop("m_probability", None)
        level.pop("u_probability", None)
    return comp


def _bare_exact_comparison(column="surname"):
    comp = _trained_exact_comparison(column)
    for level in comp["comparison_levels"]:
        level.pop("m_probability", None)
        level.pop("u_probability", None)
    return comp


def _trained_settings():
    """Trained settings whose first_name level carries tf_adjustment_column
    (converts to tf_adjustment=True) so the TF-tables lever has work to do."""
    return {
        "comparisons": [_trained_jw_comparison(), _trained_exact_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
        "probability_two_random_records_match": 0.0002,
    }


def _bare_settings():
    return {
        "comparisons": [_bare_jw_comparison(), _bare_exact_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }


def _write_settings(tmp_path, settings, name="settings.json"):
    p = tmp_path / name
    p.write_text(json.dumps(settings), encoding="utf-8")
    return p


# ── Dataset fixture (skewed first_name distribution + planted duplicates,
# so the TF lever has a non-trivial frequency table and measurement has
# real clusters to find) ──────────────────────────────────────────────────

_SURNAME_STEMS = [
    "smith", "jones", "brown", "davis", "wilson", "moore", "taylor", "clark",
]
_NAME_STEMS = [
    "alice", "alice", "alice", "bob", "carol", "dave", "erin", "frank",
]


def _upgrade_df(n_entities=8, dupes_per_entity=3):
    uids: list[str] = []
    first: list[str] = []
    sur: list[str] = []
    j = 0
    for i in range(n_entities):
        for _ in range(dupes_per_entity):
            uids.append(f"r{j}")
            first.append(_NAME_STEMS[i % len(_NAME_STEMS)])
            sur.append(_SURNAME_STEMS[i % len(_SURNAME_STEMS)])
            j += 1
    return pl.DataFrame({"unique_id": uids, "first_name": first, "surname": sur})


def _write_parquet(tmp_path, df, name="data.parquet"):
    p = tmp_path / name
    df.write_parquet(str(p))
    return p


# ── 1. Trained + --upgrade + --model-out -> four files ───────────────────────


def test_upgrade_trained_writes_four_files(tmp_path):
    settings_path = _write_settings(tmp_path, _trained_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"
    model_path = tmp_path / "m.json"

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--model-out", str(model_path),
            "--upgrade", str(data_path),
            "--no-measure",
        ],
    )

    assert result.exit_code == 0, result.stdout

    baseline_yaml = tmp_path / "out.baseline.yaml"
    baseline_model = tmp_path / "m.baseline.json"
    assert out_path.exists()
    assert model_path.exists()
    assert baseline_yaml.exists()
    assert baseline_model.exists()

    upgraded_cfg = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert upgraded_cfg["matchkeys"][0]["model_path"] == str(model_path)

    baseline_cfg = yaml.safe_load(baseline_yaml.read_text(encoding="utf-8"))
    assert baseline_cfg["matchkeys"][0]["model_path"] == str(baseline_model)

    baseline_em = EMResult.load_json(str(baseline_model))
    upgraded_em = EMResult.load_json(str(model_path))

    # Baseline is the as-imported model -- Splink exports carry no TF
    # tables, so the baseline lacks tf_freqs entirely, while the upgraded
    # copy carries a freq table for the tf_adjustment=True field.
    assert not baseline_em.tf_freqs
    assert upgraded_em.tf_freqs is not None
    assert "first_name" in upgraded_em.tf_freqs


# ── 2. Baseline-first ordering: upgraded write failure still leaves the
#      baseline pair on disk ─────────────────────────────────────────────────


def test_baseline_pair_survives_upgraded_write_failure(tmp_path, monkeypatch):
    settings_path = _write_settings(tmp_path, _trained_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"
    model_path = tmp_path / "m.json"

    calls = {"n": 0}
    real_save_json = EMResult.save_json

    def _flaky_save_json(self, path):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("simulated failure on the upgraded model write")
        return real_save_json(self, path)

    monkeypatch.setattr(EMResult, "save_json", _flaky_save_json)

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--model-out", str(model_path),
            "--upgrade", str(data_path),
            "--no-measure",
        ],
    )

    assert result.exit_code == 1

    baseline_yaml = tmp_path / "out.baseline.yaml"
    baseline_model = tmp_path / "m.baseline.json"
    assert baseline_yaml.exists()
    assert baseline_model.exists()


# ── 3. Trained + --upgrade WITHOUT --model-out -> exit 1, no files ──────────


def test_upgrade_trained_without_model_out_fails_cleanly(tmp_path):
    settings_path = _write_settings(tmp_path, _trained_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--upgrade", str(data_path),
        ],
    )

    assert result.exit_code == 1
    assert "--model-out" in result.output
    assert not out_path.exists()
    assert not (tmp_path / "out.baseline.yaml").exists()


# ── 4. Bare settings + --upgrade WITHOUT --model-out -> two yaml files ──────


def test_upgrade_bare_settings_without_model_out(tmp_path):
    settings_path = _write_settings(tmp_path, _bare_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--upgrade", str(data_path),
            "--no-measure",
        ],
    )

    assert result.exit_code == 0, result.stdout

    baseline_yaml = tmp_path / "out.baseline.yaml"
    assert out_path.exists()
    assert baseline_yaml.exists()

    upgraded_cfg = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    baseline_cfg = yaml.safe_load(baseline_yaml.read_text(encoding="utf-8"))
    assert "model_path" not in upgraded_cfg["matchkeys"][0]
    assert "model_path" not in baseline_cfg["matchkeys"][0]
    GoldenMatchConfig(**upgraded_cfg)
    GoldenMatchConfig(**baseline_cfg)

    # No model files were written at all (no --model-out given) -- only the
    # input settings.json (not a model file) should be present.
    assert [p.name for p in tmp_path.glob("*.json")] == ["settings.json"]


# ── 5. --no-measure skips the delta table; measurement prints one ──────────


def test_no_measure_skips_delta_table(tmp_path):
    settings_path = _write_settings(tmp_path, _bare_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--upgrade", str(data_path),
            "--no-measure",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Baseline -> Upgraded Delta" not in result.stdout


def test_measure_prints_delta_table_with_clusters_row(tmp_path):
    settings_path = _write_settings(tmp_path, _bare_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--upgrade", str(data_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Baseline -> Upgraded Delta" in result.stdout
    assert "clusters" in result.stdout


# ── 6. --sample-cap / --id-column threaded to upgrade_splink_conversion ─────


def test_sample_cap_and_id_column_threaded(tmp_path, monkeypatch):
    import goldenmatch.config.splink_upgrade as splink_upgrade_mod
    from goldenmatch.config.from_splink import ConversionReport

    settings_path = _write_settings(tmp_path, _bare_settings())
    data_path = _write_parquet(tmp_path, _upgrade_df())
    out_path = tmp_path / "out.yaml"

    captured: dict = {}

    def _fake_upgrade(conversion, data, **kwargs):
        captured.update(kwargs)
        captured["data"] = data
        return splink_upgrade_mod.MigrationResult(
            baseline_config=conversion.config,
            upgraded_config=conversion.config,
            em_model=conversion.em_model,
            report=ConversionReport(),
            measurement=None,
        )

    monkeypatch.setattr(splink_upgrade_mod, "upgrade_splink_conversion", _fake_upgrade)

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--upgrade", str(data_path),
            "--sample-cap", "10",
            "--id-column", "unique_id",
            "--no-measure",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["sample_cap"] == 10
    assert captured["id_column"] == "unique_id"


# ── 7. Regression: non-upgrade stdout stays byte-identical to pre-refactor ──
# (Complements the full, unmodified tests/test_cli_import_splink.py file --
# run alongside this file in CI/pre-push.)


def test_non_upgrade_combined_line_after_table(tmp_path):
    """The pre---upgrade CLI printed ONE combined line, AFTER the findings
    table: 'Wrote config to <path>. <summary>'. Pin that exact content and
    position (the --upgrade refactor must not perturb the legacy path).

    Uses a short relative output path inside isolated_filesystem: rich
    word-wraps at 80 cols and splits long tmp paths mid-word, which would
    make exact-content matching depend on the wrap point.
    """
    settings = _bare_settings()
    # Cross-column condition -> recognize_level() rejects it -> warning
    # finding -> a findings table is guaranteed rendered.
    settings["comparisons"][0]["comparison_levels"].insert(
        3,
        {
            "sql_condition": (
                'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85'
            )
        },
    )

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_settings(Path("."), settings)

        result = runner.invoke(
            app, ["import-splink", "settings.json", "-o", "out.yaml"]
        )

        assert result.exit_code == 0, result.stdout

        # Normalize rich's soft wrapping before matching content.
        normalized = " ".join(result.stdout.split())
        table_idx = normalized.find("Splink Conversion Findings")
        assert table_idx != -1

        # Exact combined-line content: path + summary in ONE line...
        m = re.search(
            r"Wrote config to out\.yaml\. "
            r"\d+ error\(s\), \d+ warning\(s\), \d+ info note\(s\)",
            normalized,
        )
        assert m is not None, normalized
        # ... appearing AFTER the findings-table marker.
        assert m.start() > table_idx
        # And the summary is not printed anywhere on its own BEFORE the
        # combined line (pre-refactor printed it exactly once, combined).
        assert normalized.count("error(s)") == 1


# ── 8. Regression: the upgraded YAML survives a load_config round-trip when
# the fan_out lever fired (NE fields + tuned max_cluster_size) ───────────────


def test_upgraded_yaml_with_fanout_output_reloads(tmp_path):
    """Wild-bench regression: the fan_out guard lever writes
    ``golden_rules.max_cluster_size`` into the upgraded YAML, but
    ``loader._normalize_golden_rules`` used to sweep any key outside
    ``_GOLDEN_RULES_SPECIAL_KEYS`` into ``field_rules`` (the key was omitted
    on a now-stale "set programmatically, never via YAML" assumption), so
    reloading the CLI's own output failed schema validation
    (``field_rules.max_cluster_size ... should be ... GoldenFieldRule``).

    Reuses the Task F6 homonym fixture (tests/test_splink_upgrade_fanout_e2e)
    so the fan_out lever genuinely fires end-to-end through the CLI: NE on
    phone AND guard tuning from --labels. --no-measure skips the two dedupe
    runs -- the lever outputs (not the measurement) are what must round-trip.
    Doubles as the CLI-level NE YAML regression test: the written matchkey's
    negative_evidence must survive the reload in EM-learned shape.
    """
    from goldenmatch.config.loader import load_config

    from tests.test_splink_upgrade_fanout_e2e import (
        _build_fixture,
    )
    from tests.test_splink_upgrade_fanout_e2e import (
        _trained_settings as _fanout_trained_settings,
    )

    settings_path = _write_settings(tmp_path, _fanout_trained_settings())
    df, labels = _build_fixture()
    data_path = _write_parquet(tmp_path, df)
    labels_path = _write_parquet(tmp_path, labels, name="labels.parquet")
    out_path = tmp_path / "out.yaml"
    model_path = tmp_path / "model.json"

    result = runner.invoke(
        app,
        [
            "import-splink", str(settings_path),
            "-o", str(out_path),
            "--model-out", str(model_path),
            "--upgrade", str(data_path),
            "--labels", str(labels_path),
            "--id-column", "rec_id",
            "--no-measure",
        ],
    )
    assert result.exit_code == 0, result.stdout

    # Sanity: the lever really fired -- the raw YAML carries both outputs.
    raw = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert raw["golden_rules"]["max_cluster_size"] == 10
    assert raw["matchkeys"][0]["negative_evidence"][0]["field"] == "phone"

    # THE regression: the CLI's own output must reload through load_config.
    cfg = load_config(str(out_path))
    gr = cfg.golden_rules
    assert gr is not None
    assert gr.max_cluster_size == 10  # tuned value, NOT swept into field_rules
    assert "max_cluster_size" not in (gr.field_rules or {})

    ne = cfg.get_matchkeys()[0].negative_evidence
    assert ne, "negative_evidence did not survive the YAML round-trip"
    assert [n.field for n in ne] == ["phone"]
    assert ne[0].penalty is None and ne[0].penalty_bits is None  # EM-learned
