"""Slice: JSON-mode extraction routing -- extract() prefers complete_json when available + gated on."""
from __future__ import annotations

from goldengraph.extract import extract


class _JsonLLM:
    """Records which method extraction used."""

    def __init__(self):
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append("complete")
        return '{"entities": [], "relationships": []}'

    def complete_json(self, prompt: str) -> str:
        self.calls.append("complete_json")
        return '{"entities": [], "relationships": []}'


class _PlainLLM:
    def __init__(self):
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append("complete")
        return '{"entities": [], "relationships": []}'


def test_extract_uses_complete_json_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_EXTRACT_JSON_MODE", raising=False)
    llm = _JsonLLM()
    extract("Acme was founded by Ada.", llm)
    assert llm.calls == ["complete_json"]


def test_extract_falls_back_when_no_complete_json(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_EXTRACT_JSON_MODE", raising=False)
    llm = _PlainLLM()  # stub without complete_json
    extract("Acme was founded by Ada.", llm)
    assert llm.calls == ["complete"]


def test_extract_json_mode_disabled_uses_complete(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    llm = _JsonLLM()
    extract("Acme was founded by Ada.", llm)
    assert llm.calls == ["complete"]


class _CapLLM:
    """Captures the prompt extraction sent."""

    def __init__(self):
        self.prompt = None

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return '{"entities": [], "relationships": []}'


def test_relation_vocab_param_constrains_predicate(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    monkeypatch.delenv("GOLDENGRAPH_RELATION_VOCAB", raising=False)
    llm = _CapLLM()
    extract("X acquired Y.", llm, relation_vocab=("acquired", "part of"))
    assert "closed set" in llm.prompt and "acquired" in llm.prompt and "part of" in llm.prompt


def test_relation_vocab_from_env(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    monkeypatch.setenv("GOLDENGRAPH_RELATION_VOCAB", "acquired, works at")
    llm = _CapLLM()
    extract("X.", llm)
    assert "acquired" in llm.prompt and "works at" in llm.prompt


def test_no_vocab_is_open_extraction(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    monkeypatch.delenv("GOLDENGRAPH_RELATION_VOCAB", raising=False)
    llm = _CapLLM()
    extract("X.", llm)
    assert "closed set" not in llm.prompt
