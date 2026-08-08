"""Transform-prep on the ARROW lane with polars absent (#2430).

GoldenFlow's transform engine is NOT polars-native as a whole: `transform_df` is
the polars engine, but module-level `goldenflow.transform` is a POLARS-FREE
columnar engine. On the arrow lane `run_transform` PREFERS the polars engine
(faster, broader config coverage) and falls back to the columnar one when polars
is absent -- so a polars-free install STANDARDIZES normally instead of silently
skipping, while polars-present installs are byte-identical and unregressed.

Before #2430 the arrow lane bridged via `pl.from_arrow`, which raised
ImportError on a polars-free install and degraded to no-transform. That was a
SILENT capability loss: the run continued with unstandardized values while the
warning implied nothing could be done short of installing polars. It was caught
downstream, where column profiling saw raw values.

The degrade path still exists but is now NARROW -- the columnar engine declines
an UNCOVERED config, and the known trigger is an all-null column. Both behaviors
are pinned below so the distinction can't silently regress in either direction.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent

# Block polars at import time so the subprocess simulates a polars-free install.
_PRELUDE = (
    "import sys\n"
    "class _Block:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'polars' or name.startswith('polars.'):\n"
    "            raise ImportError('polars blocked')\n"
    "        return None\n"
    "sys.meta_path.insert(0, _Block())\n"
)


def _run_polars_free(body: str, marker: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG_ROOT)
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _PRELUDE + textwrap.dedent(body)],
        capture_output=True, text=True, env=env, timeout=180,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert marker in proc.stdout


def test_run_transform_standardizes_without_polars():
    """SUBPROCESS, polars BLOCKED: the arrow lane APPLIES standardization.

    This is the #2430 regression guard. It asserts VALUES, not just that the
    call didn't crash -- the pre-#2430 code also "passed" a shape-only check
    while skipping standardization entirely, which is exactly how the bug hid.
    """
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.transform import run_transform
        from goldenmatch.config.schemas import TransformConfig
        tbl = pa.table({
            "name": pa.array(["  Alice ", "BOB", "  Alice "], pa.string()),
            "dob": pa.array(
                ["april 01, 1999", "1938-07-05", "12/25/1980"], pa.string()
            ),
            "small": pa.array([1, 2, 3], pa.int32()),
        })
        out, fixes = run_transform(tbl, TransformConfig(mode="announced"))
        assert isinstance(out, pa.Table), type(out)
        assert out.num_rows == 3
        # Standardization ACTUALLY ran (the point of #2430).
        assert out["name"].to_pylist() == ["Alice", "BOB", "Alice"], out["name"]
        assert out["dob"].to_pylist() == [
            "1999-04-01", "1938-07-05", "1980-12-25",
        ], out["dob"]
        assert fixes, "no fixes reported despite transforms applying"
        # An untouched column keeps its EXACT arrow type -- a naive
        # dict->pa.table rebuild silently widens int32 to int64.
        assert str(out.schema.field("small").type) == "int32", out.schema
        assert "polars" not in sys.modules, "polars leaked in run_transform"
        print("TRANSFORM-NO-POLARS-STANDARDIZED OK")
    """
    _run_polars_free(body, "TRANSFORM-NO-POLARS-STANDARDIZED OK")


def test_run_transform_degrades_on_uncovered_config_without_polars():
    """SUBPROCESS, polars BLOCKED: an UNCOVERED config still degrades safely.

    An all-null column makes GoldenFlow's zero-config auto-detect decline to the
    polars engine, which is absent -- so `run_transform` must return the frame
    unchanged rather than crash. This is the remaining narrow fallback, not the
    whole arrow lane.
    """
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.transform import run_transform
        from goldenmatch.config.schemas import TransformConfig
        tbl = pa.table({
            "name": pa.array(["  Alice ", "BOB"], pa.string()),
            "empty": pa.array([None, None], pa.string()),
        })
        out, fixes = run_transform(tbl, TransformConfig(mode="announced"))
        assert isinstance(out, pa.Table), type(out)
        assert out.num_rows == 2
        # Degraded: returned unchanged rather than raising.
        assert out["name"].to_pylist() == ["  Alice ", "BOB"], out["name"]
        assert fixes == [], fixes
        assert "polars" not in sys.modules, "polars leaked in run_transform"
        print("TRANSFORM-NO-POLARS-DEGRADE OK")
    """
    _run_polars_free(body, "TRANSFORM-NO-POLARS-DEGRADE OK")


def test_run_transform_honors_exclude_columns_without_polars():
    """SUBPROCESS, polars BLOCKED: the `exclude_columns` strip/re-attach still
    holds on the columnar engine.

    The exclusion contract is durability-critical -- a `record_hash` column must
    pass through VERBATIM even though GoldenFlow has strip/lowercase rules that
    would rewrite it, and column ORDER must be preserved. That logic wraps the
    transform, so switching the engine underneath it could silently break it.
    """
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.transform import run_transform
        from goldenmatch.config.schemas import TransformConfig
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
        tbl = pa.table({
            "name": pa.array(["  Ann ", "bob"], pa.string()),
            "record_hash": pa.array(["  KEEP-ME  ", "  AND-ME "], pa.string()),
            "dob": pa.array(["april 01, 1999", "1938-07-05"], pa.string()),
        })
        tok = _RUNTIME_EXCLUDE_COLUMNS.set(["record_hash"])
        try:
            out, _ = run_transform(tbl, TransformConfig(mode="announced"))
        finally:
            _RUNTIME_EXCLUDE_COLUMNS.reset(tok)
        assert out.column_names == ["name", "record_hash", "dob"], out.column_names
        assert out["record_hash"].to_pylist() == [
            "  KEEP-ME  ", "  AND-ME ",
        ], "excluded column was transformed"
        assert out["name"].to_pylist() == ["Ann", "bob"], out["name"]
        assert out["dob"].to_pylist() == ["1999-04-01", "1938-07-05"], out["dob"]
        assert "polars" not in sys.modules, "polars leaked in run_transform"
        print("TRANSFORM-NO-POLARS-EXCLUSIONS OK")
    """
    _run_polars_free(body, "TRANSFORM-NO-POLARS-EXCLUSIONS OK")


def test_run_transform_strict_raises_on_uncovered_config_without_polars():
    """SUBPROCESS, polars BLOCKED: `strict=True` re-raises instead of degrading,
    so MCP/A2A callers who explicitly asked for transforms are not silently
    handed unstandardized data."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.transform import run_transform
        from goldenmatch.config.schemas import TransformConfig
        tbl = pa.table({
            "name": pa.array(["  Alice ", "BOB"], pa.string()),
            "empty": pa.array([None, None], pa.string()),
        })
        try:
            run_transform(tbl, TransformConfig(mode="announced"), strict=True)
        except ImportError:
            print("TRANSFORM-NO-POLARS-STRICT OK")
        else:
            raise AssertionError("strict=True should have re-raised ImportError")
    """
    _run_polars_free(body, "TRANSFORM-NO-POLARS-STRICT OK")
