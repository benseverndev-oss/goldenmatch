"""Tests for DriftDetectionProfiler."""
import polars as pl
from goldencheck.profilers.drift_detection import DriftDetectionProfiler


def test_categorical_drift_detected():
    values = ["A", "B", "C"] * 500 + ["A", "B", "C", "D", "E"] * 100
    df = pl.DataFrame({"category": values})
    findings = DriftDetectionProfiler().profile(df, "category")
    assert any("drift" in f.message.lower() or "new" in f.message.lower() for f in findings)


def test_no_drift_no_finding():
    df = pl.DataFrame({"status": ["active", "inactive"] * 1000})
    findings = DriftDetectionProfiler().profile(df, "status")
    drift_findings = [f for f in findings if f.check == "drift_detection"]
    assert len(drift_findings) == 0


def test_small_dataset_skipped():
    df = pl.DataFrame({"x": ["A", "B", "C"] * 10})
    findings = DriftDetectionProfiler().profile(df, "x")
    assert len(findings) == 0


def test_numeric_drift_detected():
    import random
    random.seed(42)
    # First half: mean ~0, second half: mean ~100 (clear drift)
    first = [float(i % 5) for i in range(500)]
    second = [float(100 + i % 5) for i in range(500)]
    df = pl.DataFrame({"value": first + second})
    findings = DriftDetectionProfiler().profile(df, "value")
    assert any("drift" in f.message.lower() or "shift" in f.message.lower() for f in findings)


def test_numeric_no_drift():
    # Uniform data with no drift
    values = [float(i % 10) for i in range(2000)]
    df = pl.DataFrame({"score": values})
    findings = DriftDetectionProfiler().profile(df, "score")
    drift_findings = [f for f in findings if f.check == "drift_detection"]
    assert len(drift_findings) == 0


def test_drift_confidence():
    values = ["A", "B", "C"] * 500 + ["A", "B", "C", "D", "E"] * 100
    df = pl.DataFrame({"category": values})
    findings = DriftDetectionProfiler().profile(df, "category")
    drift_findings = [f for f in findings if f.check == "drift_detection"]
    assert all(f.confidence == 0.6 for f in drift_findings)


def test_check_name():
    values = ["A", "B", "C"] * 500 + ["A", "B", "C", "D", "E"] * 100
    df = pl.DataFrame({"category": values})
    findings = DriftDetectionProfiler().profile(df, "category")
    assert all(f.check == "drift_detection" for f in findings)
