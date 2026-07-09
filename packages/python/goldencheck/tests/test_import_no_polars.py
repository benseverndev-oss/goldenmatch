import os
import subprocess
import sys
from pathlib import Path


def test_import_goldencheck_does_not_load_polars():
    code = ("import goldencheck, sys; "
            "bad=[m for m in sys.modules if m=='polars' or m.startswith('polars.')]; "
            "assert not bad, bad")
    # Anchor the subprocess PYTHONPATH to THIS package dir (…/packages/python/goldencheck),
    # so the gate tests this checkout's goldencheck regardless of worktree/CWD.
    pkg_dir = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
