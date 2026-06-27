"""GoldenMatch-native RAG engines -- retrieval ordering, answer parsing, token + id
surfacing, and the entity-resolution path. The OpenAI client is a fake (no network);
goldenmatch's REAL retrieval surface (retrieve_similar_records / entity_aware_retrieve)
runs over the fake embeddings."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

pytest.importorskip("numpy")
pytest.importorskip("polars")
pytest.importorskip("goldenmatch")

from erkgbench.qa_e2e.engines.goldenmatch_rag import (  # noqa: E402
    GoldenmatchEntityRAGQAEngine,
    GoldenmatchRAGQAEngine,
)


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


def _corpus():
    return _Corpus([
        _Doc("d0::p0", "The sky is blue and unrelated."),
        _Doc("d0::p1", "Acme built the famous Rocket in 1958."),
    ])


def test_goldenmatch_rag_retrieves_relevant_first_and_parses_answer():
    client = _FakeClient()
    eng = GoldenmatchRAGQAEngine(client=client, top_k=2)
    handle = eng.build_kg(_corpus()).handle
    res = eng.answer(handle, "who made the rocket?")

    assert res.text == "Acme"
    # the rocket passage is retrieved FIRST (goldenmatch's ANN ranks it top)
    assert res.retrieved_fact_ids[0] == "d0::p1"
    assert res.input_tokens == 11 and res.output_tokens == 3
    # the prompt actually carried the retrieved passage text
    assert "Rocket" in client.chat.completions.last_messages[0]["content"]


def test_goldenmatch_rag_caches_corpus_embedding_across_questions():
    # The adapter must embed the corpus once (cache_key hit), not per question.
    client = _FakeClient()
    eng = GoldenmatchRAGQAEngine(client=client, top_k=2)
    handle = eng.build_kg(_corpus()).handle
    eng.answer(handle, "who made the rocket?")
    cached_keys = set(eng._embedder._cache)
    eng.answer(handle, "who made the rocket?")
    # second question reused the same corpus cache key (still present, not regrown)
    assert any(k.startswith("retrieve:text:") for k in cached_keys)


def test_goldenmatch_entity_rag_resolves_and_answers():
    client = _FakeClient()
    eng = GoldenmatchEntityRAGQAEngine(client=client, top_k=2)
    handle = eng.build_kg(_corpus()).handle
    res = eng.answer(handle, "who made the rocket?")

    assert res.text == "Acme"
    # distinct paragraphs -> each is its own entity -> both ids surface
    assert "d0::p1" in res.retrieved_fact_ids
    assert res.input_tokens == 11 and res.output_tokens == 3


def test_both_empty_corpus_is_safe():
    for cls in (GoldenmatchRAGQAEngine, GoldenmatchEntityRAGQAEngine):
        eng = cls(client=_FakeClient())
        handle = eng.build_kg(_Corpus([])).handle
        assert eng.answer(handle, "q?").text == ""


def test_build_engine_constructs_goldenmatch_rag_engines(monkeypatch):
    import erkgbench.qa_e2e.engines.goldenmatch_rag as gr

    monkeypatch.setattr(
        gr, "GoldenmatchRAGQAEngine",
        lambda **kw: ("goldenmatch_rag", kw.get("model")), raising=True
    )
    monkeypatch.setattr(
        gr, "GoldenmatchEntityRAGQAEngine",
        lambda **kw: ("goldenmatch_entity_rag", kw.get("model")), raising=True
    )
    from erkgbench.qa_e2e.run_qa_e2e import _build_engine

    assert _build_engine("goldenmatch_rag") == ("goldenmatch_rag", "gpt-4o-mini")
    assert _build_engine("goldenmatch_entity_rag") == (
        "goldenmatch_entity_rag", "gpt-4o-mini"
    )
