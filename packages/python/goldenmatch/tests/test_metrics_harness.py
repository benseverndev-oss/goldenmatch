"""Unified metrics harness (scripts/metrics/harness.py).

Covers the offline core: the diff/baseline logic (fast, synthetic reports) plus a
smoke run of each real probe (accuracy F1/P/R + perf wall/RSS) to confirm they
produce sane, deterministic numbers. The harness lives under scripts/, imported
here via a sys.path shim (same pattern as the other scripts-importing tests).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from metrics import harness  # noqa: E402

# ── synthetic data generator ─────────────────────────────────────────────────


def test_make_labeled_unique_names_and_ground_truth():
    rows, gt = harness.make_labeled(n_entities=50, seed=7)
    assert len(rows) >= 50  # entities + their duplicates
    # ground-truth pairs are canonical (min, max) over positions
    assert all(a < b for a, b in gt)
    # distinct entities must not share a (first, last) -> recoverable structure
    base_names = {(r["first_name"].strip().lower(), r["last_name"].strip().lower())
                  for r in rows if r["first_name"].islower()}
    # clean (lowercase, unmessed) rows are the entity anchors; they're unique
    assert len(base_names) >= 50 - 5


def test_make_labeled_is_deterministic():
    a_rows, a_gt = harness.make_labeled(n_entities=80, seed=7)
    b_rows, b_gt = harness.make_labeled(n_entities=80, seed=7)
    assert a_rows == b_rows
    assert a_gt == b_gt


def test_make_labeled_rejects_too_many_entities():
    with pytest.raises(ValueError, match="unique name combos"):
        harness.make_labeled(n_entities=100_000)


# ── diff / baseline logic (fast, synthetic reports) ──────────────────────────


def _report(f1: float, scored: int) -> dict:
    return {"probes": {
        "accuracy_synthetic": {"group": "accuracy", "metrics": {"f1": f1, "precision": 1.0, "recall": 0.8}},
        "perf_synthetic": {"group": "perf", "metrics": {"wall_s": 3.0, "scored_pairs": scored}},
    }}


def test_flatten_keeps_only_numeric_metrics():
    flat = harness._flatten(_report(0.87, 1061))
    assert flat["accuracy_synthetic.f1"] == 0.87
    assert flat["perf_synthetic.scored_pairs"] == 1061.0


def test_build_baseline_marks_gated_and_informational():
    base = harness.build_baseline(_report(0.87, 1061))
    m = base["metrics"]
    assert m["accuracy_synthetic.f1"]["gated"] is True
    assert m["perf_synthetic.wall_s"]["gated"] is False  # wall is informational


def test_diff_clean_when_within_tolerance():
    base = harness.build_baseline(_report(0.87, 1061))
    # f1 dropped 0.01 (within the 0.02 tol) -> no regression
    diff = harness.diff_against_baseline(_report(0.86, 1061), base)
    assert diff["regressions"] == []


def test_diff_detects_gated_accuracy_regression():
    base = harness.build_baseline(_report(0.87, 1061))
    # f1 dropped 0.10 (past the 0.02 tol) -> regression
    diff = harness.diff_against_baseline(_report(0.77, 1061), base)
    assert [r["metric"] for r in diff["regressions"]] == ["accuracy_synthetic.f1"]


def test_diff_detects_deterministic_count_drift():
    base = harness.build_baseline(_report(0.87, 1061))
    # scored_pairs is gated with zero tolerance -> any drift is a regression
    diff = harness.diff_against_baseline(_report(0.87, 1100), base)
    assert any(r["metric"] == "perf_synthetic.scored_pairs" for r in diff["regressions"])


def test_diff_detects_count_drift_in_either_direction():
    # deterministic counts are two-sided: a DROP (e.g. blocking losing candidate
    # pairs) must regress just as an increase does -- not pass as "lower is better".
    base = harness.build_baseline(_report(0.87, 1061))
    diff = harness.diff_against_baseline(_report(0.87, 1011), base)
    assert any(r["metric"] == "perf_synthetic.scored_pairs" for r in diff["regressions"])


def test_diff_wall_swing_is_not_a_regression():
    base = harness.build_baseline(_report(0.87, 1061))
    report = _report(0.87, 1061)
    report["probes"]["perf_synthetic"]["metrics"]["wall_s"] = 30.0  # 10x slower
    diff = harness.diff_against_baseline(report, base)
    assert diff["regressions"] == []  # wall is informational, never gates


def test_committed_baseline_is_loadable_and_well_formed():
    base = harness.load_baseline()
    assert base is not None and base["schema"] == harness._SCHEMA
    assert "accuracy_synthetic.f1" in base["metrics"]


# ── real probes (smoke) ───────────────────────────────────────────────────────


def test_accuracy_probe_produces_sane_metrics():
    out = harness.probe_accuracy()
    assert out.group == "accuracy" and out.error is None
    assert set(out.metrics) == {"f1", "precision", "recall"}
    assert all(0.0 <= v <= 1.0 for v in out.metrics.values())
    assert out.metrics["f1"] > 0.5  # the explicit config recovers most of the structure


def test_accuracy_probe_is_deterministic():
    a = harness.probe_accuracy().metrics
    b = harness.probe_accuracy().metrics
    assert a == b


def test_perf_probe_records_wall_and_counts():
    out = harness.probe_perf(n_entities=120)
    assert out.group == "perf" and out.error is None
    assert out.metrics["wall_s"] > 0.0
    assert out.metrics["scored_pairs"] >= 0
    assert "stage_timings_s" in out.meta


def test_run_report_shape_and_resilience():
    report = harness.run_report(["accuracy_synthetic"])
    assert report["schema"] == harness._SCHEMA
    assert "git" in report and "env" in report
    assert "accuracy_synthetic" in report["probes"]
    # an unknown probe is recorded as an error, not a crash
    bad = harness.run_report(["nope"])
    assert "error" in bad["probes"]["nope"]
