"""Text-RAG baseline engine -- retrieval ordering, answer parsing, token + id
surfacing. Pure (injected fake openai client; no network)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

pytest.importorskip("numpy")

from erkgbench.qa_e2e.engines.text_rag import TextRAGQAEngine  # noqa: E402


def _vec(t: str):
    # 2-D toy embedding: aligned with the query iff the text mentions "rocket".
    return [1.0, 0.0] if "rocket" in t.lower() else [0.0, 1.0]


class _Emb:
    def __init__(self, e):
        self.embedding = e


class _EmbResp:
    def __init__(self, data):
        self.data = data


class _FakeEmbeddings:
    def create(self, *, model, input):
        return _EmbResp([_Emb(_vec(t)) for t in input])


class _ChatResp:
    def __init__(self, content, pt=11, ct=3):
        self.choices = [
            type("C", (), {"message": type("M", (), {"content": content})()})()
        ]
        self.usage = type("U", (), {"prompt_tokens": pt, "completion_tokens": ct})()


class _FakeChatCompletions:
    def __init__(self):
        self.last_messages = None

    def create(self, *, model, messages):
        self.last_messages = messages
        return _ChatResp("hop one\nAnswer: Acme")


class _FakeChat:
    def __init__(self):
        self.completions = _FakeChatCompletions()


class _FakeClient:
    def __init__(self):
        self.embeddings = _FakeEmbeddings()
        self.chat = _FakeChat()


class _Doc:
    def __init__(self, id, text):
        self.id = id
        self.text = text


class _Corpus:
    def __init__(self, docs):
        self.documents = docs


def test_text_rag_retrieves_relevant_first_and_parses_answer():
    client = _FakeClient()
    eng = TextRAGQAEngine(client=client, top_k=2)
    corpus = _Corpus([
        _Doc("d0::p0", "The sky is blue and unrelated."),
        _Doc("d0::p1", "Acme built the famous Rocket in 1958."),
    ])
    handle = eng.build_kg(corpus).handle
    res = eng.answer(handle, "who made the rocket?")

    # answer parsed from the last `Answer:` line
    assert res.text == "Acme"
    # the rocket passage is retrieved FIRST (the support_recall signal text-RAG has)
    assert res.retrieved_fact_ids[0] == "d0::p1"
    assert len(res.retrieved_fact_ids) == 2
    # chat usage is metered onto the answer
    assert res.input_tokens == 11 and res.output_tokens == 3
    # the prompt actually contained the retrieved passages
    assert "Rocket" in client.chat.completions.last_messages[0]["content"]


def test_text_rag_empty_corpus_is_safe():
    eng = TextRAGQAEngine(client=_FakeClient())
    res = eng.answer(_Corpus([]).__dict__ | {"ids": [], "texts": [], "unit": []}, "q?")
    assert res.text == ""


def test_build_engine_constructs_text_rag(monkeypatch):
    # _build_engine must not need a network/key to construct the engine -> patch the
    # OpenAI client the engine would otherwise build.
    import erkgbench.qa_e2e.engines.text_rag as tr

    monkeypatch.setattr(tr, "TextRAGQAEngine",
                        lambda **kw: ("text_rag", kw.get("model")), raising=True)
    from erkgbench.qa_e2e.run_qa_e2e import _build_engine

    got = _build_engine("text_rag")
    assert got == ("text_rag", "gpt-4o-mini")
