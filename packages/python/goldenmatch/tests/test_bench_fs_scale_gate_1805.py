"""Issue #1805 (checkbox 3) — unit tests for the FS scheduled-scale-gate
decision (`bench_fs_distributed.evaluate_scale_gate`).

The gate turns the FS scale bench (F1 + wall + peak RSS at 5M) into a
fail-on-regression check so a ≥500K FS regression can't sit below every CI gate
the way #1792 / #1798 did. This pins the PURE decision logic so it's verified
without a 5M dedupe (mirrors `scripts/test_qis_gate.py`): each threshold is
independent and optional, and every breach is reported (not just the first).
"""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "bench_fs_distributed",
    pathlib.Path(__file__).parent.parent / "scripts" / "bench_fs_distributed.py",
)
_bfd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bfd)
evaluate_scale_gate = _bfd.evaluate_scale_gate


def test_all_thresholds_met_is_clean():
    assert evaluate_scale_gate(
        0.95, 100.0, 10.0,
        min_f1=0.90, max_wall_seconds=200.0, max_peak_rss_gb=20.0,
    ) == []


def test_no_thresholds_never_gates():
    # workflow_dispatch ad-hoc bench: measure only, never fail.
    assert evaluate_scale_gate(0.01, 9999.0, 999.0) == []


def test_f1_floor_breach():
    errs = evaluate_scale_gate(0.80, 100.0, 10.0, min_f1=0.90, rows=5_000_000)
    assert len(errs) == 1
    assert "F1" in errs[0] and "::error::" in errs[0]


def test_wall_ceiling_breach():
    errs = evaluate_scale_gate(0.95, 2000.0, 10.0, max_wall_seconds=1800.0)
    assert len(errs) == 1
    assert "wall" in errs[0]


def test_rss_ceiling_breach():
    errs = evaluate_scale_gate(0.95, 100.0, 45.0, max_peak_rss_gb=40.0)
    assert len(errs) == 1
    assert "RSS" in errs[0]


def test_every_breach_is_reported_not_just_first():
    errs = evaluate_scale_gate(
        0.50, 3000.0, 99.0,
        min_f1=0.90, max_wall_seconds=1800.0, max_peak_rss_gb=40.0,
    )
    assert len(errs) == 3


def test_threshold_is_inclusive_boundary_passes():
    # Exactly at the limit is NOT a breach (strict < / >).
    assert evaluate_scale_gate(
        0.90, 1800.0, 40.0,
        min_f1=0.90, max_wall_seconds=1800.0, max_peak_rss_gb=40.0,
    ) == []


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
