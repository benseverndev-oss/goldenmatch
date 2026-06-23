"""Backend-selection seam + shared marshaling for the vector backends (#1088).

Covers ``infer_backend`` / ``open_vector_index`` dispatch and the backend-shared
helpers (``embed_texts`` cache, ``prep_rows``). The pgvector branch is exercised
in ``test_vector_store_pgvector.py`` (skip-guarded on a live DSN).
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from goldenmatch.core.retrieval import RetrievedRecord
from goldenmatch.core.vector_store import (
    VectorStore,
    embed_texts,
    infer_backend,
    open_vector_index,
    prep_rows,
)

CORP = pl.DataFrame({"name": ["acme corp", "globex inc"], "city": ["NYC", "SF"]})


# ── infer_backend ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "location,expected",
    [
        ("postgresql://u:p@host/db", "pgvector"),
        ("postgres://u@host/db", "pgvector"),
        ("host=localhost dbname=gm", "pgvector"),
        ("/tmp/index.duckdb", "duckdb"),
        ("relative/path.ddb", "duckdb"),
        ("duckdb:/tmp/x", "duckdb"),
        ("/tmp/some_dir", "local"),
        (".goldenmatch_vectors", "local"),
    ],
)
def test_infer_backend(location, expected):
    assert infer_backend(location) == expected


# ── open_vector_index dispatch ───────────────────────────────────────────────


def test_factory_local_roundtrip(tmp_path):
    loc = str(tmp_path / "vidir")
    idx = open_vector_index(loc, backend="local", column="name")
    idx.build(CORP).save()
    reopened = open_vector_index(loc, backend="local")
    assert reopened.size == 2
    hits = reopened.query("acme corp", k=1)
    assert isinstance(hits[0], RetrievedRecord)
    assert hits[0].record["name"] == "acme corp"


def test_factory_auto_picks_local_for_dir(tmp_path):
    loc = str(tmp_path / "vidir")
    idx = open_vector_index(loc, column="name")  # auto
    assert type(idx).__name__ == "VectorIndex"


def test_factory_auto_picks_duckdb_for_file(tmp_path):
    pytest.importorskip("duckdb")
    loc = str(tmp_path / "vi.duckdb")
    idx = open_vector_index(loc, column="name")  # auto -> duckdb
    assert type(idx).__name__ == "DuckDBVectorIndex"
    idx.build(CORP)
    assert idx.query("globex inc", k=1)[0].record["name"] == "globex inc"


def test_factory_duckdb_prefix_strips(tmp_path):
    pytest.importorskip("duckdb")
    loc = "duckdb:" + str(tmp_path / "vi.duckdb")
    idx = open_vector_index(loc, column="name")
    assert type(idx).__name__ == "DuckDBVectorIndex"
    assert idx.path.endswith("vi.duckdb") and not idx.path.startswith("duckdb:")


def test_factory_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown backend"):
        open_vector_index(str(tmp_path / "x"), backend="redis")


def test_backends_satisfy_protocol(tmp_path):
    pytest.importorskip("duckdb")
    local = open_vector_index(str(tmp_path / "d"), backend="local", column="name")
    duck = open_vector_index(str(tmp_path / "v.duckdb"), backend="duckdb", column="name")
    assert isinstance(local, VectorStore)
    assert isinstance(duck, VectorStore)


# ── shared helpers ───────────────────────────────────────────────────────────


class _Stub:
    def __init__(self):
        self.embedded = []

    def embed_column(self, values, cache_key):  # noqa: ARG002
        self.embedded.extend(values)
        return np.ones((len(values), 4), dtype=np.float32)


def test_embed_texts_caches_unique_only():
    stub = _Stub()
    cache: dict[str, np.ndarray] = {}
    out = embed_texts(stub, "inhouse", cache, ["a", "b", "a"])
    assert out.shape == (3, 4)
    assert stub.embedded == ["a", "b"]  # 'a' embedded once
    # second call reuses cache entirely
    embed_texts(stub, "inhouse", cache, ["a", "b"])
    assert stub.embedded == ["a", "b"]


def test_embed_texts_empty():
    assert embed_texts(_Stub(), "inhouse", {}, []).shape == (0, 0)


def test_prep_rows_record_excludes_internal_and_numbers_from_base():
    df = pl.DataFrame({"name": ["x", "y"], "__internal__": [1, 2]})
    row_ids, texts, records = prep_rows(df, "name", None, base=10)
    assert row_ids == [10, 11]
    assert texts == ["x", "y"]
    import json

    assert json.loads(records[0]) == {"name": "x"}  # internal column dropped


def test_prep_rows_uses_id_column():
    df = pl.DataFrame({"name": ["x"], "pk": [99]})
    row_ids, _, _ = prep_rows(df, "name", "pk", base=0)
    assert row_ids == [99]


def test_prep_rows_missing_column_raises():
    with pytest.raises(ValueError, match="not in dataframe"):
        prep_rows(pl.DataFrame({"a": [1]}), "missing", None, base=0)
