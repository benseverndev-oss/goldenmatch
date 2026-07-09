import os
import subprocess
import sys


def test_import_goldencheck_does_not_load_polars():
    code = ("import goldencheck, sys; "
            "bad=[m for m in sys.modules if m=='polars' or m.startswith('polars.')]; "
            "assert not bad, bad")
    env = dict(os.environ)
    env["PYTHONPATH"] = r"D:/show_case/gc-polars-evict/packages/python/goldencheck"
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
