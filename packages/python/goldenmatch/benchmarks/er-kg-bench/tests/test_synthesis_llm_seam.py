# tests/test_synthesis_llm_seam.py
"""Cascade synthesis-LLM seam (stage-2-E): synthesis can use a separate model from extraction.
Default off = the extraction llm (same object). Pure (openai.OpenAI constructs without network)."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e.engines.goldengraph import _build_synthesis_llm


def test_synthesis_unset_reuses_extraction_llm(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTHESIS_MODEL", raising=False)
    sentinel = object()
    assert _build_synthesis_llm(sentinel) is sentinel        # byte-identical: same object


def test_synthesis_set_builds_separate_client(monkeypatch):
    pytest.importorskip("openai")                            # set branch needs openai; skip if absent
    monkeypatch.setenv("GOLDENGRAPH_SYNTHESIS_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("GOLDENGRAPH_SYNTHESIS_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("GOLDENGRAPH_SYNTHESIS_API_KEY", "sk-test-not-real")
    sentinel = object()
    synth = _build_synthesis_llm(sentinel)
    assert synth is not sentinel                              # a separate object is built
    assert synth._inner.model == "deepseek-reasoner"          # _CountingLLM -> OpenAIClient.model
