"""Transform-prep arrow-lane polars-absent degradation (zero-config arrow eviction).

GoldenFlow's transform engine (`_do_transform`) is polars-native, so on the arrow
lane `run_transform` bridges via `pl.from_arrow`. When polars is absent it now
DEGRADES to no-transform (skip standardization) instead of crashing -- the two
polars touchpoints fixed: the `isinstance(df, pl.DataFrame)` discriminator (now
`isinstance(df, pa.Table)`, no polars import) and the `pl.from_arrow` bridge
(ImportError caught -> degrade). Byte-identical when polars is present.

This is the leak the endgame tripwire (`test_zero_config_dedupe_df_is_polars_free`)
hit on the full-native CI env (where zero-config configures a transform) but a
fallback-path dev box masks.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent


def test_run_transform_degrades_to_no_transform_without_polars():
    """SUBPROCESS, polars BLOCKED: run_transform on a dirty arrow frame with an
    enabled transform config completes without importing polars (degrades to
    no-transform) rather than crashing on the `pl.from_arrow` bridge."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.transform import run_transform
        from goldenmatch.config.schemas import TransformConfig
        tbl = pa.table({
            "name": ["  Alice ", "BOB", "  Alice "],
            "city": ["NYC", "LA", "NYC"],
        })
        out, fixes = run_transform(tbl, TransformConfig(mode="announced"))
        assert isinstance(out, pa.Table), type(out)
        assert out.num_rows == 3
        assert isinstance(fixes, list)
        assert "polars" not in sys.modules, "polars leaked in run_transform"
        print("TRANSFORM-NO-POLARS OK")
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG_ROOT)
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    prelude = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('polars blocked')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "TRANSFORM-NO-POLARS OK" in proc.stdout
