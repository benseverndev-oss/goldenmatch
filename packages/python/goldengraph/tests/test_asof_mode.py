"""asof_object + ask(mode='auto') temporal dispatch -- needs goldengraph_native (CI lane)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("goldengraph_native")

from conftest import StubEmbedder, StubLLM  # noqa: E402
from goldengraph.answer import ask, asof_object  # noqa: E402

_BIG = 10**12


def _windowed_store():
    # X works_at Apple for [1,5); X works_at Banana for [5, inf).
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    batch = {
        "entities": [
            {"local_id": 0, "canonical_name": "X", "typ": "concept", "surface_names": ["X"], "record_keys": ["kx"]},
            {"local_id": 1, "canonical_name": "Apple", "typ": "concept", "surface_names": ["Apple"], "record_keys": ["ka"]},
            {"local_id": 2, "canonical_name": "Banana", "typ": "concept", "surface_names": ["Banana"], "record_keys": ["kb"]},
        ],
        "edges": [
            {"subj_local": 0, "predicate": "works_at", "obj_local": 1, "valid_from": 1, "valid_to": 5, "source_refs": []},
            {"subj_local": 0, "predicate": "works_at", "obj_local": 2, "valid_from": 5, "valid_to": None, "source_refs": []},
        ],
        "ingested_at": 1,
    }
    store.append(json.dumps(batch))
    return store


def test_asof_object_flips_across_the_correction():
    store = _windowed_store()
    assert asof_object(store.as_of(3, _BIG), "X", "works_at") == "Apple"
    assert asof_object(store.as_of(7, _BIG), "X", "works_at") == "Banana"


def test_asof_object_none_when_anchor_missing():
    store = _windowed_store()
    assert asof_object(store.as_of(3, _BIG), "Nonexistent", "works_at") is None


def test_ask_auto_routes_temporal_past():
    store = _windowed_store()
    out = ask("As of 3, what does X works_at?", store, llm=StubLLM("UNUSED"),
              embedder=StubEmbedder({}), valid_t=_BIG, tx_t=_BIG, mode="auto")
    assert out == "Apple"


def test_ask_auto_routes_temporal_current():
    store = _windowed_store()
    out = ask("As of 7, what does X works_at?", store, llm=StubLLM("UNUSED"),
              embedder=StubEmbedder({}), valid_t=_BIG, tx_t=_BIG, mode="auto")
    assert out == "Banana"
