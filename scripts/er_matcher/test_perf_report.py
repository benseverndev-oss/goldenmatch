"""Tests for the ER-matcher P3a perf GO/NO-GO gate (pure -- no GPU).

Locks the extrapolation math, the learning-curve slope, and the four-check
gate decision (mirrors scripts/er_matcher/test_eval.py + scripts/test_qis_gate.py)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import perf_report as pr  # noqa: E402,I001


def test_extrapolate_full_run_linear():
    ext = pr.extrapolate_full_run(
        smoke_steps=100, smoke_wall_s=200.0, total_steps=5000,
        gpu_cost_per_hour_usd=1.10,
    )
    assert ext["s_per_step"] == 2.0
    assert ext["full_wall_s"] == 10000.0
    assert ext["full_wall_h"] == 10000.0 / 3600.0
    assert abs(ext["full_cost_usd"] - (10000.0 / 3600.0) * 1.10) < 1e-9


def test_extrapolate_rejects_zero_steps():
    import pytest
    with pytest.raises(ValueError, match="smoke_steps"):
        pr.extrapolate_full_run(smoke_steps=0, smoke_wall_s=1.0, total_steps=1,
                                gpu_cost_per_hour_usd=1.0)


def test_learning_curve_slope_climbing_vs_plateau():
    climbing = [{"frac": 0.1, "eval_loss": 0.9}, {"frac": 0.25, "eval_loss": 0.7},
                {"frac": 0.5, "eval_loss": 0.5}]
    assert abs(pr.learning_curve_slope(climbing) - 0.2) < 1e-9  # 0.7 - 0.5
    plateau = [{"frac": 0.5, "eval_loss": 0.50}, {"frac": 1.0, "eval_loss": 0.499}]
    assert abs(pr.learning_curve_slope(plateau) - 0.001) < 1e-9
    assert pr.learning_curve_slope([{"frac": 1.0, "eval_loss": 0.5}]) == 0.0  # <2 pts


def _good_metrics() -> dict:
    return {
        "gpu_util": 0.94,
        "peak_mem_gb": 18.0,
        "smoke_steps": 100,
        "smoke_wall_s": 180.0,
        "tokens_per_s": 5200.0,
        "learning_curve": [{"frac": 0.5, "eval_loss": 0.7}, {"frac": 1.0, "eval_loss": 0.55}],
        "total_steps": 4000,
        "gpu_cost_per_hour_usd": 1.10,
        "gpu_capacity_gb": 24.0,
    }


def test_gate_go_when_all_clear():
    g = pr.evaluate_perf_gate(_good_metrics())
    assert g.go is True
    assert g.extrapolation["full_cost_usd"] < 50.0


def test_gate_nogo_on_low_gpu_util():
    m = _good_metrics()
    m["gpu_util"] = 0.60  # data-bound
    g = pr.evaluate_perf_gate(m)
    assert g.go is False
    assert any(c["check"] == "gpu_util" and not c["passed"] for c in g.checks)


def test_gate_nogo_over_budget():
    m = _good_metrics()
    m["smoke_wall_s"] = 6000.0  # 60 s/step -> 4000 steps ~ 66.7 h -> way over $50
    g = pr.evaluate_perf_gate(m, budget_usd=50.0)
    assert g.go is False
    assert any(c["check"] == "budget" and not c["passed"] for c in g.checks)


def test_gate_nogo_on_plateau():
    m = _good_metrics()
    m["learning_curve"] = [{"frac": 0.5, "eval_loss": 0.5001}, {"frac": 1.0, "eval_loss": 0.5000}]
    g = pr.evaluate_perf_gate(m, min_curve_slope=0.01)
    assert g.go is False
    assert any(c["check"] == "learning_curve" and not c["passed"] for c in g.checks)


def test_gate_nogo_on_oom():
    m = _good_metrics()
    m["peak_mem_gb"] = 26.0  # exceeds 24 GB capacity
    g = pr.evaluate_perf_gate(m)
    assert g.go is False
    assert any(c["check"] == "memory" and not c["passed"] for c in g.checks)


def test_cli_flags_override_metrics(tmp_path):
    import json
    m = _good_metrics()
    del m["gpu_cost_per_hour_usd"]  # force CLI to supply it
    p = tmp_path / "smoke_metrics.json"
    p.write_text(json.dumps(m))
    rc = pr.main(["--metrics", str(p), "--gpu-cost-per-hour-usd", "1.10", "--total-steps", "4000"])
    assert rc == 0
