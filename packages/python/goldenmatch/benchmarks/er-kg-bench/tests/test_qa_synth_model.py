"""Synthesis-model override (GOLDENGRAPH_SYNTH_MODEL) on the QA engine adapter.

The trace shows SYNTHESIS is the dominant loss bucket -- the answer edge is in the
retrieved ball but the answer-time reasoning fails. This override lets a run use a
stronger model for the `ask()` synthesis call ONLY while the build stays on the
base model. These tests pin the wiring (no native, no real LLM)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

pytest.importorskip("goldengraph")

from erkgbench.qa_e2e.engines.goldengraph import GoldenGraphQAEngine  # noqa: E402


class _StubLLM:
    def complete(self, prompt):  # pragma: no cover - never called in __init__
        return ""


class _StubEmbedder:
    def embed(self, texts):  # pragma: no cover - never called in __init__
        return []


def test_synth_llm_reuses_base_when_unset(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_MODEL", raising=False)
    eng = GoldenGraphQAEngine(llm=_StubLLM(), embedder=_StubEmbedder())
    # byte-identical to before: the synthesis call uses the very same wrapped llm.
    assert eng._synth_llm is eng._llm


def test_synth_llm_builds_separate_client_when_set(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_MODEL", "gpt-4o")
    captured = {}

    class _FakeOpenAIClient:
        def __init__(self, *, model):
            captured["model"] = model

        def complete(self, prompt):  # pragma: no cover
            return ""

    # Patch the symbol the adapter imports inside __init__.
    import goldengraph.llm as gllm

    monkeypatch.setattr(gllm, "OpenAIClient", _FakeOpenAIClient, raising=False)

    eng = GoldenGraphQAEngine(llm=_StubLLM(), embedder=_StubEmbedder())
    assert eng._synth_llm is not eng._llm  # a distinct client
    assert captured["model"] == "gpt-4o"  # built with the override model
