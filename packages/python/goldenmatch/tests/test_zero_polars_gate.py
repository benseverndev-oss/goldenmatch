"""D6 gate: the arrow lane's covered engine runs with polars NEVER imported.

Subprocess-based (the W0 lazy-import gate precedent): run an eligible
dedupe on the Frame lane (tests/_zero_polars_probe.py) and assert
``polars`` is absent from ``sys.modules``. This is the endgame's
invariant #1 arbiter -- every ``isinstance(x, pl.X)`` on a hot path
triggers the lazy proxy and fails this test until it is guarded.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROBE = Path(__file__).parent / "_zero_polars_probe.py"


def test_arrow_lane_exact_dedupe_imports_zero_polars():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    proc = subprocess.run(
        [sys.executable, str(_PROBE)],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    assert "ZERO-POLARS OK" in proc.stdout
