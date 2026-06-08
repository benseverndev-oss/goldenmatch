"""Analyzer registry: entry-point discovery + fallback."""

from __future__ import annotations

from goldenanalysis.registry import available_analyzers, load_analyzer


def test_load_frame_summary() -> None:
    analyzer = load_analyzer("frame.summary")
    assert analyzer.info.name == "frame.summary"
    assert "frame" in analyzer.info.consumes


def test_available_includes_frame_summary() -> None:
    assert "frame.summary" in available_analyzers()


def test_unknown_analyzer_raises() -> None:
    import pytest

    with pytest.raises(KeyError):
        load_analyzer("does.not.exist")
