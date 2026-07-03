"""GOLDENGRAPH_EXTRACT_RECALL prepends an exhaustive-entity instruction to the extract prompt."""
from goldengraph.extract import extract


class _CaptureLLM:
    def __init__(self):
        self.prompt = None

    def complete(self, prompt):
        self.prompt = prompt
        return '{"entities": [], "relationships": []}'
    # no complete_json -> extract() falls back to .complete


def test_recall_instruction_absent_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_EXTRACT_RECALL", raising=False)
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "Extract EVERY named entity" not in llm.prompt


def test_recall_instruction_present_when_gated(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_RECALL", "1")
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # force .complete for the stub
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "Extract EVERY named entity" in llm.prompt
