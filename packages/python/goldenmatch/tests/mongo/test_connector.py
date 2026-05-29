"""Tests for the MongoDB connector.

Uses ``mongomock`` so no live MongoDB instance is required. The
connector takes a ``pymongo``-shaped client at runtime, and
mongomock is API-compatible enough to exercise every method.
"""
from __future__ import annotations

import pytest

# Skip the whole module if mongomock isn't installed. CI installs it
# via the `mongo` test extra; local dev pulls it with
# `pip install goldenmatch[mongo,dev]`.
mongomock = pytest.importorskip("mongomock")


def _stub_pymongo_module(monkeypatch, client_factory):
    """Make ``import pymongo`` return a module whose
    ``MongoClient(uri)`` returns the supplied client."""
    import sys
    import types

    fake = types.ModuleType("pymongo")
    fake.MongoClient = client_factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymongo", fake)


@pytest.fixture
def mongo_connector(monkeypatch):
    """A MongoConnector wired up to a single mongomock client.

    Yields (connector, client) so tests can seed + inspect docs.
    """
    client = mongomock.MongoClient()
    _stub_pymongo_module(monkeypatch, lambda uri: client)

    from goldenmatch.connectors.mongo import MongoConnector
    connector = MongoConnector(
        {"credentials_env": None},
    )
    yield connector, client


def test_read_flattens_nested(mongo_connector):
    connector, client = mongo_connector
    client["gm"]["customers"].insert_many([
        {"_id": "1", "name": "Alice", "address": {"city": "Raleigh"}},
        {"_id": "2", "name": "Bob",   "address": {"city": "Durham"}},
    ])

    lf = connector.read({
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
    })
    df = lf.collect()
    assert df.height == 2
    cols = set(df.columns)
    # _id, name, address.city -- nested field flattened.
    assert {"_id", "name", "address.city"} <= cols


def test_read_unflattened_preserves_nested(mongo_connector):
    connector, client = mongo_connector
    client["gm"]["customers"].insert_one(
        {"_id": "1", "address": {"city": "Raleigh"}},
    )
    lf = connector.read({
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
        "flatten": False,
    })
    df = lf.collect()
    assert "address" in df.columns
    assert "address.city" not in df.columns


def test_read_filter_and_limit(mongo_connector):
    connector, client = mongo_connector
    client["gm"]["customers"].insert_many([
        {"_id": str(i), "name": f"P{i}", "score": i} for i in range(10)
    ])
    lf = connector.read({
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
        "filter": {"score": {"$gte": 5}},
        "limit": 2,
    })
    df = lf.collect()
    assert df.height == 2


def test_read_empty_returns_empty_frame(mongo_connector):
    connector, client = mongo_connector
    lf = connector.read({
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "missing",
    })
    df = lf.collect()
    assert df.height == 0


def test_read_missing_database_errors(mongo_connector):
    from goldenmatch.connectors.base import ConnectorError

    connector, _ = mongo_connector
    with pytest.raises(ConnectorError, match="'database'"):
        connector.read({"connection": "mongodb://fake", "collection": "x"})


def test_write_append_inserts(mongo_connector):
    import polars as pl

    connector, client = mongo_connector
    df = pl.DataFrame({"name": ["Alice", "Bob"], "city": ["R", "D"]})
    connector.write(df, {
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
    })

    docs = list(client["gm"]["customers"].find())
    assert len(docs) == 2
    names = {d["name"] for d in docs}
    assert names == {"Alice", "Bob"}


def test_write_replace_clears_first(mongo_connector):
    import polars as pl

    connector, client = mongo_connector
    client["gm"]["customers"].insert_many([{"old": True}])

    df = pl.DataFrame({"name": ["Carol"]})
    connector.write(df, {
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
        "mode": "replace",
    })

    docs = list(client["gm"]["customers"].find())
    assert len(docs) == 1
    assert docs[0]["name"] == "Carol"


def test_write_upsert_keys_on_field(mongo_connector):
    import polars as pl

    connector, client = mongo_connector
    client["gm"]["customers"].insert_one(
        {"id": "u1", "name": "Old", "city": "R"},
    )

    df = pl.DataFrame({"id": ["u1", "u2"], "name": ["New", "Bob"]})
    connector.write(df, {
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
        "mode": "upsert",
        "key": "id",
    })

    docs = {d["id"]: d for d in client["gm"]["customers"].find()}
    # u1 updated in place; u2 inserted.
    assert docs["u1"]["name"] == "New"
    assert docs["u2"]["name"] == "Bob"


def test_write_strips_internal_columns_by_default(mongo_connector):
    import polars as pl

    connector, client = mongo_connector
    df = pl.DataFrame({
        "name": ["Alice"], "__row_id__": [0], "__source__": ["test"],
    })
    connector.write(df, {
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
    })
    doc = client["gm"]["customers"].find_one()
    assert "__row_id__" not in doc
    assert "__source__" not in doc
    assert doc["name"] == "Alice"


def test_write_upsert_requires_key(mongo_connector):
    import polars as pl
    from goldenmatch.connectors.base import ConnectorError

    connector, _ = mongo_connector
    df = pl.DataFrame({"name": ["x"]})
    with pytest.raises(ConnectorError, match="upsert requires"):
        connector.write(df, {
            "connection": "mongodb://fake",
            "database": "gm",
            "collection": "customers",
            "mode": "upsert",
        })


def test_write_empty_frame_is_noop(mongo_connector):
    import polars as pl

    connector, client = mongo_connector
    df = pl.DataFrame({"name": []})
    connector.write(df, {
        "connection": "mongodb://fake",
        "database": "gm",
        "collection": "customers",
    })
    assert client["gm"]["customers"].count_documents({}) == 0


def test_load_connector_registers_mongo(monkeypatch):
    """``load_connector("mongo", ...)`` resolves via the _BUILTIN
    table without requiring pymongo at import time."""
    # Stub pymongo so the import inside MongoConnector._connect doesn't
    # explode -- we never actually call read/write here.
    _stub_pymongo_module(monkeypatch, lambda uri: mongomock.MongoClient())

    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.mongo import MongoConnector

    conn = load_connector("mongo", {"credentials_env": None})
    assert isinstance(conn, MongoConnector)
    conn_alias = load_connector("mongodb", {"credentials_env": None})
    assert isinstance(conn_alias, MongoConnector)
