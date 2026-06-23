"""GoldenmatchEmbedder must chunk large embed batches under the provider input
cap -- seed_by_query embeds every entity name in one call, which 400s past a few
thousand entities (the N>=30 MuSiQue scaling failure). Pure: a stub provider
records the per-request batch sizes; no network."""
from __future__ import annotations

import numpy as np
from goldengraph.embed import _MAX_EMBED_BATCH, GoldenmatchEmbedder


class _StubProvider:
    def __init__(self):
        self.batch_sizes = []

    def embed(self, texts):
        self.batch_sizes.append(len(texts))
        # 2-d vector encoding the index so we can assert order is preserved
        return np.array([[float(len(t)), 1.0] for t in texts])


def _embedder_with(stub):
    e = GoldenmatchEmbedder()
    e._provider = stub  # bypass lazy resolve_provider
    return e


def test_large_batch_is_chunked_under_cap():
    stub = _StubProvider()
    e = _embedder_with(stub)
    n = _MAX_EMBED_BATCH * 2 + 5
    out = e.embed([f"t{i}" for i in range(n)])
    assert out.shape == (n, 2)
    # every request stayed at/under the cap, and they sum to n
    assert all(b <= _MAX_EMBED_BATCH for b in stub.batch_sizes)
    assert sum(stub.batch_sizes) == n
    assert len(stub.batch_sizes) == 3  # 1000 + 1000 + 5


def test_small_batch_is_one_request():
    stub = _StubProvider()
    out = _embedder_with(stub).embed(["a", "bb", "ccc"])
    assert stub.batch_sizes == [3]
    # order preserved (first col = len of each text)
    assert list(out[:, 0]) == [1.0, 2.0, 3.0]


def test_empty_is_noop():
    stub = _StubProvider()
    out = _embedder_with(stub).embed([])
    assert out.shape == (0, 0)
    assert stub.batch_sizes == []
