"""Extraction-F1 eval -- wheel-free (stub LLM, api extractor; no torch/PyStore)."""
from __future__ import annotations

from erkgbench.qa_e2e.extraction_eval import ExtractionF1, evaluate_extractor, render_md


class _StubLLM:
    def complete(self, prompt: str) -> str:
        return '{"entities": [{"name": "X", "type": "concept"}], "relationships": []}'


def test_evaluate_extractor_shape(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACTOR", "api")
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # stub has no complete_json
    r = evaluate_extractor("api_nojson", llm=_StubLLM(), seed=7, n_questions=6, ambiguity=0.0)
    assert r.n_docs > 0
    for d in (r.entity, r.relation):
        assert {"precision", "recall", "f1"} <= set(d)
        assert 0.0 <= d["f1"] <= 1.0


class _BadJSONLLM:
    def complete(self, prompt: str) -> str:
        return '{"entities": [{"name": "X", "type":'  # truncated -> JSONDecodeError


def test_evaluate_is_fail_soft_on_bad_json(monkeypatch):
    # a malformed-JSON extraction must NOT crash the run (it crashed CI before this fix); it counts
    # as an empty extraction and bumps n_failed.
    monkeypatch.setenv("GOLDENGRAPH_EXTRACTOR", "api")
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    r = evaluate_extractor("api_nojson", llm=_BadJSONLLM(), seed=7, n_questions=6, ambiguity=0.0)
    assert r.n_docs > 0
    assert r.n_failed == r.n_docs  # every doc failed -> all empty, no crash
    assert r.entity["f1"] == 0.0 and r.relation["f1"] == 0.0


def test_render_md_table():
    res = [
        ExtractionF1("api_json", {"f1": 0.5, "precision": 0.5, "recall": 0.5},
                     {"f1": 0.3, "precision": 0.3, "recall": 0.3}, 20),
        ExtractionF1("rebel", {"f1": 0.7, "precision": 0.7, "recall": 0.7},
                     {"f1": 0.1, "precision": 0.1, "recall": 0.1}, 20),
    ]
    md = render_md(res, model="qwen2.5:7b-instruct")
    assert "Extraction-F1" in md and "api_json" in md and "rebel" in md and "0.500" in md
