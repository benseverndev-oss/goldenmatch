"""Regression guard for the LightRAG event-loop bug (2026-06-21): the adapter must
build the KG and answer EVERY question on one persistent loop, or LightRAG's
loop-bound storage primitives crash on the second loop ("bound to a different event
loop") and the engine scores 0.

Runs in normal CI -- no real LightRAG. A fake RAG reproduces the exact failure mode
by capturing the loop running during `initialize_storages` and rejecting any later
`ainsert`/`aquery` on a different loop with the same RuntimeError the production
stack hit. A stub `lightrag` module satisfies the `from lightrag import QueryParam`
import inside `answer`.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.engines.lightrag import LightRAGQAEngine  # noqa: E402


class _LoopBoundRAG:
    """Mimics LightRAG's loop affinity: storage primitives created during
    initialize_storages are bound to the loop running then, and any later use from a
    different loop raises (exactly the production "bound to a different event loop")."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None

    def _check_loop(self):
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError(
                "storage primitive is bound to a different event loop"
            )

    async def initialize_storages(self):
        self._loop = asyncio.get_running_loop()

    async def ainsert(self, text):
        self._check_loop()
        return None

    async def aquery(self, question, param=None):
        self._check_loop()
        return f"answer to {question}"


@pytest.fixture
def _stub_lightrag_module():
    """Provide a `lightrag` module with QueryParam so `answer`'s import resolves
    without the real package."""
    mod = types.ModuleType("lightrag")
    mod.QueryParam = lambda **kwargs: None  # type: ignore[attr-defined]
    mod.LightRAG = object  # type: ignore[attr-defined]
    sys.modules["lightrag"] = mod
    try:
        yield
    finally:
        sys.modules.pop("lightrag", None)


async def _stub_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
    return "ok"


def test_build_then_multiple_answers_share_one_loop(tmp_path, _stub_lightrag_module):
    eng = LightRAGQAEngine(
        llm_model_func=_stub_llm, embedding_func=object(), work_root=str(tmp_path)
    )
    rag = _LoopBoundRAG()
    eng._new_rag = lambda working_dir: rag  # type: ignore[assignment]

    class _Doc:
        def __init__(self, text):
            self.text = text

    class _Corpus:
        documents = (_Doc("Acme works at Foo."),)

    build = eng.build_kg(_Corpus())
    # Two answers AFTER build: the second loop is exactly where the old asyncio.run
    # -per-call path raised "bound to a different event loop".
    a1 = eng.answer(build.handle, "q one")
    a2 = eng.answer(build.handle, "q two")
    assert a1.text == "answer to q one"
    assert a2.text == "answer to q two"
