"""Validates the LightRAG adapter's protocol conformance + cost-counting seam
WITHOUT running LightRAG end-to-end (its extraction needs a real LLM's delimited
output; the real e2e run is the opt-in bench-graphrag-qa lane). Needs lightrag
installed, so it runs in the LightRAG isolated-venv lane."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("lightrag")

from erkgbench.qa_e2e.engines.lightrag import (  # noqa: E402
    LightRAGQAEngine,
    make_counting_llm_func,
)
from erkgbench.qa_e2e.harness import QAEngine  # noqa: E402


async def _stub_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
    return "Ada"


def test_lightrag_engine_conforms_to_protocol():
    eng = LightRAGQAEngine(
        llm_model_func=_stub_llm, embedding_func=object(), work_root="."
    )
    assert isinstance(eng, QAEngine)
    assert eng.name == "lightrag"
    assert eng.fidelity == "real-e2e"


def test_counting_llm_func_accounts_tokens():
    counter = {"in": 0, "out": 0}
    wrapped = make_counting_llm_func(_stub_llm, counter)
    out = asyncio.run(wrapped("a long enough prompt to count", system_prompt="sys"))
    assert out == "Ada"
    assert counter["in"] > 0 and counter["out"] > 0
