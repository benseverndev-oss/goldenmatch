"""Graphiti QA engine adapter. Graphiti is a retrieval layer over a graph DB
(Neo4j/FalkorDB) with LLM-driven extraction; it returns FACTS, not answers, so the
adapter adds a small LLM synthesis step over the retrieved facts.

Cost seam (pragmatic + honest): the adapter counts its OWN synthesis call (len//4,
consistent with the goldengraph/lightrag engines). Graphiti's INTERNAL extraction
LLM calls (during add_episode) run through Graphiti's own OpenAI client and are NOT
counted by the harness -- so build cost is reported as 0/approximate. This avoids
wrapping Graphiti's version-fragile LLMClient internals; the engineered corpus is
small, and the results note Graphiti's cost as synthesis-only. Graphiti is also
nondeterministic by construction.

Graphiti + the FalkorDB driver are imported lazily so importing this module for the
registry never requires a DB or the heavy dep."""
from __future__ import annotations

import asyncio
import datetime as _dt
import time
from typing import Any

from ..harness import AnswerResult, BuildResult


async def synthesize_from_facts(question: str, facts, llm_callable, counter: dict) -> str:
    """One LLM call turning retrieved facts into a concise answer; counts tokens
    (len//4) into `counter`. `llm_callable` is an async `(prompt) -> str`."""
    prompt = (
        "Answer the question concisely using ONLY these facts.\nFacts:\n"
        + "\n".join(f"- {f}" for f in facts)
        + f"\n\nQuestion: {question}\nAnswer:"
    )
    counter["in"] += max(1, len(prompt) // 4)
    out = await llm_callable(prompt)
    counter["out"] += max(1, len(out) // 4)
    return out or ""


async def _default_openai_complete(prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()  # reads OPENAI_API_KEY
    resp = await client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
    )
    return resp.choices[0].message.content or ""


def _new_graphiti(host: str, port: int):
    """A fresh Graphiti client over FalkorDB. Created INSIDE the asyncio.run loop that
    uses it -- Graphiti's FalkorDB connection is bound to the running loop, so a client
    shared across the build loop and the per-answer loops raises 'Event loop is
    closed'. Each call gets its own client; the graph persists in FalkorDB."""
    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver

    return Graphiti(graph_driver=FalkorDriver(host=host, port=port))


async def _close_quietly(graphiti) -> None:
    """Close the client's connections within the current loop (best-effort), so they
    aren't torn down later against a closed loop."""
    close = getattr(graphiti, "close", None)
    if callable(close):
        try:
            await close()
        except Exception:  # noqa: BLE001 - teardown must never fail the run
            pass


class GraphitiQAEngine:
    name = "graphiti"
    fidelity = "real-e2e"

    def __init__(
        self,
        *,
        falkordb_host: str = "localhost",
        falkordb_port: int = 6379,
        llm_callable: Any | None = None,
    ):
        self._host = falkordb_host
        self._port = falkordb_port
        self._counter = {"in": 0, "out": 0}
        # synthesis LLM (the only call the adapter makes directly); injectable for tests
        self._synth = llm_callable or _default_openai_complete

    def build_kg(self, corpus) -> BuildResult:
        from graphiti_core.nodes import EpisodeType

        t0 = time.perf_counter()

        async def _build():
            # default OpenAI llm/embedder via OPENAI_API_KEY
            graphiti = _new_graphiti(self._host, self._port)
            try:
                await graphiti.build_indices_and_constraints()
                now = _dt.datetime.now(_dt.UTC)
                for doc in corpus.documents:
                    await graphiti.add_episode(
                        name=doc.id,
                        episode_body=doc.text,
                        source=EpisodeType.text,
                        reference_time=now,
                        source_description="qa-e2e",
                    )
            finally:
                await _close_quietly(graphiti)

        asyncio.run(_build())
        # No live client in the handle: it would be bound to the loop above and break
        # the per-answer loops. The graph lives in FalkorDB; answer reconnects.
        # Graphiti-internal extraction cost is not counted (see module docstring).
        return BuildResult(
            handle={}, input_tokens=0, output_tokens=0, latency_s=time.perf_counter() - t0
        )

    def answer(self, handle, question: str) -> AnswerResult:
        t0 = time.perf_counter()
        before = dict(self._counter)

        async def _answer():
            graphiti = _new_graphiti(self._host, self._port)
            try:
                edges = await graphiti.search(question, num_results=5)
                facts = [getattr(e, "fact", str(e)) for e in edges]
                return await synthesize_from_facts(
                    question, facts, self._synth, self._counter
                )
            finally:
                await _close_quietly(graphiti)

        text = asyncio.run(_answer())
        return AnswerResult(
            text=text,
            retrieved_fact_ids=(),  # fact uuids exist but don't align to corpus ids
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )
