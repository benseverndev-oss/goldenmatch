"""Semantic retrieval API (#1089).

Exercised with the zero-config in-house embedder (deterministic, no torch /
cloud) + ANNBlocker's numpy fallback, so it runs anywhere. Identical strings
embed to identical unit vectors, so an exact-text query scores ~1.0 against its
row -- a robust deterministic anchor regardless of the (untrained) projection.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.retrieval import RetrievedRecord, retrieve_similar_records


def _corpus():
    return pl.DataFrame({
        "__row_id__": [10, 11, 12],
        "title": [
            "red apple pie recipe",
            "blue car engine repair",
            "green apple tart bake",
        ],
        "cat": ["food", "auto", "food"],
    })


def test_exact_text_query_ranks_its_row_first():
    res = retrieve_similar_records(_corpus(), "blue car engine repair", "title", k=3)
    assert res
    assert isinstance(res[0], RetrievedRecord)
    assert res[0].row_id == 11
    assert res[0].score == pytest.approx(1.0, abs=1e-4)
    # The record payload drops internal columns.
    assert res[0].record["cat"] == "auto"
    assert "__row_id__" not in res[0].record


def test_threshold_keeps_only_strong_matches():
    res = retrieve_similar_records(
        _corpus(), "blue car engine repair", "title", k=3, threshold=0.999,
    )
    assert len(res) == 1
    assert res[0].row_id == 11


def test_k_caps_results():
    res = retrieve_similar_records(_corpus(), "apple", "title", k=1)
    assert len(res) <= 1


def test_filters_prefilter_before_embedding():
    # Restrict to food rows: the car row (11) is filtered out BEFORE embedding,
    # so even a car-text query can never return it. threshold=-1.0 keeps every
    # filtered row, proving exclusion is by the filter, not by a low score.
    res = retrieve_similar_records(
        _corpus(), "blue car engine repair", "title", k=5,
        filters={"cat": "food"}, threshold=-1.0,
    )
    assert {r.row_id for r in res} == {10, 12}
    assert all(r.record["cat"] == "food" for r in res)


def test_filter_on_missing_value_yields_nothing():
    res = retrieve_similar_records(
        _corpus(), "apple", "title", filters={"cat": "nonexistent"},
    )
    assert res == []


def test_blank_query_and_empty_frame_return_empty():
    assert retrieve_similar_records(_corpus(), "", "title") == []
    empty = pl.DataFrame({"__row_id__": [], "title": []},
                         schema={"__row_id__": pl.Int64, "title": pl.Utf8})
    assert retrieve_similar_records(empty, "apple", "title") == []


def test_missing_column_raises():
    with pytest.raises(ValueError):
        retrieve_similar_records(_corpus(), "apple", "nope")


def test_numpy_fallback_matches(monkeypatch):
    # Force the no-FAISS path; the exact-text query must still rank its row first.
    monkeypatch.setattr(
        "goldenmatch.core.ann_blocker._HAS_FAISS", False, raising=False,
    )
    res = retrieve_similar_records(_corpus(), "red apple pie recipe", "title", k=3)
    assert res[0].row_id == 10
    assert res[0].score == pytest.approx(1.0, abs=1e-4)


def test_no_row_id_column_uses_position():
    df = pl.DataFrame({"title": ["alpha widget", "beta gadget"]})
    res = retrieve_similar_records(df, "beta gadget", "title", k=2)
    assert res[0].row_id == 1  # position of "beta gadget"


def test_explicit_embedder_is_used():
    import numpy as np

    class StubEmbedder:
        # Maps each distinct text to a fixed unit vector; "match" aligns with row 1.
        def embed_column(self, values, cache_key):
            table = {
                "match": np.array([1.0, 0.0]),
                "a": np.array([0.0, 1.0]),
                "b": np.array([1.0, 0.0]),
            }
            return np.array([table.get(v, np.array([0.0, 0.0])) for v in values])

    df = pl.DataFrame({"__row_id__": [0, 1], "title": ["a", "b"]})
    res = retrieve_similar_records(df, "match", "title", k=2, embedder=StubEmbedder())
    assert res[0].row_id == 1
    assert res[0].score == pytest.approx(1.0, abs=1e-6)


def test_result_as_dict_serializable():
    import json

    res = retrieve_similar_records(_corpus(), "green apple tart bake", "title")
    blob = json.dumps([r.as_dict() for r in res])
    assert "row_id" in blob and "score" in blob


# --- fuzzy (lexical) retrieval, powered by goldenfuzz with a pure-Python fallback


def test_retrieve_similar_fuzzy_ranks_lexically():
    import polars as pl

    from goldenmatch.core.retrieval import retrieve_similar_fuzzy

    df = pl.DataFrame(
        {
            "name": ["Jonathan Smith", "Jon Smyth", "Jane Doe", "J. Smith", "Johnathan Smithe"],
            "city": ["NYC", "NYC", "LA", "NYC", "SF"],
        }
    )
    res = retrieve_similar_fuzzy(df, "Jonathan Smith", "name", k=3, scorer="jaro_winkler")
    assert [r.record["name"] for r in res][:1] == ["Jonathan Smith"]  # exact match first
    assert res[0].score == 1.0
    assert all(res[i].score >= res[i + 1].score for i in range(len(res) - 1))  # descending
    assert len(res) <= 3

    # metadata pre-filter + threshold
    nyc = retrieve_similar_fuzzy(
        df, "Jonathan Smith", "name", k=5, scorer="jaro_winkler", filters={"city": "NYC"}, threshold=0.7
    )
    assert {r.record["city"] for r in nyc} == {"NYC"}
    assert all(r.score >= 0.7 for r in nyc)


def test_retrieve_similar_fuzzy_fallback_matches_goldenfuzz():
    # The pure-Python fallback must rank bit-identically to the goldenfuzz wheel
    # (both are the goldenfuzz-core math).
    import builtins

    from goldenmatch.core.retrieval import _fuzzy_extract

    vals = ["Jonathan Smith", "Jon Smyth", "Jane Doe", "J. Smith", "Johnathan Smithe"]
    goldenfuzz = pytest.importorskip("goldenfuzz")
    native = goldenfuzz.extract("Jonathan Smith", vals, scorer="jaro_winkler", score_cutoff=0.0, limit=0)

    real_import = builtins.__import__

    def _no_goldenfuzz(name, *a, **k):
        if name == "goldenfuzz":
            raise ImportError
        return real_import(name, *a, **k)

    builtins.__import__ = _no_goldenfuzz
    try:
        fallback = _fuzzy_extract("Jonathan Smith", vals, "jaro_winkler", 0.0, 0)
    finally:
        builtins.__import__ = real_import

    assert native == fallback  # same indices AND same float bits


def test_retrieve_similar_fuzzy_bad_scorer():
    import polars as pl

    from goldenmatch.core.retrieval import retrieve_similar_fuzzy

    with pytest.raises(ValueError):
        retrieve_similar_fuzzy(pl.DataFrame({"n": ["a"]}), "a", "n", scorer="nope")
