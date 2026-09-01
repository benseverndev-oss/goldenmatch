"""Coverage must cross the sweep's subprocess boundary.

The sweeps run every command in a child process. Without an explicit
process-startup hook the parent's coverage records nothing, and the resulting
.dat file is a green artifact measuring nil.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).parent


def test_probe_source_starts_coverage_in_the_child():
    """Both sweeps must inject the startup hook into their generated probe."""
    sys.path.insert(0, str(SCRIPTS))
    from sweep_cli_polars_free import _probe_source as cli_probe
    from sweep_mcp_polars_free import _probe_source as mcp_probe

    for name, src in (("cli", cli_probe()), ("mcp", mcp_probe())):
        assert "COVERAGE_PROCESS_START" in src, f"{name} probe has no coverage hook"
        assert "coverage.process_startup()" in src, f"{name} probe never starts coverage"


def test_a_child_process_actually_records_coverage(tmp_path):
    """End-to-end: a child importing goldenmatch must produce its own data file."""
    rc = tmp_path / "rc"
    rc.write_text("[run]\nsource = goldenmatch\nparallel = True\n", encoding="utf-8")
    child = tmp_path / "child.py"
    child.write_text(
        textwrap.dedent(
            """
            import os
            if os.environ.get("COVERAGE_PROCESS_START"):
                import coverage
                coverage.process_startup()
            import goldenmatch.core.frame  # noqa: F401
            """
        ),
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys\nsubprocess.run([sys.executable, sys.argv[1]], check=True)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(tmp_path / "c.dat")
    env["COVERAGE_PROCESS_START"] = str(rc)
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--rcfile", str(rc), str(parent), str(child)],
        check=True,
        env=env,
        cwd=str(tmp_path),
    )
    written = sorted(tmp_path.glob("c.dat*"))
    assert len(written) >= 2, f"expected a parent and a child data file, got {written}"
