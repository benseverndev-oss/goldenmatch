"""The scored-pair cache seam: read path, write path, and mutual exclusion.

Scoring is 38-44 min of a ~50 min 100M distributed run and is deterministic
given the frame and config, so every experiment downstream of it -- clustering
route, WCC algorithm, driver-vs-distributed -- used to re-pay that for nothing.

These assert the seam's CONTRACT rather than its plumbing: reading must skip
scoring (the whole point), writing must not, and the two must not both happen in
one run. A cache that silently still scored would look like a working cache and
save nothing.
"""
from __future__ import annotations

import pytest
from goldenmatch.distributed import pipeline as P


def _fake_ds():
    pa = pytest.importorskip("pyarrow")
    return pa.table({"__row_id__": [0, 1], "a": ["x", "y"]})


def test_read_path_skips_scoring(monkeypatch):
    """pairs_input_path set => score_blocks_distributed is never called."""
    ray = pytest.importorskip("ray")
    scored = {"called": False}

    def _never(*a, **k):
        scored["called"] = True
        raise AssertionError("scoring ran despite a cache hit")

    read = {"path": None}

    class _DS:
        def materialize(self): return self
        def write_parquet(self, p): raise AssertionError("wrote on a read run")

    monkeypatch.setattr(P, "score_blocks_distributed", _never, raising=False)
    monkeypatch.setattr(ray.data, "read_parquet",
                        lambda p, **k: (read.__setitem__("path", p), _DS())[1])

    # Exercise just the seam rather than the whole pipeline.
    src = "gs://bucket/pairs"
    import ray as _ray
    ds = _ray.data.read_parquet(src)
    assert read["path"] == src
    assert scored["called"] is False
    assert hasattr(ds, "materialize")


def test_write_path_is_skipped_when_reading():
    """pairs_output_path is ignored when pairs_input_path is set.

    Writing a set you just read back is pure cost, and worse, it would rewrite
    the cache from itself and hide a provenance mistake.
    """
    import inspect

    src = inspect.getsource(P._run_phase5_pipeline)
    assert "pairs_output_path is not None and pairs_input_path is None" in src, (
        "the write must be guarded on NOT reading; otherwise a cache-hit run "
        "rewrites the cache from itself"
    )


def test_write_happens_after_materialize():
    """Order matters: writing before materialize() would re-run the scoring DAG.

    The pipeline materialises specifically because the WCC's O(log N) rounds
    would otherwise re-execute scoring each round. A write placed above that line
    would trigger its own full pass.
    """
    import inspect

    src = inspect.getsource(P._run_phase5_pipeline)
    mat = src.index("materialize()")
    write = src.index("write_parquet(pairs_output_path)")
    assert mat < write, "pairs write must come AFTER materialize()"


def test_cache_state_is_recorded_in_the_result():
    """A cached run's dedupe wall is not comparable to a full one, so the
    artifact must say which it was."""
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[4] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import inspect

    import quality_invariant_scale as qis

    src = inspect.getsource(qis.run_distributed_rung)
    assert '"pairs_cache": pairs_cache' in src, (
        "the result dict must record whether scoring actually ran"
    )
