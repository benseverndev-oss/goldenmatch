"""Protocol conformance + the fact-synthesis cost seam, DB-free and LLM-free. The
real e2e (add_episode -> search against a live FalkorDB) is the opt-in
bench-graphrag-qa lane (it needs a graph DB). Runs in the graphiti-core venv lane."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("graphiti_core")

from erkgbench.qa_e2e.engines.graphiti import (  # noqa: E402
    GraphitiQAEngine,
    synthesize_from_facts,
)
from erkgbench.qa_e2e.harness import QAEngine  # noqa: E402


def test_graphiti_engine_conforms_to_protocol():
    eng = GraphitiQAEngine(falkordb_host="localhost", falkordb_port=6379)
    assert isinstance(eng, QAEngine)
    assert eng.name == "graphiti"
    assert eng.fidelity == "real-e2e"


def test_synthesize_from_facts_calls_llm_and_counts():
    counter = {"in": 0, "out": 0}

    async def _stub(prompt):
        return "Ada"

    text = asyncio.run(
        synthesize_from_facts("Who founded Acme?", ["Acme founded by Ada"], _stub, counter)
    )
    assert text == "Ada"
    assert counter["in"] > 0 and counter["out"] > 0
