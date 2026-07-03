"""SP1 EntityIndex: embed-once ANN retrieval over entity names (box-safe, numpy fallback)."""
from __future__ import annotations

import goldenmatch.core.ann_blocker as _ab
import numpy as np
import pytest
from goldengraph.entity_index import EntityIndex


@pytest.fixture(autouse=True)
def _force_numpy_fallback(monkeypatch):
    # Hermetic: never depend on faiss being installed; numpy fallback gives the same neighbor set.
    monkeypatch.setattr(_ab, "_HAS_FAISS", False)


_VECS = {"apple": [1.0, 0.0, 0.0], "banana": [0.0, 1.0, 0.0], "cherry": [0.0, 0.0, 1.0]}


class _StubEmbedder:
    """Deterministic: known name -> its axis vector, unknown -> zero. Counts embed() calls."""
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return np.array([_VECS.get(str(t).strip().lower(), [0.0, 0.0, 0.0]) for t in texts], dtype=float)


def _ent(eid, name, typ="thing"):
    return {"entity_id": eid, "canonical_name": name, "typ": typ}


# --- Task 1: build + query ----------------------------------------------------------------------------
def test_build_and_query_topk():
    idx = EntityIndex.build([_ent(0, "apple"), _ent(1, "banana"), _ent(2, "cherry")], _StubEmbedder())
    assert idx.query("banana", _StubEmbedder(), k=1) == [1]
    assert idx.query("apple", _StubEmbedder(), k=1) == [0]


def test_query_maps_rows_to_entity_ids():
    idx = EntityIndex.build([_ent(5, "apple"), _ent(1, "banana"), _ent(99, "cherry")], _StubEmbedder())
    assert idx.query("apple", _StubEmbedder(), k=1) == [5]
    assert idx.query("cherry", _StubEmbedder(), k=1) == [99]


def test_build_filters_literals_and_empty():
    ents = [_ent(0, "apple"), _ent(1, "2024-01-01", typ="literal:date"), _ent(2, "  ")]
    idx = EntityIndex.build(ents, _StubEmbedder())
    assert len(idx) == 1
    assert idx.query("apple", _StubEmbedder(), k=1) == [0]


def test_query_embeds_query_only():
    idx = EntityIndex.build([_ent(0, "apple"), _ent(1, "banana")], _StubEmbedder())
    emb = _StubEmbedder()
    idx.query("apple", emb, k=1)
    assert emb.calls == 1                      # ONE embed (the query), NOT N -- the anti-regression
    idx.query("banana", emb, k=1)
    assert emb.calls == 2


def test_query_rejects_k_above_capacity():
    idx = EntityIndex.build([_ent(0, "apple")], _StubEmbedder(), top_k=50)
    with pytest.raises(ValueError):
        idx.query("apple", _StubEmbedder(), k=100)


def test_empty_index_returns_empty():
    idx = EntityIndex.build([_ent(0, "  ", typ="literal:x")], _StubEmbedder())
    assert len(idx) == 0 and idx.query("apple", _StubEmbedder(), k=5) == []


# --- Task 2: save / load ------------------------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    ents = [_ent(5, "apple"), _ent(1, "banana"), _ent(99, "cherry")]
    idx = EntityIndex.build(ents, _StubEmbedder())
    idx.save(str(tmp_path / "idx"))
    loaded = EntityIndex.load(str(tmp_path / "idx"))
    assert len(loaded) == 3
    assert loaded.query("cherry", _StubEmbedder(), k=1) == [99]


# --- Task 3: seed_by_query seam -----------------------------------------------------------------------
class _Graph:
    def __init__(self, ents):
        self._e = ents

    def entities(self):
        return self._e


def test_seed_by_query_uses_index_when_given():
    from goldengraph.embed import seed_by_query

    ents = [_ent(0, "apple"), _ent(1, "banana")]
    idx = EntityIndex.build(ents, _StubEmbedder())
    emb = _StubEmbedder()
    seeds = seed_by_query(_Graph(ents), "apple", emb, k=1, index=idx)
    assert seeds == [0]
    assert emb.calls == 1                      # ONLY the query embedded (via the index)


def test_seed_by_query_none_preserves_current_path():
    from goldengraph.embed import seed_by_query

    ents = [_ent(0, "apple"), _ent(1, "banana")]
    emb = _StubEmbedder()
    seeds = seed_by_query(_Graph(ents), "apple", emb, k=1)   # index=None -> current re-embed path
    assert seeds == [0]
    assert emb.calls == 1                      # current path embeds [query]+names in ONE call
