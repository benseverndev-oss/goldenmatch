"""Regression tests for two process-global concurrency hazards on the real
ThreadPoolExecutor block-scoring path:

- ``core/embedder.py``: ``get_embedder`` was a check-then-set on the module
  ``_embedders`` cache, so concurrent first-use could build (and load) the heavy
  model twice. Now double-checked-lock guarded.
- ``core/scorer.py``: ``_NE_BROKEN`` is a process global that the scoring entry
  points now reset per run, so a broken negative-evidence entry from one dedupe
  doesn't leak into the next (a real bug in a long-lived MCP/A2A server).
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import goldenmatch.core.embedder as em
from goldenmatch.core import scorer


def test_embedder_concurrent_first_use_builds_once():
    """8 threads racing get_embedder() for the same uncached model build the
    (heavy) model EXACTLY once -- the double-checked lock prevents the
    check-then-set double-init."""
    count = {"n": 0}

    class StubEmbedder:
        def __init__(self, model_name):
            count["n"] += 1
            time.sleep(0.02)  # widen the construction window so threads overlap
            self.model_name = model_name

    em._embedders.clear()
    try:
        with patch.object(em, "Embedder", StubEmbedder), patch(
            "goldenmatch.core.gpu.detect_gpu_mode", return_value=None
        ):
            barrier = threading.Barrier(8)
            results: list[object] = []

            def worker():
                barrier.wait()
                results.append(em.get_embedder("test-model-xyz"))

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert count["n"] == 1, f"model constructed {count['n']}x (double-init)"
        assert len({id(r) for r in results}) == 1, "threads got different instances"
    finally:
        em._embedders.clear()


def test_reset_ne_broken_clears():
    scorer._NE_BROKEN.add(("x", "y"))
    scorer.reset_ne_broken()
    assert scorer._NE_BROKEN == set()


def test_score_blocks_reset_ne_broken_per_run():
    """The scoring entry points reset _NE_BROKEN so a prior run's known-broken
    NE entries don't leak forward. Empty blocks: each function resets then
    returns early (mk is not touched on that path)."""
    scorer._NE_BROKEN.add(("stale_scorer", "stale_field"))
    scorer.score_blocks_parallel([], None, set())
    assert scorer._NE_BROKEN == set()

    scorer._NE_BROKEN.add(("stale_scorer", "stale_field"))
    scorer.score_blocks_columnar([], None, set())
    assert scorer._NE_BROKEN == set()
