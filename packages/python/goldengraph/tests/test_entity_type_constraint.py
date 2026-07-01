"""GOLDENGRAPH_ENTITY_TYPE_CANON prepends the type-vocab instruction to the extract prompt."""
from goldengraph.extract import extract


class _CaptureLLM:
    def __init__(self):
        self.prompt = None

    def complete(self, prompt):
        self.prompt = prompt
        return '{"entities": [], "relationships": []}'
    # no complete_json -> extract() falls back to complete()


def test_type_vocab_instruction_absent_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_ENTITY_TYPE_CANON", raising=False)
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "MUST be exactly one of" not in llm.prompt


def test_type_vocab_instruction_present_when_gated(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_ENTITY_TYPE_CANON", "1")
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # force the .complete path for the stub
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "MUST be exactly one of" in llm.prompt
    assert "organization" in llm.prompt and "concept" in llm.prompt
