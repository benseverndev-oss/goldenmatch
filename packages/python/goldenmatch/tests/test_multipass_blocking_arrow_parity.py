"""multi_pass blocking on the arrow lane: byte-identical to polars + polars-free.

`_build_multi_pass_blocks` delegates each pass to `_build_static_blocks`, which is
dual-rep (seam `derive_block_key` on arrow, the raw `pl.col` expr on a polars
LazyFrame). So multi_pass stays arrow when no polars-only feature (multi-key
auto-select or an active profile emitter) is in play -- the load-bearing
zero-config blocking-spine eviction (the #1207 per-identifier union is multi_pass).

Two locks:
1. **Parity**: multi_pass block membership (block_key + the row-id set per block)
   is identical building from a pl.DataFrame vs the equivalent pa.Table.
2. **Polars-free** (subprocess, polars BLOCKED): `build_blocks` on an arrow frame
   with a multi_pass config runs to completion without importing polars.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pyarrow as pa
import pytest

_PKG_ROOT = Path(__file__).parent.parent

_DATA = {
    "first": ["ann", "ann", "bob", "bob", "cara", "dan"] * 5,
    "last": ["smith", "smyth", "jones", "jones", "lee", "poe"] * 5,
    "zip": [f"{10000 + i % 7}" for i in range(30)],
}


def _multipass_config():
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    return BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["zip"], transforms=["strip"]),
            BlockingKeyConfig(fields=["last"], transforms=["lowercase"]),
        ],
    )


def _block_signature(frame):
    from goldenmatch.core.blocker import build_blocks

    sig = []
    for b in build_blocks(frame, _multipass_config()):
        col = b.materialize().native["__row_id__"]
        rids = col.to_pylist() if hasattr(col, "to_pylist") else col.to_list()
        sig.append((b.block_key, tuple(sorted(int(r) for r in rids if r is not None))))
    return sorted(sig)


def test_multipass_blocks_identical_arrow_vs_polars():
    import polars as pl

    dfp = pl.DataFrame(_DATA).with_row_index("__row_id__")
    tbl = pa.Table.from_pydict(
        {"__row_id__": list(range(len(_DATA["first"]))), **_DATA}
    )
    assert _block_signature(dfp) == _block_signature(tbl)


def test_multipass_blocking_arrow_is_polars_free():
    """SUBPROCESS, polars BLOCKED: build_blocks(arrow, multi_pass) completes
    without importing polars (each pass runs the seam static builder)."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.blocker import build_blocks
        from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
        data = {
            "first": ["ann", "ann", "bob", "bob", "cara", "dan"] * 5,
            "last": ["smith", "smyth", "jones", "jones", "lee", "poe"] * 5,
            "zip": [str(10000 + i % 7) for i in range(30)],
        }
        tbl = pa.Table.from_pydict({"__row_id__": list(range(30)), **data})
        cfg = BlockingConfig(strategy="multi_pass", passes=[
            BlockingKeyConfig(fields=["zip"], transforms=["strip"]),
            BlockingKeyConfig(fields=["last"], transforms=["lowercase"]),
        ])
        blocks = build_blocks(tbl, cfg)
        assert len(blocks) > 0
        assert "polars" not in sys.modules, "polars leaked in multi_pass build_blocks"
        print("MULTIPASS-ARROW OK")
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
    assert "MULTIPASS-ARROW OK" in proc.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
