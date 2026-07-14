"""ClusterFrames-eviction (Tier A) gate: the cluster stage is polars-free on the
arrow lane.

The default cluster stage is ``build_cluster_frames`` (frames-out), which threads
``backend='arrow'`` when the collected frame is an ``ArrowFrame`` (pipeline.py).
On that lane the assignments/metadata frames are ``pa.Table`` and every read must
go through the Frame seam -- no ``import polars`` / ``pl.col`` / ``pa.Table``
subscript. Two clustering paths + the telemetry emitter are exercised with polars
import BLOCKED (the D6 zero-polars gate mechanism, see ``_zero_polars_probe.py``):

1. ``build_cluster_frames(backend='arrow')`` with auto-split -> arrow ClusterFrames.
2. ``build_clusters_arrow_native(backend='arrow')`` (Rust-kernel arrow output).
3. ``_emit_cluster_profile_frames`` under an ACTIVE ``profile_capture`` -- the
   autoconfig sample-capture path that used to ``import polars`` + subscript the
   arrow metadata frame (the concrete Tier A leak this branch fixed).

Parity (byte-identical clusters on the polars path) is covered separately by
``test_cluster_frames_out_parity`` / ``test_build_clusters_arrow_native_parity``;
this file only locks the arrow-lane polars-freeness so it can't silently regress.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent


def _run_polars_blocked(body: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG_ROOT)
    env.update(extra_env)
    prelude = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('polars blocked (ClusterFrames Tier A gate)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


_ENV = {
    "GOLDENMATCH_FRAME": "arrow",
    "GOLDENMATCH_NATIVE": "1",
    "POLARS_SKIP_CPU_CHECK": "1",
    "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
}


def test_build_cluster_frames_arrow_is_polars_free():
    """SUBPROCESS, polars BLOCKED: the frames-out cluster stage on the arrow
    backend (auto-split ON) runs to completion without importing polars and
    returns arrow ClusterFrames."""
    body = """
        import sys
        from goldenmatch.core.cluster import build_cluster_frames
        import pyarrow as pa
        pairs = [(1, 2, 0.95), (2, 3, 0.9), (4, 5, 0.8), (1, 1, 0.99)]
        cf = build_cluster_frames(
            pairs, [1, 2, 3, 4, 5],
            max_cluster_size=100, weak_cluster_threshold=0.3,
            auto_split=True, backend="arrow",
        )
        assert isinstance(cf.assignments, pa.Table), type(cf.assignments)
        assert isinstance(cf.metadata, pa.Table), type(cf.metadata)
        assert cf.metadata.num_rows >= 1
        assert "polars" not in sys.modules, "polars leaked in build_cluster_frames(arrow)"
        print("CLUSTER-FRAMES ARROW OK")
    """
    proc = _run_polars_blocked(body, _ENV)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "CLUSTER-FRAMES ARROW OK" in proc.stdout


def test_emit_cluster_profile_frames_arrow_is_polars_free():
    """SUBPROCESS, polars BLOCKED: the telemetry emitter (the autoconfig
    sample-capture path) runs under an ACTIVE ``profile_capture`` on arrow frames
    without importing polars -- the concrete leak this branch fixed. Asserts the
    ClusterProfile is populated (a no-op emitter would defeat the test)."""
    body = """
        import sys
        from goldenmatch.core.cluster import build_cluster_frames
        from goldenmatch.core.profile_emitter import profile_capture, current_emitter
        pairs = [(1, 2, 0.95), (2, 3, 0.9), (4, 5, 0.8)]
        with profile_capture():
            build_cluster_frames(
                pairs, [1, 2, 3, 4, 5],
                max_cluster_size=100, weak_cluster_threshold=0.3,
                auto_split=True, backend="arrow",
            )
            prof = current_emitter().cluster
        assert prof is not None and prof.n_clusters == 2, prof
        # transitivity is reconstructed from pairs+assignments (a frames-path 0.0
        # would silently change the autoconfig controller's decisions).
        assert prof.transitivity_rate == 1.0, prof.transitivity_rate
        assert "polars" not in sys.modules, "polars leaked in _emit_cluster_profile_frames"
        print("CLUSTER-EMIT ARROW OK")
    """
    proc = _run_polars_blocked(body, _ENV)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "CLUSTER-EMIT ARROW OK" in proc.stdout


def test_build_clusters_arrow_native_is_polars_free():
    """SUBPROCESS, polars BLOCKED: the Rust-kernel arrow cluster builder on the
    arrow backend emits arrow ClusterFrames without importing polars. Skips
    gracefully (still asserts polars-free) if the native ``build_clusters_arrow``
    symbol isn't in this build."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.cluster import build_clusters_arrow_native
        pairs = pa.table({"id_a": [1, 2, 4], "id_b": [2, 3, 5], "score": [0.95, 0.9, 0.8]})
        cf = build_clusters_arrow_native(
            pairs, all_ids=[1, 2, 3, 4, 5], max_cluster_size=100, backend="arrow",
        )
        assert isinstance(cf.assignments, pa.Table), type(cf.assignments)
        assert isinstance(cf.metadata, pa.Table), type(cf.metadata)
        assert "polars" not in sys.modules, "polars leaked in build_clusters_arrow_native(arrow)"
        print("CLUSTER-ARROW-NATIVE OK")
    """
    proc = _run_polars_blocked(body, _ENV)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "CLUSTER-ARROW-NATIVE OK" in proc.stdout
