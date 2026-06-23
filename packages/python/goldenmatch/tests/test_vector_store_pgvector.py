"""pgvector (Postgres) vector backend (#1088).

The live contract suite needs a Postgres with the ``vector`` extension; it runs
only when ``GOLDENMATCH_TEST_PG_DSN`` is set (e.g.
``postgresql://postgres@localhost/goldenmatch_test``) and ``psycopg`` +
``pgvector`` are installed. Without those it skips -- CI has no pgvector, so the
DuckDB suite is the in-CI parity gate for the backend abstraction. The
import-guard test below runs everywhere.
"""
from __future__ import annotations

import importlib.util
import os

import polars as pl
import pytest
from goldenmatch.core.retrieval import RetrievedRecord

_DSN = os.environ.get("GOLDENMATCH_TEST_PG_DSN")
_HAS_PG = (
    importlib.util.find_spec("psycopg") is not None
    and importlib.util.find_spec("pgvector") is not None
)

CORP = pl.DataFrame(
    {
        "name": ["acme corporation", "globex incorporated", "initech systems"],
        "city": ["NYC", "SF", "Austin"],
    }
)

pgmark = pytest.mark.skipif(
    not (_DSN and _HAS_PG),
    reason="needs GOLDENMATCH_TEST_PG_DSN + psycopg + pgvector",
)


def test_import_error_is_clean_without_deps():
    """When psycopg/pgvector are absent, construction raises a helpful ImportError
    (not an opaque ModuleNotFoundError mid-method)."""
    if _HAS_PG:
        pytest.skip("psycopg + pgvector present; the missing-deps path can't be exercised")
    from goldenmatch.core.vector_store import PgVectorIndex

    with pytest.raises(ImportError, match="goldenmatch\\[pgvector\\]"):
        PgVectorIndex("postgresql://localhost/db")


def _make(table="gm_vectors_test", **kw):
    from goldenmatch.core.vector_store import PgVectorIndex

    return PgVectorIndex(_DSN, table=table, column="name", **kw)


@pgmark
def test_build_query_filter_threshold():
    idx = _make().build(CORP)
    try:
        assert idx.size == 3
        hits = idx.query("acme corporation", k=3)
        assert isinstance(hits[0], RetrievedRecord)
        assert hits[0].record["name"] == "acme corporation"
        assert hits[0].score == pytest.approx(1.0, abs=1e-3)
        assert len(idx.query("acme", k=1)) == 1
        assert idx.query("acme", k=5, threshold=1.1) == []
        sf = idx.query("incorporated", k=5, filters={"city": "SF"})
        assert [h.record["name"] for h in sf] == ["globex incorporated"]
        assert idx.query("acme", k=5, filters={"city": "Mars"}) == []
    finally:
        idx.close()


@pgmark
def test_incremental_add_and_reopen():
    from goldenmatch.core.vector_store import PgVectorIndex

    idx = _make().build(CORP)
    idx.add(pl.DataFrame({"name": ["umbrella corp"], "city": ["Raccoon"]}))
    assert idx.size == 4
    idx.close()
    # a fresh connection sees the persisted rows.
    reopened = PgVectorIndex.load(_DSN, table="gm_vectors_test")
    try:
        assert reopened.size == 4
        assert reopened.query("umbrella corp", k=1)[0].record["name"] == "umbrella corp"
    finally:
        reopened.close()
