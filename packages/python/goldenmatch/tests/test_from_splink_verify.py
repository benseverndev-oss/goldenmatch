"""Auto-verify a Splink -> GoldenMatch conversion against real Splink.

`verify_against_splink` runs BOTH the original Splink settings (when splink is
installed) and the converted config on a sample, then reports pairwise cluster
agreement. splink is an OPTIONAL dependency, so:

- the graceful-degradation + agreement-assembly paths are tested WITHOUT splink
  (patching the two engine-run helpers), so they run in the normal CI lane;
- one real end-to-end test is guarded by ``importorskip("splink")`` and only
  fires in an environment that has splink + pandas.
"""
from __future__ import annotations

import json

import polars as pl
import pytest
from goldenmatch.cli.main import app
from goldenmatch.config import splink_verify
from goldenmatch.config.from_splink import ConversionReport, from_splink
from goldenmatch.config.splink_verify import (
    SplinkVerification,
    verify_against_splink,
)
from typer.testing import CliRunner

runner = CliRunner()

_DF = pl.DataFrame(
    {
        "unique_id": list(range(6)),
        "first_name": ["john", "jon", "alice", "alicia", "bob", "bob"],
        "surname": ["smith", "smith", "jones", "jones", "lee", "lee"],
    }
)

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
                {"sql_condition": 'jaro_winkler_similarity("first_name_l","first_name_r") >= 0.8'},
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


def _converted():
    return from_splink(_SETTINGS)


# ── graceful degradation (no splink) ─────────────────────────────────────────


def test_returns_none_when_splink_absent(monkeypatch) -> None:
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: False)
    report = ConversionReport()
    result = verify_against_splink(
        _SETTINGS, _DF, _converted().config, report=report
    )
    assert result is None
    assert any(
        f.splink_path == "verify" and "not installed" in f.message
        for f in report.findings
    )


def test_returns_none_on_empty_data(monkeypatch) -> None:
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: True)
    report = ConversionReport()
    empty = _DF.head(0)
    result = verify_against_splink(
        _SETTINGS, empty, _converted().config, report=report
    )
    assert result is None
    assert any(f.splink_path == "verify" and "empty" in f.message for f in report.findings)


def test_soft_fails_when_splink_run_raises(monkeypatch) -> None:
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: True)

    def _boom(*_a, **_k):
        raise RuntimeError("duckdb cannot run spark SQL")

    monkeypatch.setattr(splink_verify, "_run_splink", _boom)
    report = ConversionReport()
    result = verify_against_splink(
        _SETTINGS, _DF, _converted().config, report=report
    )
    assert result is None
    assert any(
        f.splink_path == "verify" and "could not run the original Splink" in f.message
        for f in report.findings
    )


# ── agreement assembly (patched engine runs, no splink needed) ───────────────


def test_perfect_agreement_assembly(monkeypatch) -> None:
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: True)
    # ids resolve from the `unique_id` column -> "0".."5". Both engines cluster
    # {0,1}, {2,3}, {4,5} identically.
    same = {"0": "a", "1": "a", "2": "b", "3": "b", "4": "c", "5": "c"}
    monkeypatch.setattr(splink_verify, "_run_splink", lambda *a, **k: (dict(same), "4.0.16"))
    monkeypatch.setattr(splink_verify, "_run_goldenmatch", lambda *a, **k: dict(same))

    report = ConversionReport()
    result = verify_against_splink(
        _SETTINGS, _DF, _converted().config, report=report
    )
    assert isinstance(result, SplinkVerification)
    assert result.agreement == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert result.is_faithful
    assert result.n_shared_ids == 6
    assert result.gm_multi_clusters == 3
    assert result.splink_multi_clusters == 3
    assert result.splink_version == "4.0.16"


def test_divergent_agreement_is_flagged(monkeypatch) -> None:
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: True)
    # Splink links all three pairs; GoldenMatch links none -> recall 0.
    splink_map = {"0": "a", "1": "a", "2": "b", "3": "b", "4": "c", "5": "c"}
    gm_map = {str(i): f"solo{i}" for i in range(6)}
    monkeypatch.setattr(splink_verify, "_run_splink", lambda *a, **k: (splink_map, "4.0.16"))
    monkeypatch.setattr(splink_verify, "_run_goldenmatch", lambda *a, **k: gm_map)

    report = ConversionReport()
    result = verify_against_splink(
        _SETTINGS, _DF, _converted().config, report=report
    )
    assert result is not None
    assert result.agreement["recall"] == 0.0
    assert not result.is_faithful
    # a divergent verdict is surfaced as a warning finding
    assert any(
        f.splink_path == "verify" and "DIVERGENT" in f.message for f in report.findings
    )


