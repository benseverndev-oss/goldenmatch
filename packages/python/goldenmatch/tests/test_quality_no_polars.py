"""Quality-fix arrow-lane polars-absent degradation (zero-config arrow eviction).

goldencheck's SCAN is arrow-native, but its fix engine (``apply_fixes``) is
polars-native (goldencheck's ``[polars]`` extra). So on the arrow lane, when
polars is not installed, ``core.quality._scan_and_fix`` keeps the native scan but
DEGRADES auto-fix to scan-only (report the detected issues, apply none) instead
of crashing. The polars-present path is byte-identical to before.

This subprocess test blocks ``import polars`` (the D6 zero-polars gate mechanism)
and asserts a dirty arrow frame runs the quality check to completion polars-free.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent


def _run_polars_blocked(body: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG_ROOT)
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    prelude = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('polars blocked (quality-fix gate)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_quality_fix_degrades_to_scan_only_without_polars():
    """SUBPROCESS, polars BLOCKED: run_quality_check on a DIRTY arrow frame
    (whitespace/smart-quote issues the scan flags) completes without importing
    polars -- the fix step degrades to scan-only rather than crashing."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.quality import run_quality_check
        # Leading/trailing whitespace + a smart quote -> goldencheck finds fixes,
        # which would trip the polars fix bridge if it weren't degraded.
        tbl = pa.table({
            "name": ["  Alice ", "Bob\\u2019s", "  Alice ", "Carol", "Bob\\u2019s"],
            "city": ["NYC", "LA", "NYC", "SF", "LA"],
        })
        fixed, fixes = run_quality_check(tbl, config=None)
        # Frame comes back (unchanged arrow, since fixes were skipped) + no crash.
        assert fixed is not None
        assert isinstance(fixes, list)
        assert "polars" not in sys.modules, "polars leaked in the quality path"
        print("QUALITY-NO-POLARS OK")
    """
    proc = _run_polars_blocked(body)
    assert proc.returncode == 0, f"stdout={proc.stdout}\\nstderr={proc.stderr[-2500:]}"
    assert "QUALITY-NO-POLARS OK" in proc.stdout
