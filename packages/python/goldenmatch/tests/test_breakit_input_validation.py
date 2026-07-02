"""Break-it review fixes: silent input-validation gaps.

B1 non-UTF-8 CSV no longer silently corrupts accented names; B2 anomaly
sensitivity is validated + case-normalized; B3 an unknown --backend is rejected;
and --format is validated at parse time (not after the whole run).
"""
from __future__ import annotations

import logging

import polars as pl
import pytest
from typer.testing import CliRunner

runner = CliRunner()

# "José Muñoz" / "Björk" via code points, so this test file's own encoding
# can't confuse the fixture bytes.
_JOSE = "José Muñoz"
_BJORK = "Björk"


# ── B1: cp1252 CSV decoded, not mangled ─────────────────────────────────────

def test_cp1252_csv_decoded_not_mangled(tmp_path, caplog):
    from goldenmatch.core.ingest import load_file

    p = tmp_path / "legacy.csv"
    p.write_bytes(f"name\n{_JOSE}\n".encode("cp1252"))

    with caplog.at_level(logging.WARNING):
        df = load_file(p).collect()

    assert df["name"].to_list() == [_JOSE]           # not "Jos� Mu�..."
    assert "�" not in df["name"][0]             # no replacement chars
    assert any("not valid UTF-8" in r.message for r in caplog.records)


def test_valid_utf8_csv_unchanged(tmp_path):
    from goldenmatch.core.ingest import load_file

    p = tmp_path / "u.csv"
    p.write_text(f"name\n{_JOSE}\n", encoding="utf-8")
    assert load_file(p).collect()["name"].to_list() == [_JOSE]


def test_explicit_nonpolars_encoding_works(tmp_path):
    from goldenmatch.core.ingest import load_file

    p = tmp_path / "e.csv"
    p.write_bytes(f"name\n{_BJORK}\n".encode("cp1252"))
    # scan_csv can't take "cp1252"; the explicit branch decodes via the codec.
    assert load_file(p, encoding="cp1252").collect()["name"].to_list() == [_BJORK]


# ── B2: anomaly sensitivity validated + normalized ──────────────────────────

def _anom_df() -> pl.DataFrame:
    return pl.DataFrame({"__row_id__": [0, 1], "email": ["test@test.com", "real@x.com"]})


def test_anomaly_sensitivity_miscased_behaves_as_low():
    from goldenmatch.core.anomaly import detect_anomalies

    # "Low" must filter like "low", NOT fall through to the no-filter (high) path.
    miscased = detect_anomalies(_anom_df(), "Low")
    proper = detect_anomalies(_anom_df(), "low")
    assert [a["type"] for a in miscased] == [a["type"] for a in proper]


def test_anomaly_sensitivity_invalid_raises():
    from goldenmatch.core.anomaly import detect_anomalies

    with pytest.raises(ValueError, match="sensitivity"):
        detect_anomalies(_anom_df(), "bogus")


# ── B3 + --format: CLI rejects unknown values at parse time ─────────────────

def _tiny_csv(tmp_path) -> str:
    p = tmp_path / "d.csv"
    p.write_text("name,email\na,a@x.com\nb,b@y.com\n", encoding="utf-8")
    return str(p)


def test_cli_unknown_backend_rejected(tmp_path):
    from goldenmatch.cli.main import app

    r = runner.invoke(app, ["dedupe", _tiny_csv(tmp_path), "--backend", "not_a_backend"])
    assert r.exit_code != 0
    assert "Unknown backend" in r.output


def test_cli_valid_backend_accepted(tmp_path):
    from goldenmatch.cli.main import app

    # A known backend must NOT be rejected by the validator (it may still fail
    # later for other reasons, but never with our "Unknown backend" message).
    r = runner.invoke(app, ["dedupe", _tiny_csv(tmp_path), "--backend", "bucket", "--preview"])
    assert "Unknown backend" not in r.output


def test_cli_unknown_format_rejected_at_parse_time(tmp_path):
    from goldenmatch.cli.main import app

    r = runner.invoke(app, ["dedupe", _tiny_csv(tmp_path), "--format", "xml"])
    assert r.exit_code != 0
    assert "Unsupported output format" in r.output


# ── watch-list: structured non-tabular input + non-positive size flags ──────

def test_cli_json_input_rejected_with_helpful_message(tmp_path):
    from goldenmatch.cli.main import app

    p = tmp_path / "data.json"
    p.write_text('[{"name": "a"}]', encoding="utf-8")
    r = runner.invoke(app, ["dedupe", str(p)])
    assert r.exit_code != 0
    assert "non-tabular" in r.output and ".json" in r.output


def test_cli_negative_chunk_size_rejected(tmp_path):
    from goldenmatch.cli.main import app

    r = runner.invoke(app, ["dedupe", _tiny_csv(tmp_path), "--chunk-size", "-1"])
    assert r.exit_code != 0
    assert "--chunk-size must be >= 1" in r.output


def test_cli_zero_preview_size_rejected(tmp_path):
    from goldenmatch.cli.main import app

    r = runner.invoke(app, ["dedupe", _tiny_csv(tmp_path), "--preview-size", "0", "--preview"])
    assert r.exit_code != 0
    assert "--preview-size must be >= 1" in r.output