def test_partial_agreement_math(monkeypatch) -> None:
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: True)
    # Splink pairs: (0,1),(2,3),(4,5). GoldenMatch pairs: (0,1),(2,3) only.
    splink_map = {"0": "a", "1": "a", "2": "b", "3": "b", "4": "c", "5": "c"}
    gm_map = {"0": "a", "1": "a", "2": "b", "3": "b", "4": "s4", "5": "s5"}
    monkeypatch.setattr(splink_verify, "_run_splink", lambda *a, **k: (splink_map, "4.0.16"))
    monkeypatch.setattr(splink_verify, "_run_goldenmatch", lambda *a, **k: gm_map)

    result = verify_against_splink(_SETTINGS, _DF, _converted().config)
    assert result is not None
    # 2 of GM's 2 pairs are correct -> precision 1.0; 2 of Splink's 3 -> recall 2/3
    assert result.agreement["precision"] == 1.0
    assert result.agreement["recall"] == pytest.approx(2 / 3)


def test_is_faithful_threshold() -> None:
    hi = SplinkVerification(
        agreement={"precision": 1.0, "recall": 0.95, "f1": 0.95},
        n_records=10, n_shared_ids=10, gm_cluster_count=2, splink_cluster_count=2,
        gm_multi_clusters=1, splink_multi_clusters=1, match_threshold=0.5,
        splink_version="4.0.16", id_source="unique_id",
    )
    lo = SplinkVerification(
        agreement={"precision": 1.0, "recall": 0.5, "f1": 0.6},
        n_records=10, n_shared_ids=10, gm_cluster_count=2, splink_cluster_count=2,
        gm_multi_clusters=1, splink_multi_clusters=1, match_threshold=0.5,
        splink_version="4.0.16", id_source="unique_id",
    )
    assert hi.is_faithful
    assert not lo.is_faithful


# ── real end-to-end (needs splink) ───────────────────────────────────────────


# A TRAINED settings dict (m/u on every non-null level). Verification is
# apples-to-apples only on a trained model -- both engines then score with the
# imported weights. A BARE model legitimately diverges (Splink uses untrained
# neutral weights; GoldenMatch's zero-config trains EM on the sample), so the
# real end-to-end faithful-agreement assertion needs trained input.
_TRAINED_SETTINGS = {
    "link_type": "dedupe_only",
    "unique_id_column_name": "unique_id",
    "comparisons": [
        {
            "output_column_name": "first_name",
            "comparison_levels": [
                {"sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                 "is_null_level": True},
                {"sql_condition": '"first_name_l" = "first_name_r"',
                 "m_probability": 0.7, "u_probability": 0.02},
                {"sql_condition": 'jaro_winkler_similarity("first_name_l","first_name_r") >= 0.8',
                 "m_probability": 0.25, "u_probability": 0.05},
                {"sql_condition": "ELSE", "m_probability": 0.05, "u_probability": 0.93},
            ],
        },
        {
            "output_column_name": "surname",
            "comparison_levels": [
                {"sql_condition": '"surname_l" IS NULL OR "surname_r" IS NULL',
                 "is_null_level": True},
                {"sql_condition": '"surname_l" = "surname_r"',
                 "m_probability": 0.9, "u_probability": 0.1},
                {"sql_condition": "ELSE", "m_probability": 0.1, "u_probability": 0.9},
            ],
        },
    ],
    "blocking_rules_to_generate_predictions": ["l.surname = r.surname"],
    "probability_two_random_records_match": 0.1,
}


def test_end_to_end_agreement_with_real_splink() -> None:
    pytest.importorskip("splink")
    pytest.importorskip("pandas")

    conversion = from_splink(_TRAINED_SETTINGS)
    report = ConversionReport()
    result = verify_against_splink(
        _TRAINED_SETTINGS,
        _DF,
        conversion.config,
        em_model=conversion.em_model,
        sample_size=1000,
        report=report,
    )
    assert result is not None
    # On this clean, clearly-separable data both trained engines must agree.
    assert result.is_faithful
    assert result.agreement["f1"] >= 0.95
    assert result.splink_version


# ── CLI wiring (--verify degrades gracefully when splink is absent) ───────────


def test_cli_verify_flag_degrades_when_splink_absent(tmp_path, monkeypatch) -> None:
    # Force the splink-absent path so this runs in the normal (splink-free) lane.
    monkeypatch.setattr(splink_verify, "_splink_available", lambda: False)

    settings_path = tmp_path / "model.json"
    settings_path.write_text(json.dumps(_SETTINGS))
    data_path = tmp_path / "data.csv"
    _DF.write_csv(data_path)
    out_path = tmp_path / "config.yaml"

    result = runner.invoke(
        app,
        [
            "import-splink",
            str(settings_path),
            "-o",
            str(out_path),
            "--verify",
            str(data_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # config is still written, and the verify skip is surfaced (not a crash)
    assert out_path.exists()
    assert "verification skipped" in result.output.lower()
