"""Tests for the local/private embedding runtime (goldenmatch.embeddings).

Exercises the provider contract, the model_id+text_hash cache, and dispatch
using the dependency-free ``none`` provider plus an injected fake provider — no
models, no network.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.embeddings import (
    EmbeddingCache,
    NoneProvider,
    embed_records,
    normalize_text,
    resolve_provider,
    text_hash,
)


class CountingProvider:
    """Deterministic fake provider that records every embed() batch."""

    def __init__(self, dim: int = 4, model_id: str = "fake") -> None:
        self.dim = dim
        self.model_id = model_id
        self.batches: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.batches.append(list(texts))
        # vector derived from the text so each distinct string is distinguishable
        return np.array(
            [[float(len(t)), float(sum(map(ord, t)) % 97), 0.0, 1.0] for t in texts],
            dtype=np.float32,
        )


def test_normalize_text():
    assert normalize_text("  Foo   BAR ") == "foo bar"
    assert normalize_text(None) == ""
    assert normalize_text("A\tB\nC") == "a b c"


def test_text_hash_stable_and_distinct():
    assert text_hash("foo bar") == text_hash("foo bar")
    assert text_hash("foo bar") != text_hash("foo baz")


def test_resolve_provider_names_and_passthrough():
    assert resolve_provider("none").model_id == "none"
    assert resolve_provider("local", model="m").model_id == "local:m"
    assert resolve_provider("openai", model="text-embedding-3-large").model_id == (
        "openai:text-embedding-3-large"
    )
    obj = NoneProvider(dim=8)
    assert resolve_provider(obj) is obj
    with pytest.raises(ValueError):
        resolve_provider("bogus")


def test_none_provider_returns_zeros():
    out = embed_records(["a", "b", "c"], provider="none", dim=16)
    assert out.shape == (3, 16)
    assert out.dtype == np.float32
    assert not out.any()


def test_empty_input():
    out = embed_records([], provider="none", dim=8)
    assert out.shape == (0, 8)


def test_order_preserved_and_shape():
    prov = CountingProvider()
    out = embed_records(["alpha", "be", "gamma"], provider=prov)
    assert out.shape == (3, 4)
    assert out[0][0] == 5.0  # len("alpha")
    assert out[1][0] == 2.0  # len("be")
    assert out[2][0] == 5.0  # len("gamma")


def test_duplicates_embedded_once():
    prov = CountingProvider()
    out = embed_records(["x", "y", "x", "x", "y"], provider=prov)
    assert out.shape == (5, 4)
    # one batch, unique texts only
    assert len(prov.batches) == 1
    assert sorted(prov.batches[0]) == ["x", "y"]
    # repeated rows are identical vectors
    assert np.array_equal(out[0], out[2])
    assert np.array_equal(out[0], out[3])


def test_normalization_collapses_equivalent_texts():
    prov = CountingProvider()
    out = embed_records(["Foo  Bar", "foo bar"], provider=prov)
    assert len(prov.batches) == 1
    assert prov.batches[0] == ["foo bar"]
    assert np.array_equal(out[0], out[1])


def test_cache_hit_skips_provider_across_calls():
    prov = CountingProvider()
    cache = EmbeddingCache()
    embed_records(["a", "b"], provider=prov, cache=cache)
    assert len(prov.batches) == 1
    # second call: everything cached -> provider not invoked again
    out = embed_records(["a", "b"], provider=prov, cache=cache)
    assert len(prov.batches) == 1
    assert out.shape == (2, 4)


def test_model_id_namespaces_cache():
    cache = EmbeddingCache()
    p1 = CountingProvider(model_id="fake-1")
    p2 = CountingProvider(model_id="fake-2")
    embed_records(["a"], provider=p1, cache=cache)
    embed_records(["a"], provider=p2, cache=cache)
    # different model_id -> separate entries, both providers invoked
    assert len(p1.batches) == 1
    assert len(p2.batches) == 1


def test_persistent_cache_roundtrip(tmp_path):
    path = tmp_path / "emb.db"
    prov = CountingProvider()
    first = embed_records(["a", "b", "c"], provider=prov, cache=str(path))
    assert len(prov.batches) == 1

    # new process-equivalent cache from the same file, fresh provider
    prov2 = CountingProvider()
    second = embed_records(["a", "b", "c"], provider=prov2, cache=str(path))
    assert len(prov2.batches) == 0  # all served from disk
    assert np.array_equal(first, second)


def test_no_normalize_keys_on_raw_text():
    prov = CountingProvider()
    out = embed_records(["Foo  Bar", "foo bar"], provider=prov, normalize=False)
    # distinct raw strings -> two embeds
    assert len(prov.batches) == 1
    assert sorted(prov.batches[0]) == ["Foo  Bar", "foo bar"]
    assert not np.array_equal(out[0], out[1])


# ---------------------------------------------------------------------------
# SnowflakeCortexProvider -- no live Snowflake required; we stub the
# connector at the cursor.fetchall() boundary. Live-end-to-end coverage
# lives in the dbt-goldensuite smoke harness against a real account.
# ---------------------------------------------------------------------------


class _FakeCortexConn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self):
        return _FakeCortexCursor(self)


class _FakeCortexCursor:
    """Mimics the slice of snowflake.connector.cursor.SnowflakeCursor that
    SnowflakeCortexProvider._embed_chunk uses."""

    def __init__(self, conn: _FakeCortexConn) -> None:
        self._conn = conn

    def execute(self, sql: str, params):
        self._conn.executed.append((sql, params))
        texts_json, _model = params
        import json as _json
        texts = _json.loads(texts_json)
        # Stable deterministic vector per text -- mimics what real Cortex
        # returns (a Python list of floats), one row per input text.
        self._rows = [
            (i, [float(len(t)), float(sum(map(ord, t)) % 97)] + [0.0] * 766)
            for i, t in enumerate(texts)
        ]

    def fetchall(self):
        return self._rows

    def close(self):  # noqa: D401 - matches connector API
        pass


def test_cortex_provider_basic_shape():
    from goldenmatch.embeddings import SnowflakeCortexProvider

    conn = _FakeCortexConn()
    prov = SnowflakeCortexProvider(connection=conn)
    out = prov.embed(["alpha", "be", "gamma"])
    assert out.shape == (3, 768)
    assert out.dtype == np.float32
    # Cache layer is upstream; the provider itself returns one row per text.
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "SNOWFLAKE.CORTEX.EMBED_TEXT_768" in sql
    assert params[1] == "snowflake-arctic-embed-m-v1.5"


def test_cortex_provider_picks_dim_from_known_model():
    from goldenmatch.embeddings import SnowflakeCortexProvider

    conn = _FakeCortexConn()
    prov = SnowflakeCortexProvider(
        model="snowflake-arctic-embed-l-v2.0", connection=conn,
    )
    assert prov.dim == 1024
    assert "EMBED_TEXT_1024" in prov._fn_name


def test_cortex_provider_unknown_model_requires_dim():
    from goldenmatch.embeddings import SnowflakeCortexProvider

    with pytest.raises(ValueError, match="unknown Snowflake Cortex model"):
        SnowflakeCortexProvider(model="future-model-2027")


def test_cortex_provider_override_model_dim():
    from goldenmatch.embeddings import SnowflakeCortexProvider

    conn = _FakeCortexConn()
    prov = SnowflakeCortexProvider(
        model="future-model-2027", connection=conn, model_dim=512,
    )
    assert prov.dim == 512


def test_cortex_provider_empty_input_returns_zeros():
    from goldenmatch.embeddings import SnowflakeCortexProvider

    conn = _FakeCortexConn()
    prov = SnowflakeCortexProvider(connection=conn)
    out = prov.embed([])
    assert out.shape == (0, 768)
    # No round-trips on empty input.
    assert conn.executed == []


def test_cortex_provider_chunking():
    from goldenmatch.embeddings import SnowflakeCortexProvider

    conn = _FakeCortexConn()
    prov = SnowflakeCortexProvider(connection=conn, chunk_size=3)
    out = prov.embed(["a", "b", "c", "d", "e"])
    assert out.shape == (5, 768)
    # 2 chunks: [a,b,c] + [d,e]
    assert len(conn.executed) == 2


def test_cortex_provider_resolves_via_name():
    from goldenmatch.embeddings import SnowflakeCortexProvider, resolve_provider

    prov = resolve_provider("snowflake_cortex")
    assert isinstance(prov, SnowflakeCortexProvider)
    assert prov.model_id == "snowflake-cortex:snowflake-arctic-embed-m-v1.5"
    # Hyphenated + short alias.
    assert resolve_provider("snowflake-cortex").model_id == prov.model_id
    assert resolve_provider("cortex").model_id == prov.model_id


def test_cortex_provider_namespaces_cache():
    """Parity with the Vertex/OpenAI providers: model_id includes the
    provider+model so swapping providers doesn't reuse foreign vectors."""
    from goldenmatch.embeddings import EmbeddingCache, SnowflakeCortexProvider

    cache = EmbeddingCache()
    c1 = _FakeCortexConn()
    c2 = _FakeCortexConn()
    p1 = SnowflakeCortexProvider(connection=c1, model="e5-base-v2")
    p2 = SnowflakeCortexProvider(connection=c2, model="snowflake-arctic-embed-m-v1.5")
    embed_records(["x"], provider=p1, cache=cache)
    embed_records(["x"], provider=p2, cache=cache)
    # Different models -> each ran its own query.
    assert len(c1.executed) == 1
    assert len(c2.executed) == 1
