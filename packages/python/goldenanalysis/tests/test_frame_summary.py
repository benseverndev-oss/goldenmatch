"""``frame.summary`` exact-value contract test (Appendix A)."""

from __future__ import annotations

import polars as pl

from fixtures import build_customers_small, ensure_customers_small

from goldenanalysis.adapters import FrameArtifactAdapter
from goldenanalysis.analyzers.frame_summary import FrameSummaryAnalyzer


def _metrics(df: pl.DataFrame):
    inp = FrameArtifactAdapter().load(df, dataset="customers")
    result = FrameSummaryAnalyzer().run(inp)
    return {m.key: m for m in result.metrics}, result


def test_frame_summary_exact_values() -> None:
    # Read the committed parquet (path anchored to __file__ via the fixtures pkg).
    path = ensure_customers_small()
    df = pl.read_parquet(path)
    metrics, _ = _metrics(df)

    assert metrics["frame.row_count"].value == 20
    assert metrics["frame.column_count"].value == 4
    assert metrics["frame.null_ratio_mean"].value == 0.275
    assert metrics["frame.duplicate_row_ratio"].value == 0.1
    assert isinstance(metrics["frame.memory_bytes"].value, int)
    assert metrics["frame.memory_bytes"].value > 0


def test_frame_summary_directions() -> None:
    df = build_customers_small()
    metrics, _ = _metrics(df)
    assert metrics["frame.row_count"].direction == "neutral"
    assert metrics["frame.column_count"].direction == "neutral"
    assert metrics["frame.null_ratio_mean"].direction == "lower_better"
    assert metrics["frame.duplicate_row_ratio"].direction == "lower_better"
    assert metrics["frame.memory_bytes"].direction == "neutral"


def test_frame_summary_per_column_table() -> None:
    df = build_customers_small()
    _, result = _metrics(df)
    tables = {t.name: t for t in result.tables}
    assert "per_column" in tables
    pc = tables["per_column"]
    assert pc.columns == ["column", "dtype", "null_ratio", "n_unique"]
    assert len(pc.rows) == 4  # one row per column
