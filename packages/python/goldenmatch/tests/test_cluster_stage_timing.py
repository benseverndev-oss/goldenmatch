"""The driver-side clustering stage's per-step timing actually fires.

Written because the previous diagnostic on this lane did NOT. A
GOLDENMATCH_BUCKET_DEBUG dispatch set the flag by appending an export to
~/.bashrc via `ray exec`; non-interactive bash never sources it, so the run
completed normally and produced no timing at all. A flag that silently does
nothing is worse than no flag, because it is indistinguishable from a measured
null.

The three GATE tests need no Ray -- that gate is a plain env read -- so they run
in the ordinary python lane. The fourth exercises the real stage, which imports
ray internally, so it skips where ray is absent and runs in the distributed
lanes. Splitting them that way keeps the cheap half of the guard everywhere
rather than confining all of it to a Ray-only lane.
"""
from __future__ import annotations

import logging

import goldenmatch.distributed.clustering as C


def test_debug_gate_is_off_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_DEBUG", raising=False)
    assert C._cluster_debug_on() is False


def test_debug_gate_reads_env_at_call_time(monkeypatch):
    """Read at CALL time, not import: the driver sets this via the container
    env, and a module-level constant would freeze the value at import."""
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_DEBUG", "1")
    assert C._cluster_debug_on() is True


def test_debug_gate_treats_falsey_spellings_as_off(monkeypatch):
    for v in ("0", "", "false", "False", "no", "off"):
        monkeypatch.setenv("GOLDENMATCH_CLUSTER_DEBUG", v)
        assert C._cluster_debug_on() is False, f"{v!r} should be off"


def test_stage_timing_emits_every_step(monkeypatch, caplog):
    """The five steps must all report. A missing step is how a stage's cost
    goes unattributed, which is the whole reason this exists."""
    import numpy as np
    import pyarrow as pa
    import pytest

    # `_build_clusters_cc_fallback` imports ray internally, so this test cannot
    # run in the plain `python_goldenmatch` lane, which has no ray. The gate
    # tests above still cover the env contract there.
    pytest.importorskip("ray")

    class FakeDS:
        def __init__(self, t): self.t = t
        def select_columns(self, cols): return FakeDS(self.t.select(cols))
        def iter_batches(self, batch_format="pyarrow"):
            return iter([pa.Table.from_batches([b])
                         for b in self.t.to_batches(max_chunksize=2_000)])

    rng = np.random.default_rng(3)
    n = 20_000
    ds = FakeDS(pa.table({
        "id_a": rng.integers(0, n // 4, n, dtype=np.int64),
        "id_b": rng.integers(0, n // 4, n, dtype=np.int64),
        "score": rng.random(n),
    }))

    monkeypatch.setenv("GOLDENMATCH_CLUSTER_DEBUG", "1")
    # `ray.data.from_arrow` is the only Ray touch; stub it so this stays a
    # pure-Python test rather than spinning a cluster.
    import ray
    monkeypatch.setattr(ray.data, "from_arrow", lambda t: t)

    with caplog.at_level(logging.WARNING, logger=C.__name__):
        C._build_clusters_cc_fallback(ds, None, max_cluster_size=1_000_000)

    text = caplog.text
    for step in ("1_pull_pairs_to_driver", "2_derive_id_universe",
                 "3_map_ids_to_dense_index", "4_connected_components",
                 "5_build_output_table"):
        assert step in text, f"{step} missing from the timing output:\n{text}"
    assert "driver clustering stage" in text
