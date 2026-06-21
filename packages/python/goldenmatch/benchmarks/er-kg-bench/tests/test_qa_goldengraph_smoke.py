"""Runs the REAL goldengraph engine end-to-end with stub LLM/embedder + an
identity resolver (no network, no goldenmatch dedupe). Requires the native
PyStore wheel, so it runs in the `goldengraph-pipeline` CI lane, not the
key-free er-kg-bench gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("goldengraph_native")  # native PyStore must be built

from erkgbench.qa_e2e.corpora import Document, QACorpus, QAItem  # noqa: E402
from erkgbench.qa_e2e.engines.goldengraph import GoldenGraphQAEngine  # noqa: E402
from erkgbench.qa_e2e.harness import AnswerResult, BuildResult, QAEngine  # noqa: E402
from goldengraph import ResolvedEntity  # noqa: E402

_EXTRACTION = (
    '{"entities": [{"name": "Acme", "type": "org"}, {"name": "Ada", "type": "person"}], '
    '"relationships": [{"subj": 0, "predicate": "founded by", "obj": 1}]}'
)


class _StubLLM:
    """goldengraph LLMClient. Extraction prompts (which contain "entities") get
    the canned extraction JSON; synthesis prompts get a canned answer."""

    def complete(self, prompt: str) -> str:
        return _EXTRACTION if "entities" in prompt else "Ada"


class _StubEmbedder:
    def embed(self, texts):
        import numpy as np

        return np.ones((len(texts), 4), dtype="float64")


def _identity_resolver(mentions):
    # one ResolvedEntity per mention (mirrors goldengraph/tests/test_retrieval.py)
    return [
        ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i])
        for i, m in enumerate(mentions)
    ]


def _toy_corpus():
    return QACorpus(
        name="toy",
        documents=(Document(id="d1", text="Acme was founded by Ada."),),
        questions=(
            QAItem(
                id="q1",
                question="Who founded Acme?",
                gold_answer="Ada",
                gold_supporting_fact_ids=("d1",),
                hop_count=1,
                ambiguity_level=0.0,
            ),
        ),
    )


def _engine():
    return GoldenGraphQAEngine(
        llm=_StubLLM(), embedder=_StubEmbedder(), resolver=_identity_resolver
    )


def test_goldengraph_engine_conforms_to_protocol():
    eng = _engine()
    assert isinstance(eng, QAEngine)
    assert eng.name == "goldengraph"
    assert eng.fidelity == "real-e2e"


def test_goldengraph_build_and_answer_return_typed_results():
    eng = _engine()
    build = eng.build_kg(_toy_corpus())
    assert isinstance(build, BuildResult)
    assert build.input_tokens > 0  # the counting wrapper saw the extraction call
    ans = eng.answer(build.handle, "Who founded Acme?")
    assert isinstance(ans, AnswerResult)
    assert isinstance(ans.text, str) and ans.text != ""
