"""Top-level ``analyze()`` entrypoint + report assembly."""

from __future__ import annotations

import goldenanalysis as ga
import polars as pl
from fixtures import build_customers_small


def test_analyze_explicit_analyzer() -> None:
    df = build_customers_small()
    report = ga.analyze(df, analyzers=["frame.summary"])
    assert report.analyzers_run == ["frame.summary"]
    assert report.source["dataset"] == "frame"
    assert report.schema_version == 1
    keys = {m.key for m in report.metrics}
    assert "frame.row_count" in keys


def test_analyze_defaults_to_frame_compatible() -> None:
    df = pl.DataFrame({"a": [1, 2, 3]})
    report = ga.analyze(df)  # no analyzers -> all frame-compatible (frame.summary)
    assert report.analyzers_run == ["frame.summary"]


def test_analyze_named_dataset_in_runid() -> None:
    df = pl.DataFrame({"a": [1]})
    report = ga.analyze(df, analyzers=["frame.summary"], dataset="customers")
    assert report.source["dataset"] == "customers"
    assert report.run_id.endswith("#customers")


def test_analyze_records_unavailable() -> None:
    df = pl.DataFrame({"a": [1]})
    # "does.not.exist" is not a registered analyzer -> recorded as unavailable.
    report = ga.analyze(df, analyzers=["frame.summary", "does.not.exist"])
    assert report.analyzers_run == ["frame.summary"]
    assert "does.not.exist" in report.source["unavailable"]
