"""Unit tests for the qis-gate PURE assertion logic. No goldenmatch, no scale
run -- exercises evaluate_gate() on synthetic per-rung F1. Run:
    python -m pytest scripts/test_qis_gate.py -q"""
import importlib.util
import pathlib
import sys

_spec = importlib.util.spec_from_file_location(
    "qis_gate", pathlib.Path(__file__).parent / "qis_gate.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


def _f1(**kw):
    return dict(kw)


def test_all_healthy_no_violations():
    # Flat, high F1 across scale, no baseline -> clean.
    r = mod.evaluate_gate({50_000: 0.97, 100_000: 0.97, 500_000: 0.96, 1_000_000: 0.965}, None)
    assert r.ok
    assert r.reference_n == 50_000


def test_scale_specific_regression_trips_invariance():
    # THE bug class: good at small scale, craters at 500K+. No baseline needed.
    r = mod.evaluate_gate({50_000: 0.97, 100_000: 0.96, 500_000: 0.55, 1_000_000: 0.50}, None)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (500_000, "scale_invariance") in checks
    assert (1_000_000, "scale_invariance") in checks
    # 500K/1M also breach the absolute floor (0.80 default).
    assert (500_000, "absolute_floor") in checks
    assert not r.ok


def test_uniform_decay_caught_by_floor_and_baseline_not_invariance():
    # Everything equally bad -> scale-invariance is satisfied (flat), but the
    # absolute floor and the baseline-delta catch it.
    baseline = {"50000": 0.97, "100000": 0.97, "500000": 0.96}
    r = mod.evaluate_gate({50_000: 0.60, 100_000: 0.60, 500_000: 0.60}, baseline)
    checks = {(v.rung, v.check) for v in r.violations}
    assert not any(c == "scale_invariance" for _, c in checks)  # flat => invariant
    assert (50_000, "absolute_floor") in checks
    assert (50_000, "baseline_delta") in checks


def test_baseline_drift_within_floor_still_caught():
    # F1 stays above the absolute floor and is flat (invariant) but has drifted
    # meaningfully below the committed baseline -> baseline_delta fires.
    baseline = {"50000": 0.970, "100000": 0.970}
    r = mod.evaluate_gate({50_000: 0.900, 100_000: 0.900}, baseline,
                          abs_floor=0.80, delta_tol=0.02, scale_tol=0.03)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (50_000, "baseline_delta") in checks
    assert not any(c == "absolute_floor" for _, c in checks)


def test_small_dips_within_tolerance_pass():
    # A 0.02 dip at scale is within the 0.03 scale-tol and 0.02 delta-tol.
    baseline = {"50000": 0.97, "100000": 0.97, "500000": 0.97}
    r = mod.evaluate_gate({50_000: 0.97, 100_000: 0.96, 500_000: 0.95}, baseline)
    assert r.ok, [v.line() for v in r.violations]


def test_missing_baseline_rung_skips_delta_only():
    # Baseline has no entry for 500K (e.g. matrix grew) -> delta check skipped for
    # it, but invariance + floor still apply.
    baseline = {"50000": 0.97}
    r = mod.evaluate_gate({50_000: 0.97, 500_000: 0.50}, baseline)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (500_000, "scale_invariance") in checks
    assert (500_000, "absolute_floor") in checks
    assert not any(rung == 500_000 and c == "baseline_delta" for rung, c in checks)


def test_refuse_at_scale_is_a_violation():
    # THE observed live signature: confident/high F1 at <=50K, REFUSES (RED) at
    # 100K+. Refused rungs carry f1=None + refused=True; no force-run.
    rung_f1 = {50_000: 0.998, 100_000: None, 500_000: None}
    refused = {50_000: False, 100_000: True, 500_000: True}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (100_000, "scale_invariance") in checks
    assert (500_000, "scale_invariance") in checks
    assert not r.ok
    # The message names the refuse explicitly.
    assert any("REFUSED" in v.detail for v in r.violations)


def test_reference_rung_refusing_is_flagged():
    # If even the smallest gated rung refuses, there's no confident reference.
    r = mod.evaluate_gate({50_000: None, 100_000: None}, None,
                          rung_refused={50_000: True, 100_000: True})
    checks = {(v.rung, v.check) for v in r.violations}
    assert (50_000, "scale_invariance") in checks
    assert not r.ok


def test_confident_rungs_still_scored_when_a_later_rung_refuses():
    # A refused rung must not suppress F1 checks on the confident ones.
    rung_f1 = {50_000: 0.55, 100_000: None}
    refused = {50_000: False, 100_000: True}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (50_000, "absolute_floor") in checks       # 0.55 < 0.80 floor
    assert (100_000, "scale_invariance") in checks     # refused vs GREEN reference


def test_empty_measurements_raises():
    try:
        mod.evaluate_gate({}, None)
    except ValueError:
        return
    raise AssertionError("expected ValueError on empty measurements")
