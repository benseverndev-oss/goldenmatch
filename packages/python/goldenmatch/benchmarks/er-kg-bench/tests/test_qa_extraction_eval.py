"""Extraction-F1 eval -- wheel-free (stub LLM, api extractor; no torch/PyStore)."""
from __future__ import annotations

from erkgbench.qa_e2e.extraction_eval import (
    ExtractionF1,
    evaluate_extractor,
    predicate_counts,
    render_md,
)
from goldengraph.extract import Extraction, Mention, Relationship


def test_predicate_counts_requires_label_match():
    ex = Extraction(
        mentions=[Mention("Acme", "org"), Mention("Beta", "org")],
        relationships=[Relationship(subj=0, predicate="acquired", obj=1)],
    )
    # right entities + right predicate -> hit
    assert predicate_counts("Acme", "Beta", "acquired", ex)["rel_tp"] == 1
    # right entities + WRONG predicate -> miss (the predicate-mislabel case the agnostic metric hides)
    assert predicate_counts("Acme", "Beta", "works at", ex)["rel_tp"] == 0
    # lenient: gold substring of extracted predicate
    ex2 = Extraction(
        mentions=[Mention("Acme", "org"), Mention("Beta", "org")],
        relationships=[Relationship(subj=0, predicate="was acquired by", obj=1)],
    )
    assert predicate_counts("Acme", "Beta", "acquired", ex2)["rel_tp"] == 1


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
        ExtractionF1("api_json", {"f1": 0.5}, {"f1": 0.8}, 20, relation_pred={"f1": 0.3}),
    ]
    md = render_md(res, model="qwen2.5:7b-instruct")
    assert "Extraction-F1" in md and "api_json" in md and "relation-F1(pred)" in md
    assert "0.800" in md and "0.300" in md  # edge-existence vs predicate-exact both shown
