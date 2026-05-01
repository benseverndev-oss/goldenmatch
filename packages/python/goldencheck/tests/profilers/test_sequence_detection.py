"""Tests for SequenceDetectionProfiler."""
import polars as pl
from goldencheck.profilers.sequence_detection import SequenceDetectionProfiler


def test_gap_detected():
    df = pl.DataFrame({"record_num": [1, 2, 3, 5, 6, 7, 10, 11]})  # gaps at 4, 8, 9
    findings = SequenceDetectionProfiler().profile(df, "record_num")
    assert any("gap" in f.message.lower() or "sequence" in f.message.lower() for f in findings)


def test_no_gaps_no_finding():
    df = pl.DataFrame({"id": list(range(1, 101))})
    findings = SequenceDetectionProfiler().profile(df, "id")
    gap_findings = [f for f in findings if f.check == "sequence_detection"]
    assert len(gap_findings) == 0


def test_non_sequential_column_skipped():
    df = pl.DataFrame({"price": [99, 15, 42, 7, 200]})
    findings = SequenceDetectionProfiler().profile(df, "price")
    assert len(findings) == 0


def test_string_column_skipped():
    df = pl.DataFrame({"name": ["Alice", "Bob"]})
    findings = SequenceDetectionProfiler().profile(df, "name")
    assert len(findings) == 0


def test_gap_count_correct():
    # Gaps: 4, 8, 9 — three missing values
    df = pl.DataFrame({"record_num": [1, 2, 3, 5, 6, 7, 10, 11]})
    findings = SequenceDetectionProfiler().profile(df, "record_num")
    assert findings[0].affected_rows == 3


def test_confidence_is_0_7():
    df = pl.DataFrame({"record_num": [1, 2, 3, 5, 6, 7]})
    findings = SequenceDetectionProfiler().profile(df, "record_num")
    assert all(f.confidence == 0.7 for f in findings)


def test_check_name():
    df = pl.DataFrame({"record_num": [1, 2, 3, 5, 6, 7]})
    findings = SequenceDetectionProfiler().profile(df, "record_num")
    assert all(f.check == "sequence_detection" for f in findings)
