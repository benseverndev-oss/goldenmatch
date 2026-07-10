# tests/test_frame_backend_env.py
"""GOLDENMATCH_FRAME env resolution + the arrow-mode ingest wiring (W1 Task 5).

``resolve_frame_backend()`` (core/frame.py) reads the env var; ``load_file``
(core/ingest.py) routes file reads through pyarrow when it resolves to
"arrow", ONLY for the suffix/parse_mode combos ``io_arrow`` actually covers
(.csv/text-fast-path/.parquet/.xlsx) -- anything that would otherwise fall
through to ``smart_load`` must keep doing so unchanged (io_arrow has no
smart_load-equivalent).
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl
import pytest
from goldenmatch.core.frame import resolve_frame_backend
from goldenmatch.core.ingest import load_file

# --------------------------------------------------------------------------
# resolve_frame_backend()
# --------------------------------------------------------------------------


def test_resolve_frame_backend_default_is_polars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOLDENMATCH_FRAME", raising=False)
    assert resolve_frame_backend() == "polars"


def test_resolve_frame_backend_arrow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    assert resolve_frame_backend() == "arrow"


def test_resolve_frame_backend_explicit_polars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_FRAME", "polars")
    assert resolve_frame_backend() == "polars"


def test_resolve_frame_backend_case_and_whitespace_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOLDENMATCH_FRAME", "  ARROW  ")
    assert resolve_frame_backend() == "arrow"


def test_resolve_frame_backend_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_FRAME", "duckdb")
    with pytest.raises(ValueError, match="duckdb") as excinfo:
        resolve_frame_backend()
    # Names the valid options too.
    assert "polars" in str(excinfo.value)
    assert "arrow" in str(excinfo.value)


# --------------------------------------------------------------------------
# load_file under GOLDENMATCH_FRAME=arrow
# --------------------------------------------------------------------------


def _write_sample_csv(path: Path) -> None:
    path.write_text(
        "id,first_name,last_name,zip\n"
        "1,John,Smith,19382\n"
        "2,Jane,Doe,10001\n"
        "3,Bob,Jones,90210\n",
        encoding="utf-8",
    )


def test_load_file_arrow_mode_csv_matches_polars_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sample.csv"
    _write_sample_csv(path)

    monkeypatch.delenv("GOLDENMATCH_FRAME", raising=False)
    polars_mode_df = load_file(path).collect()

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    arrow_mode_lf = load_file(path)
    assert isinstance(arrow_mode_lf, pl.LazyFrame)
    arrow_mode_df = arrow_mode_lf.collect()

    assert arrow_mode_df.equals(polars_mode_df)


def test_load_file_arrow_mode_logs_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "sample.csv"
    _write_sample_csv(path)

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    with caplog.at_level(logging.INFO, logger="goldenmatch.core.ingest"):
        load_file(path)

    assert any(
        record.levelno == logging.INFO and "arrow" in record.message.lower()
        for record in caplog.records
    )


def test_load_file_arrow_mode_unsupported_route_defers_to_smart_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``.txt`` file with default args (parse_mode='auto', delimiter=None)
    is NOT one of the polars fast-path branches -- it goes through
    ``smart_load`` today, which ``io_arrow`` does not implement. Under
    GOLDENMATCH_FRAME=arrow this must still work (defer silently), not error,
    and must produce output identical to polars mode.
    """
    path = tmp_path / "sample.txt"
    _write_sample_csv(path)

    monkeypatch.delenv("GOLDENMATCH_FRAME", raising=False)
    polars_mode_df = load_file(path).collect()

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    arrow_mode_df = load_file(path).collect()

    assert arrow_mode_df.equals(polars_mode_df)


def test_load_file_default_mode_purity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env var set, load_file's CSV output must be byte-identical to
    today's auto-mode anchor: a direct ``pl.scan_csv(..., encoding="utf8-lossy")``.
    """
    path = tmp_path / "sample.csv"
    _write_sample_csv(path)

    monkeypatch.delenv("GOLDENMATCH_FRAME", raising=False)
    result_df = load_file(path).collect()
    anchor_df = pl.scan_csv(path, encoding="utf8-lossy").collect()

    assert result_df.equals(anchor_df)
