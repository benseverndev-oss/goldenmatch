"""Hybrid passage embedder model selection (stage-2-D): the local lane's OPENAI_EMBED_MODEL (nomic)
routes the passage half through Ollama; unset falls back to the OpenAI default. Pure, no network."""
from __future__ import annotations


def test_passage_embed_model_env(monkeypatch):
    from erkgbench.qa_e2e.engines.goldengraph import _passage_embed_model

    monkeypatch.delenv("OPENAI_EMBED_MODEL", raising=False)
    assert _passage_embed_model() == "text-embedding-3-large"      # unset -> OpenAI default

    monkeypatch.setenv("OPENAI_EMBED_MODEL", "nomic-embed-text")
    assert _passage_embed_model() == "nomic-embed-text"            # local lane -> nomic

    monkeypatch.setenv("OPENAI_EMBED_MODEL", "")                   # empty -> default (falsy `or`)
    assert _passage_embed_model() == "text-embedding-3-large"
