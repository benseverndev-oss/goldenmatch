"""Self-gating smoke test: the harness gates ITSELF on its committed baseline.

Runs `gate --fast-only` over the anchors via a subprocess and asserts the gate
passes against baselines/scorecard.json. This catches both harness regressions
and (more importantly) any auto-config regression that moves an anchor signal.
"""
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_PKG = _REPO / "packages/python/goldenmatch"
_PKG_SCRIPTS = _PKG / "scripts"


def test_gate_fast_only_passes_on_committed_baseline():
    # Run with native UNPINNED (pop any inherited GOLDENMATCH_NATIVE) so on a box
    # with the native wheel built this exercises the native-on routing path against
    # the native-off baseline -- proving the gate is host-independent (planner_rung
    # drift is WARN, kernel signals match). CI without the wheel trivially matches.
    env = {
        **os.environ,
        "POLARS_SKIP_CPU_CHECK": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join(str(p) for p in (_REPO, _PKG, _PKG_SCRIPTS)),
    }
    env.pop("GOLDENMATCH_NATIVE", None)
    r = subprocess.run(
        [sys.executable, "-m", "scripts.autoconfig_quality", "gate", "--fast-only",
         "--datasets", "anchor_sparse_zip,anchor_shared_email,anchor_person_match"],
        cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, f"gate failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "verdict: PASS" in r.stdout
