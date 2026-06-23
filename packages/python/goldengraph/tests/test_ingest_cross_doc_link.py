"""Cross-document linking unit tests -- offline, deterministic. Exercises the
COMPOUND-key linker: per-entity feature rows (name/type/aliases + graph
neighborhood) are fed to a matcher (goldenmatch by default; a deterministic stub
here). _record_key is stubbed, the fake store serves entities() + query(), so no
native PyStore and no goldenmatch are needed. Locks (a) the neighborhood feature
construction and (b) the key-injection that unions cross-document entities."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Package rebinds `goldengraph.ingest` to the ingest() fn; grab the MODULE.
ingest = importlib.import_module("goldengraph.ingest")


@pytest.fixture(autouse=True)
def _stub_record_key(monkeypatch):
    monkeypatch.setattr(ingest, "_record_key", lambda name, typ: f"{typ}|{name}")


class _FakeSlice:
    def __init__(self, ents, edges):
        self._ents = ents
        self._edges = edges

    def entities(self):
        return self._ents

    def query(self, ids, hops):
        return {"entities": self._ents, "edges": self._edges}


class _FakeStore:
    def __init__(self, ents, edges=()):
        self._ents = ents
        self._edges = list(edges)

    def as_of(self, valid_t, tx_t):
        return _FakeSlice(self._ents, self._edges)


def _existing(eid, name, typ, *surfaces):
    return {"entity_id": eid, "canonical_name": name, "typ": typ,
            "surface_names": list(surfaces) or [name]}


def _batch_entity(lid, name, typ, *surfaces):
    surfaces = list(surfaces) or [name]
    return {"local_id": lid, "canonical_name": name, "typ": typ,
            "surface_names": surfaces, "record_keys": [f"{typ}|{s}" for s in surfaces]}


# --- feature construction -------------------------------------------------

def test_existing_features_fold_in_neighborhood():
    ents = [_existing(1, "Nabbes", "PERSON"), _existing(2, "Worcester College", "ORG")]
    edges = [{"subj": 1, "predicate": "educated_at", "obj": 2}]
    slice_ = _FakeSlice(ents, edges)
    _, feats, keys = ingest._existing_features(slice_)
    nabbes = feats[0]
    assert nabbes["name"] == "Nabbes"
    assert nabbes["rel"] == "educated_at"
    assert nabbes["nbr"] == "Worcester College"
    assert keys[0] == {"PERSON|Nabbes"}


def test_new_features_from_batch_edges():
    batch = {
        "entities": [_batch_entity(0, "Nabbes", "PERSON"), _batch_entity(1, "Oxford", "ORG")],
        "edges": [{"subj_local": 0, "predicate": "studied_at", "obj_local": 1}],
    }
    _, feats = ingest._new_features(batch)
    assert feats[0]["rel"] == "studied_at"
    assert feats[0]["nbr"] == "Oxford"


# --- embedding-threshold matcher ------------------------------------------

class _StubEmbedder:
    """Maps each text to a fixed vector so cosine is deterministic in tests."""

    def __init__(self, mapping):
        self._m = mapping

    def embed(self, texts):
        import numpy as np
        return np.array([self._m[t] for t in texts], dtype=float)


def test_embed_cluster_groups_by_cosine():
    emb = _StubEmbedder({
        "Thomas Nabbes": [1.0, 0.0],
        "Nabbes": [0.999, 0.045],   # ~1.0 cosine with "Thomas Nabbes"
        "Oxford": [0.0, 1.0],       # orthogonal
    })
    rows = [
        {"surfaces": "Thomas Nabbes", "type": "PERSON"},
        {"surfaces": "Nabbes", "type": "PERSON"},
        {"surfaces": "Oxford", "type": "ORG"},
    ]
    assert ingest._embed_cluster(rows, emb, threshold=0.9) == [[0, 1]]


def test_embed_cluster_same_type_guard():
    # Near-identical vectors but different types -> NOT clustered.
    emb = _StubEmbedder({"Lincoln(p)": [1.0, 0.0], "Lincoln(o)": [1.0, 0.0]})
    rows = [
        {"surfaces": "Lincoln(p)", "type": "PERSON"},
        {"surfaces": "Lincoln(o)", "type": "PLACE"},
    ]
    assert ingest._embed_cluster(rows, emb, threshold=0.9) == []


def test_cross_doc_link_uses_embedder_when_given():
    store = _FakeStore([_existing(1, "Thomas Nabbes", "PERSON")])
    batch = {"entities": [_batch_entity(0, "Nabbes", "PERSON")], "edges": []}
    emb = _StubEmbedder({"Thomas Nabbes": [1.0, 0.0], "Nabbes": [0.999, 0.045]})
    linked = ingest._cross_doc_link(store, batch, at=5, embedder=emb)
    assert linked == 1
    assert "PERSON|Thomas Nabbes" in batch["entities"][0]["record_keys"]


# --- linking via injected matcher -----------------------------------------

def test_existing_and_new_in_one_cluster_links():
    store = _FakeStore([_existing(1, "Thomas Nabbes", "PERSON")])
    batch = {"entities": [_batch_entity(0, "Nabbes", "PERSON")], "edges": []}
    linked = ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[0, 1]])
    assert linked == 1
    assert "PERSON|Thomas Nabbes" in batch["entities"][0]["record_keys"]


def test_matcher_receives_compound_rows_existing_first():
    captured = {}

    def _spy(rows):
        captured["rows"] = rows
        return []

    store = _FakeStore(
        [_existing(1, "Old", "ORG")],
        edges=[],
    )
    batch = {"entities": [_batch_entity(0, "New", "ORG")], "edges": []}
    ingest._cross_doc_link(store, batch, at=5, cluster_fn=_spy)
    rows = captured["rows"]
    assert [r["name"] for r in rows] == ["Old", "New"]
    assert set(rows[0]) == set(ingest._FEATURE_COLS)  # compound key columns present


def test_no_cluster_leaves_keys_untouched():
    store = _FakeStore([_existing(1, "Genesis", "ORG")])
    batch = {"entities": [_batch_entity(0, "Exeter College", "ORG")], "edges": []}
    assert ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: []) == 0
    assert batch["entities"][0]["record_keys"] == ["ORG|Exeter College"]


def test_cluster_without_existing_does_not_inject():
    store = _FakeStore([_existing(1, "Genesis", "ORG")])
    batch = {"entities": [_batch_entity(0, "A", "ORG"), _batch_entity(1, "B", "ORG")], "edges": []}
    # rows: [existing=0, new A=1, new B=2]; cluster the two new ones only.
    assert ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[1, 2]]) == 0


def test_same_type_guard_blocks_cross_type():
    store = _FakeStore([_existing(1, "Lincoln", "PERSON")])
    batch = {"entities": [_batch_entity(0, "Lincoln", "PLACE")], "edges": []}
    linked = ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[0, 1]])
    assert linked == 0
    assert batch["entities"][0]["record_keys"] == ["PLACE|Lincoln"]


def test_empty_existing_is_noop():
    store = _FakeStore([])
    batch = {"entities": [_batch_entity(0, "X", "ORG")], "edges": []}
    assert ingest._cross_doc_link(store, batch, at=5, cluster_fn=lambda rows: [[0]]) == 0


def test_store_without_as_of_is_a_noop():
    class _Bare:
        pass

    batch = {"entities": [_batch_entity(0, "X", "ORG")], "edges": []}
    assert ingest._cross_doc_link(_Bare(), batch, at=5) == 0


def test_gating_default_off(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_CROSS_DOC_LINK", raising=False)
    assert ingest._cross_doc_link_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_CROSS_DOC_LINK", "1")
    assert ingest._cross_doc_link_enabled() is True
    monkeypatch.setenv("GOLDENGRAPH_CROSS_DOC_LINK", "0")
    assert ingest._cross_doc_link_enabled() is False
