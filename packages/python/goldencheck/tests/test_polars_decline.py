import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_lazy_proxy_declines_with_helpful_message():
    code = textwrap.dedent("""
        import sys, importlib.abc
        class _B(importlib.abc.MetaPathFinder):
            def find_spec(self, n, path=None, target=None):
                if n == 'polars' or n.startswith('polars.'):
                    raise ModuleNotFoundError(n)
                return None
        sys.meta_path.insert(0, _B())
        from goldencheck._polars_lazy import pl
        try:
            pl.DataFrame  # triggers the deferred import
            raise SystemExit('expected ImportError')
        except ImportError as e:
            assert 'goldencheck[polars]' in str(e), str(e)
    """)
    pkg = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg + os.pathsep + env.get("PYTHONPATH", "")
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
