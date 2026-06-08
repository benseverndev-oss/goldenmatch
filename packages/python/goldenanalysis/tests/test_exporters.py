"""Report exporters: JSON round-trip, Markdown, Parquet (+ table sidecars)."""

from __future__ import annotations

from pathlib import Path

import goldenanalysis as ga
import polars as pl
from fixtures import build_customers_small


def _report() -> ga.AnalysisReport:
    return ga.analyze(build_customers_small(), analyzers=["frame.summary"], dataset="customers")


def test_json_roundtrip() -> None:
    report = _report()
    again = ga.AnalysisReport.from_json(report.to_json())
    assert again == report


def test_json_writes_file(tmp_path: Path) -> None:
    report = _report()
    out = tmp_path / "report.json"
    text = report.to_json(out)
    assert out.read_text(encoding="utf-8") == text
    assert ga.AnalysisReport.from_json(out.read_text(encoding="utf-8")) == report


def test_markdown_contains_header_and_keys() -> None:
    report = _report()
    md = report.to_markdown()
    assert "| Metric | Value |" in md
    for m in report.metrics:
        assert m.key in md


def test_parquet_one_row_per_metric(tmp_path: Path) -> None:
    report = _report()
    out = tmp_path / "report.parquet"
    report.to_parquet(out)

    frame = pl.read_parquet(out)
    assert frame.columns == ["key", "value", "unit", "direction"]
    assert frame.height == len(report.metrics)

    # per_column table written as a sidecar
    sidecar = out.with_name("report.parquet.per_column.parquet")
    assert sidecar.exists()
    assert pl.read_parquet(sidecar).height == 4
