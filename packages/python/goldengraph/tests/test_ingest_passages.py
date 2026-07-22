"""Zero-touch hybrid wiring: ingest_corpus(index_passages=True) returns a
CorpusBuild(schema, passages), and the GoldenGraph facade threads that PassageIndex
into ask() so mode="hybrid" works out of the box -- no hand-built retriever."""
from __future__ import annotations

import goldenmatch.core.ann_blocker as _ab
import pytest
from goldengraph.ingest import CorpusBuild, ingest_corpus
from goldengraph.passage_index import PassageIndex


@pytest.fixture(autouse=True)
def _force_numpy_fallback(monkeypatch):
    monkeypatch.setattr(_ab, "_HAS_FAISS", False)


class _StubLLM:
    def complete(self, prompt: str) -> str:
        return '{"entities": [], "relationships": []}'


class _StubEmbedder:
    """Deterministic axis-per-keyword embedder (first known word wins)."""

    _VOCAB = {"apple": [1.0, 0.0, 0.0], "banana": [0.0, 1.0, 0.0], "cherry": [0.0, 0.0, 1.0]}

    def embed(self, texts):
        import numpy as np

        out = []
        for t in texts:
            vec = [0.0, 0.0, 0.0]
            for w in str(t).lower().split():
                if w in self._VOCAB:
                    vec = self._VOCAB[w]
                    break
            out.append(vec)
        return np.array(out, dtype=float)


class _RecordingStore:
    """Default ingest path only calls store.append(json) -> wheel-free."""

    def __init__(self):
        self.appends = 0

    def append(self, _batch_json):
        self.appends += 1


def _stub_resolver(mentions):
    from goldengraph.resolve import ResolvedEntity

    return [ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i]) for i, m in enumerate(mentions)]


_DOCS = ["apple pie recipe", "banana bread", "cherry cobbler"]
_IDS = ["d1", "d2", "d3"]


def test_index_passages_false_returns_bare_schema():
    # Byte-identical to the prior contract: no CorpusBuild, just the schema (None here).
    out = ingest_corpus(
        _DOCS, _RecordingStore(), llm=_StubLLM(), resolver=_stub_resolver, embedder=_StubEmbedder(),
    )
    assert not isinstance(out, CorpusBuild)
    assert out is None  # schema discovery off -> None


def test_index_passages_true_returns_corpusbuild_with_working_retriever():
    store = _RecordingStore()
    out = ingest_corpus(
        _DOCS, store, llm=_StubLLM(), resolver=_stub_resolver, embedder=_StubEmbedder(),
        doc_ids=_IDS, index_passages=True,
    )
    assert isinstance(out, CorpusBuild)
    assert store.appends == 3  # all docs still committed to the store
    assert isinstance(out.passages, PassageIndex) and len(out.passages) == 3
    # the returned index is a live retriever over the source passages
    assert out.passages.retrieve("banana", k=1) == ["banana bread"]


def test_index_passages_true_without_embedder_raises():
    with pytest.raises(ValueError, match="embedder"):
        ingest_corpus(
            _DOCS, _RecordingStore(), llm=_StubLLM(), resolver=_stub_resolver,
            embedder=None, index_passages=True,
        )
