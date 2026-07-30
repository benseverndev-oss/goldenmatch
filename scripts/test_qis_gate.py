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


def test_unmeasurable_refuse_at_scale_is_a_violation():
    # Refused AND unmeasurable (the allow_red_config force-run was skipped/errored
    # -- f1=None): confident at <=50K, then no measurable F1 at 100K+. This is the
    # honest-can't-measure case (never a fabricated 0.0), and it IS a violation.
    rung_f1 = {50_000: 0.998, 100_000: None, 500_000: None}
    refused = {50_000: False, 100_000: True, 500_000: True}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (100_000, "scale_invariance") in checks
    assert (500_000, "scale_invariance") in checks
    assert not r.ok
    # The message names the refuse + unmeasurability explicitly.
    assert any("REFUSED" in v.detail and "could not be measured" in v.detail
               for v in r.violations)


def test_refused_but_measured_rung_is_not_auto_failed():
    # THE #1933 false-positive fix: a rung REFUSES (RED) but its committed config's
    # F1 was recovered via allow_red_config and clears the floor + tracks the
    # reference. RED is a confidence flag, not a quality verdict -> NO violation.
    rung_f1 = {50_000: 0.998, 100_000: 0.99, 500_000: 0.985}
    refused = {50_000: False, 100_000: True, 500_000: True}  # 100K/500K RED-but-measured
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused)
    assert r.ok, [v.line() for v in r.violations]


def test_single_refused_but_measured_reference_passes():
    # THE heavy-tier 5M scenario: one rung, it refuses, but the RED config's F1 is
    # measured and clears the floor -> OK (no confident-reference paradox, since the
    # reference IS measured; only the absolute floor applies to a single rung).
    r = mod.evaluate_gate({5_000_000: 0.93}, None, rung_refused={5_000_000: True})
    assert r.ok, [v.line() for v in r.violations]


def test_refused_but_measured_rung_below_floor_still_fails():
    # A real quality collapse is still caught even when the rung is RED: the
    # measured F1 drops below the floor -> absolute_floor fires (RED noted).
    r = mod.evaluate_gate({5_000_000: 0.42}, None, rung_refused={5_000_000: True})
    checks = {(v.rung, v.check) for v in r.violations}
    assert (5_000_000, "absolute_floor") in checks
    assert not r.ok
    assert any("RED config" in v.detail for v in r.violations)


def test_reference_rung_unmeasurable_is_flagged():
    # If even the smallest gated rung has no measurable F1, there's no reference.
    r = mod.evaluate_gate({50_000: None, 100_000: None}, None,
                          rung_refused={50_000: True, 100_000: True})
    checks = {(v.rung, v.check) for v in r.violations}
    assert (50_000, "scale_invariance") in checks
    assert not r.ok


def test_confident_rungs_still_scored_when_a_later_rung_unmeasurable():
    # An unmeasurable rung must not suppress F1 checks on the confident ones.
    rung_f1 = {50_000: 0.55, 100_000: None}
    refused = {50_000: False, 100_000: True}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (50_000, "absolute_floor") in checks       # 0.55 < 0.80 floor
    assert (100_000, "scale_invariance") in checks     # unmeasurable vs GREEN reference


def test_pair_explosion_unmeasurable_rung_is_non_gating():
    # THE #2021 fix: the 1M rung's committed RED config explodes to billions of
    # candidate pairs (unmeasurable in any CI window), while 50K-500K measure the
    # SAME config at high F1. A pair-explosion unmeasurable rung ABOVE a measured
    # reference is a COST property, not a regression -> non-gating SKIP, not a
    # violation (the #1934 gym-gate skipped_degenerate_ceiling precedent).
    rung_f1 = {50_000: 0.998, 100_000: 0.99, 500_000: 0.985, 1_000_000: None}
    refused = {50_000: False, 100_000: True, 500_000: True, 1_000_000: True}
    reasons = {50_000: None, 100_000: None, 500_000: None, 1_000_000: "pair_explosion"}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused,
                          rung_unmeasurable_reason=reasons)
    assert r.ok, [v.line() for v in r.violations]
    assert {s.rung for s in r.skipped} == {1_000_000}
    assert r.skipped[0].reason == "pair_explosion"
    assert not any(v.rung == 1_000_000 for v in r.violations)


def test_pair_explosion_skip_is_non_gating_even_when_not_refused():
    # THE #2021 preflight path: on the corrupted-realistic shape the >=100K rung
    # COMMITS a coarse blocking config (no refusal) that would still explode
    # candidate pairs, so the skip now fires BEFORE dedupe_df runs and records
    # refused=False. A pair-explosion skip must be non-gating regardless of the
    # refuse verdict -- gating keys off the reason, not `refused`.
    rung_f1 = {50_000: 0.998, 100_000: 0.99, 500_000: 0.985, 1_000_000: None}
    refused = {50_000: False, 100_000: False, 500_000: False, 1_000_000: False}
    reasons = {50_000: None, 100_000: None, 500_000: None, 1_000_000: "pair_explosion"}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused,
                          rung_unmeasurable_reason=reasons)
    assert r.ok, [v.line() for v in r.violations]
    assert {s.rung for s in r.skipped} == {1_000_000}
    assert not any(v.rung == 1_000_000 for v in r.violations)


def test_pair_explosion_at_reference_is_still_a_violation():
    # No smaller rung to establish the config measures fine -> even a pair-explosion
    # at the reference scale is a hard flag (can't wave through the ONLY evidence).
    r = mod.evaluate_gate({1_000_000: None}, None,
                          rung_refused={1_000_000: True},
                          rung_unmeasurable_reason={1_000_000: "pair_explosion"})
    checks = {(v.rung, v.check) for v in r.violations}
    assert (1_000_000, "scale_invariance") in checks
    assert not r.ok
    assert not r.skipped


def test_error_unmeasurable_still_gates_not_skipped():
    # An unmeasurable rung whose reason is NOT pair_explosion (a genuine force-run
    # ERROR) must STILL be a violation -- only the known-benign pair explosion is
    # non-gating. Guards against the fix silently swallowing real breakage.
    rung_f1 = {50_000: 0.99, 500_000: None}
    reasons = {50_000: None, 500_000: "error"}
    r = mod.evaluate_gate(rung_f1, None, rung_refused={50_000: False, 500_000: True},
                          rung_unmeasurable_reason=reasons)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (500_000, "scale_invariance") in checks
    assert not r.ok
    assert not r.skipped


def test_pair_explosion_skip_does_not_mask_a_real_regression():
    # A measured rung that craters is STILL caught even when a higher rung is a
    # non-gating pair-explosion skip -- the skip only excuses the exploded rung.
    rung_f1 = {50_000: 0.99, 100_000: 0.55, 500_000: None}
    refused = {50_000: False, 100_000: False, 500_000: True}
    reasons = {50_000: None, 100_000: None, 500_000: "pair_explosion"}
    r = mod.evaluate_gate(rung_f1, None, rung_refused=refused,
                          rung_unmeasurable_reason=reasons)
    checks = {(v.rung, v.check) for v in r.violations}
    assert (100_000, "absolute_floor") in checks       # 0.55 < 0.80 floor -> real fail
    assert (100_000, "scale_invariance") in checks       # 0.55 << 0.99 reference
    assert {s.rung for s in r.skipped} == {500_000}      # explosion still skipped
    assert not r.ok                                       # the real regression fails the gate


def test_empty_measurements_raises():
    try:
        mod.evaluate_gate({}, None)
    except ValueError:
        return
    raise AssertionError("expected ValueError on empty measurements")
