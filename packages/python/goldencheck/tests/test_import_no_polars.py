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


def test_goldencheck_survives_polars_unimportable():
    # Simulate the P4 base-deps flip WITHOUT uninstalling: a meta_path finder makes
    # `polars` unimportable, then `import goldencheck` must still succeed (lazy proxy
    # defers `import polars`), and touching the proxy must raise a clean ModuleNotFoundError.
    code = (
        "import sys, importlib.abc\n"
        "class _Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ModuleNotFoundError(f'No module named {name!r}')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "import goldencheck\n"
        "assert 'polars' not in sys.modules, sorted(m for m in sys.modules if m.startswith('polars'))\n"
        "assert hasattr(goldencheck, 'scan_dataframe')\n"
        "from goldencheck._polars_lazy import pl\n"
        "try:\n"
        "    pl.DataFrame\n"
        "    raise AssertionError('expected ModuleNotFoundError touching the lazy proxy')\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
    )
    pkg_dir = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
