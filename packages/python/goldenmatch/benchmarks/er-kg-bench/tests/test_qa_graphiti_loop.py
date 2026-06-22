"""Regression guard for the graphiti event-loop teardown noise (2026-06-22): the
adapter must run build and every answer on ONE persistent loop. The old
`asyncio.run`-per-call path closed the loop after each call, so graphiti's internal
httpx client teardown landed on a closed loop (`RuntimeError: Event loop is closed`).

DB-free + LLM-free: a fake graphiti records the running loop on every async op, and
the injected synthesis callable is a stub. With the old per-call `asyncio.run`, build
and each answer would run on distinct loops; the single-loop assertion below catches a
regression to that pattern.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.engines import graphiti as gmod  # noqa: E402
from erkgbench.qa_e2e.engines.graphiti import GraphitiQAEngine  # noqa: E402


class _LoopRecordingGraphiti:
    """Stands in for a Graphiti client; records the loop running during each op."""

    def __init__(self, seen: list):
        self._seen = seen

    async def build_indices_and_constraints(self):
        self._seen.append(asyncio.get_running_loop())

    async def add_episode(self, **kwargs):
        self._seen.append(asyncio.get_running_loop())

    async def search(self, query, num_results=5):
        self._seen.append(asyncio.get_running_loop())
        return []  # no edges -> synthesize over an empty fact list

    async def close(self):
        self._seen.append(asyncio.get_running_loop())


async def _stub_synth(prompt):
    return "ok"


def _corpus():
    class _Doc:
        def __init__(self, text):
            self.id = "d1"
            self.text = text

    class _Corpus:
        documents = (_Doc("Acme works at Foo."),)

    return _Corpus()


def test_build_and_answers_share_one_loop(monkeypatch):
    seen: list = []
    monkeypatch.setattr(gmod, "_new_graphiti", lambda host, port: _LoopRecordingGraphiti(seen))
    # EpisodeType is imported inside build_kg; stub the module attr it reads.
    import types

    fake_nodes = types.SimpleNamespace(EpisodeType=types.SimpleNamespace(text="text"))
    monkeypatch.setitem(sys.modules, "graphiti_core.nodes", fake_nodes)

    eng = GraphitiQAEngine(falkordb_host="h", falkordb_port=1, llm_callable=_stub_synth)
    build = eng.build_kg(_corpus())
    eng.answer(build.handle, "q one")
    eng.answer(build.handle, "q two")

    assert seen, "fake graphiti was never exercised"
    # Every async op across build + both answers ran on the SAME loop -- the property
    # the old asyncio.run-per-call path violated (a distinct loop per call).
    assert len({id(loop) for loop in seen}) == 1
