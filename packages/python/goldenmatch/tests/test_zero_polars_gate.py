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


def test_cli_import_zero_polars():
    """The CLI entry (what the web server boots through) imports with polars
    absent -- module-level pl.* literals are the W0 lesson class (static grep
    misses them; this gate is the authority)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    code = (
        "import sys\n"
        "class B:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('blocked')\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import goldenmatch.cli.main\n"
        "import goldenmatch.web.app\n"
        "print('CLI IMPORT ZERO-POLARS OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[-2000:]}"
    assert "CLI IMPORT ZERO-POLARS OK" in proc.stdout
