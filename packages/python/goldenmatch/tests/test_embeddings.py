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
