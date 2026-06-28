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
