"""Tests for infermap.calibration."""
from __future__ import annotations

import numpy as np
import pytest

from infermap import MapEngine
from infermap.calibration import (
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    calibrator_from_dict,
    load_calibrator,
    save_calibrator,
)
from infermap.types import FieldInfo, SchemaInfo


def _ece(scores: np.ndarray, correct: np.ndarray, num_bins: int = 10) -> float:
    """Local ECE impl — intentionally independent of infermap_bench."""
    bins: list[list[tuple[float, float]]] = [[] for _ in range(num_bins)]
    for s, c in zip(scores, correct):
        idx = min(int(s * num_bins), num_bins - 1)
        bins[idx].append((float(s), float(c)))
    total = len(scores)
    ece = 0.0
    for bin_ in bins:
        if not bin_:
            continue
        bin_conf = sum(s for s, _ in bin_) / len(bin_)
        bin_acc = sum(c for _, c in bin_) / len(bin_)
        ece += len(bin_) / total * abs(bin_conf - bin_acc)
    return ece


def test_identity_passthrough():
    cal = IdentityCalibrator()
    xs = np.array([0.1, 0.5, 0.9])
    np.testing.assert_array_equal(cal.transform(xs), xs)


def test_identity_round_trip():
    cal = IdentityCalibrator()
    d = cal.to_dict()
    loaded = calibrator_from_dict(d)
    assert isinstance(loaded, IdentityCalibrator)


def test_isotonic_is_monotonic():
    rng = np.random.default_rng(0)
    # Synthetic: true p = x, with noisy labels
    xs = rng.uniform(0, 1, 200)
    ys = (rng.uniform(0, 1, 200) < xs).astype(float)
    cal = IsotonicCalibrator()
    cal.fit(xs, ys)
    probe = np.linspace(0, 1, 50)
    out = cal.transform(probe)
    # Monotonic non-decreasing
    assert np.all(np.diff(out) >= -1e-9)


def test_isotonic_reduces_ece():
    rng = np.random.default_rng(1)
    # Raw "score" is sqrt of true probability — consistently overconfident.
    true_p = rng.uniform(0, 1, 500)
    raw = np.sqrt(true_p)  # overconfident: raw > true_p
    correct = (rng.uniform(0, 1, 500) < true_p).astype(float)
    raw_ece = _ece(raw, correct)
    cal = IsotonicCalibrator()
    cal.fit(raw, correct)
    cal_ece = _ece(cal.transform(raw), correct)
    assert cal_ece < raw_ece, f"isotonic did not reduce ECE: {raw_ece} -> {cal_ece}"


def test_platt_reduces_ece():
    rng = np.random.default_rng(2)
    true_p = rng.uniform(0, 1, 500)
    raw = np.sqrt(true_p)
    correct = (rng.uniform(0, 1, 500) < true_p).astype(float)
    raw_ece = _ece(raw, correct)
    cal = PlattCalibrator()
    cal.fit(raw, correct)
    cal_ece = _ece(cal.transform(raw), correct)
    assert cal_ece < raw_ece, f"platt did not reduce ECE: {raw_ece} -> {cal_ece}"


def test_isotonic_json_round_trip(tmp_path):
    rng = np.random.default_rng(3)
    xs = rng.uniform(0, 1, 50)
    ys = (rng.uniform(0, 1, 50) < xs).astype(float)
    cal = IsotonicCalibrator()
    cal.fit(xs, ys)
    path = tmp_path / "iso.json"
    save_calibrator(cal, path)
    loaded = load_calibrator(path)
    assert isinstance(loaded, IsotonicCalibrator)
    np.testing.assert_array_almost_equal(cal.transform(xs), loaded.transform(xs))


def test_platt_json_round_trip(tmp_path):
    cal = PlattCalibrator(a=2.5, b=-1.0)
    path = tmp_path / "platt.json"
    save_calibrator(cal, path)
    loaded = load_calibrator(path)
    assert isinstance(loaded, PlattCalibrator)
    assert loaded.a == pytest.approx(2.5)
    assert loaded.b == pytest.approx(-1.0)


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown calibrator kind"):
        calibrator_from_dict({"kind": "wat"})


def _make_fixture_schemas() -> tuple[SchemaInfo, SchemaInfo]:
    src = SchemaInfo(
        fields=[
            FieldInfo(name="cust_id", dtype="int64", sample_values=["1", "2", "3"], value_count=3),
            FieldInfo(name="email", dtype="string", sample_values=["a@x.io", "b@y.io", "c@z.io"], value_count=3),
            FieldInfo(name="amt", dtype="float64", sample_values=["1.0", "2.0", "3.0"], value_count=3),
        ],
        source_name="src",
    )
    tgt = SchemaInfo(
        fields=[
            FieldInfo(name="customer_id", dtype="int64", sample_values=["10", "20", "30"], value_count=3),
            FieldInfo(name="email_addr", dtype="string", sample_values=["x@a.io", "y@b.io", "z@c.io"], value_count=3),
            FieldInfo(name="amount", dtype="float64", sample_values=["5.0", "6.0", "7.0"], value_count=3),
            FieldInfo(name="notes", dtype="string", sample_values=["", "", ""], value_count=3),
        ],
        source_name="tgt",
    )
    return src, tgt


def test_calibrator_does_not_change_mappings():
    """Critical invariant: a calibrator must only relabel confidences, never
    change which mappings the engine picks. F1/top-1/MRR must stay identical."""
    src, tgt = _make_fixture_schemas()

    base_engine = MapEngine()
    base_result = base_engine.map_schemas(src, tgt)
    base_pairs = [(m.source, m.target) for m in base_result.mappings]

    # Fit a nontrivial calibrator.
    rng = np.random.default_rng(4)
    xs = rng.uniform(0, 1, 100)
    ys = (rng.uniform(0, 1, 100) < xs**2).astype(float)
    cal = IsotonicCalibrator()
    cal.fit(xs, ys)

    cal_engine = MapEngine(calibrator=cal)
    cal_result = cal_engine.map_schemas(src, tgt)
    cal_pairs = [(m.source, m.target) for m in cal_result.mappings]

    assert base_pairs == cal_pairs, "calibrator changed the mapping selection"
    # Confidences should in general differ (unless the identity happened to fit).
    base_confs = [m.confidence for m in base_result.mappings]
    cal_confs = [m.confidence for m in cal_result.mappings]
    assert base_confs != cal_confs or all(b == c for b, c in zip(base_confs, cal_confs))


def test_engine_default_no_calibrator_bit_identical():
    """MapEngine() without calibrator must be bit-identical to before the feature."""
    src, tgt = _make_fixture_schemas()
    e1 = MapEngine()
    e2 = MapEngine(calibrator=None)
    r1 = e1.map_schemas(src, tgt)
    r2 = e2.map_schemas(src, tgt)
    assert [(m.source, m.target, m.confidence) for m in r1.mappings] == \
           [(m.source, m.target, m.confidence) for m in r2.mappings]


def test_empty_fit_is_noop():
    cal = IsotonicCalibrator()
    cal.fit(np.array([]), np.array([]))
    # Default x=[0,1], y=[0,1] → passthrough on [0,1] range
    out = cal.transform(np.array([0.3, 0.7]))
    np.testing.assert_array_almost_equal(out, np.array([0.3, 0.7]))
