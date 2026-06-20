import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import throughput_perf_gate as gate

BASE = {"candidate_pairs": 1000, "reduction_ratio": 0.95, "measured_recall": 0.97}


def test_pass_at_baseline():
    ok, fails = gate.compare(BASE, dict(BASE))
    assert ok and not fails


def test_fail_when_pairs_blow_up():
    cur = dict(BASE, candidate_pairs=1200)   # +20% > +15% tol
    ok, fails = gate.compare(BASE, cur)
    assert not ok and any("candidate_pairs" in f for f in fails)


def test_pass_within_pairs_tolerance():
    cur = dict(BASE, candidate_pairs=1140)   # +14% < +15%
    ok, _ = gate.compare(BASE, cur)
    assert ok


def test_fail_when_recall_drops():
    cur = dict(BASE, measured_recall=0.955)  # < 0.97 - 0.01
    ok, fails = gate.compare(BASE, cur)
    assert not ok and any("recall" in f for f in fails)


def test_fail_when_reduction_drops():
    cur = dict(BASE, reduction_ratio=0.93)   # < 0.95 - 0.01
    ok, fails = gate.compare(BASE, cur)
    assert not ok and any("reduction_ratio" in f for f in fails)


def test_update_baseline_roundtrip(tmp_path):
    p = tmp_path / "baseline.json"
    gate.write_baseline(p, BASE)
    assert json.loads(p.read_text())["candidate_pairs"] == 1000
