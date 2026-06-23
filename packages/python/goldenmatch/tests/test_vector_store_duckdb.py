"""DuckDB-HNSW vector backend (#1088).

Mirrors the ``VectorIndex`` contract (``tests/test_vector_index.py``) on the
DuckDB backend: build/query/filters/threshold/incremental-add/embedding-cache/
persistence-across-processes/id_column. Offline + deterministic -- the
zero-config in-house embedder needs no network/torch, and ranking uses DuckDB's
core ``array_cosine_similarity`` (the ``vss`` HNSW index is an optional
accelerator, not required for correctness).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys

import numpy as np
import polars as pl
import pytest

duckdb = pytest.importorskip("duckdb")

from goldenmatch.core.retrieval import RetrievedRecord
from goldenmatch.core.vector_store import DuckDBVectorIndex

CORP = pl.DataFrame(
    {
        "name": ["acme corporation", "globex incorporated", "initech systems"],
        "city": ["NYC", "SF", "Austin"],
    }
)


class _CountingEmbedder:
    """Deterministic cross-call-stable stub that records every text embedded."""

    def __init__(self, dim: int = 16):
        self.dim = dim
        self.embedded: list[str] = []

    def embed_column(self, values, cache_key):  # noqa: ARG002
        self.embedded.extend(values)
        return np.stack([self._vec(v) for v in values]).astype(np.float32)

    def _vec(self, text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
        v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) or 1.0)


def _idx(tmp_path, name="vi.duckdb", **kw):
    return DuckDBVectorIndex(str(tmp_path / name), column="name", **kw)


# ── build + query ────────────────────────────────────────────────────────────


def test_build_then_query_anchors_exact_text(tmp_path):
    idx = _idx(tmp_path).build(CORP)
    assert len(idx) == 3
    hits = idx.query("acme corporation", k=3)
    assert hits and hits[0].record["name"] == "acme corporation"
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    assert isinstance(hits[0], RetrievedRecord)


def test_query_k_cap_and_threshold(tmp_path):
    idx = _idx(tmp_path).build(CORP)
    assert len(idx.query("acme", k=1)) == 1
    assert idx.query("acme", k=5, threshold=1.1) == []


def test_filters_pre_exclude(tmp_path):
    idx = _idx(tmp_path).build(CORP)
    hits = idx.query("incorporated", k=5, filters={"city": "SF"})
    assert [h.record["name"] for h in hits] == ["globex incorporated"]
    assert idx.query("acme", k=5, filters={"city": "Mars"}) == []


def test_empty_query_and_empty_index(tmp_path):
    idx = _idx(tmp_path).build(CORP)
    assert idx.query("", k=3) == []
    empty = _idx(tmp_path, name="empty.duckdb")
    assert len(empty) == 0
    assert empty.query("acme", k=3) == []


# ── persistence: survives reload + cross-process ─────────────────────────────


def test_persist_and_reload_in_process(tmp_path):
    p = str(tmp_path / "vi.duckdb")
    DuckDBVectorIndex(p, column="name").build(CORP).save().close()
    reloaded = DuckDBVectorIndex.load(p)
    assert len(reloaded) == 3
    assert reloaded.column == "name"
    a = reloaded.query("acme corporation", k=1)
    assert a[0].record["name"] == "acme corporation"
    assert a[0].score == pytest.approx(1.0, abs=1e-3)


def test_index_survives_across_processes(tmp_path):
    p = str(tmp_path / "vi.duckdb")
    code = (
        "import polars as pl;"
        "from goldenmatch.core.vector_store import DuckDBVectorIndex;"
        "df = pl.DataFrame({'name': ['alpha widget','beta gadget','gamma gizmo']});"
        f"DuckDBVectorIndex({p!r}, column='name').build(df).save().close()"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)
    idx = DuckDBVectorIndex.load(p)
    assert len(idx) == 3
    hits = idx.query("alpha widget", k=1)
    assert hits and hits[0].record["name"] == "alpha widget"


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        DuckDBVectorIndex.load(str(tmp_path / "nope.duckdb"))


def test_open_loads_or_creates(tmp_path):
    p = str(tmp_path / "vi.duckdb")
    created = DuckDBVectorIndex.open(p, column="name")
    assert len(created) == 0
    created.build(CORP).save().close()
    reopened = DuckDBVectorIndex.open(p)
    assert len(reopened) == 3


# ── incremental add + embedding cache ────────────────────────────────────────


def test_incremental_add_grows_and_is_queryable(tmp_path):
    idx = _idx(tmp_path).build(CORP)
    idx.add(pl.DataFrame({"name": ["umbrella corp"], "city": ["Raccoon"]}))
    assert len(idx) == 4
    assert idx.query("umbrella corp", k=1)[0].record["name"] == "umbrella corp"
    assert idx.query("acme corporation", k=1)[0].record["name"] == "acme corporation"


def test_add_on_empty_index_builds(tmp_path):
    idx = _idx(tmp_path)
    idx.add(CORP)
    assert len(idx) == 3


def test_embedding_cache_never_reembeds_a_text(tmp_path):
    stub = _CountingEmbedder()
    dupe = pl.DataFrame({"name": ["acme", "globex", "acme"]})
    idx = _idx(tmp_path, embedder=stub).build(dupe)
    idx.add(pl.DataFrame({"name": ["acme", "initech"]}))
    idx.query("acme", k=1)
    assert sorted(stub.embedded) == sorted(set(stub.embedded))
    assert set(stub.embedded) == {"acme", "globex", "initech"}


def test_reload_repopulates_cache_so_add_skips_known_text(tmp_path):
    p = str(tmp_path / "vi.duckdb")
    DuckDBVectorIndex(p, column="name", embedder=_CountingEmbedder()).build(CORP).save().close()
    stub = _CountingEmbedder()
    reloaded = DuckDBVectorIndex.load(p, embedder=stub)
    reloaded.add(pl.DataFrame({"name": ["acme corporation", "brand new co"]}))
    assert stub.embedded == ["brand new co"]


def test_id_column_used_for_row_id(tmp_path):
    df = CORP.with_columns(pl.Series("pk", [100, 200, 300], dtype=pl.Int64))
    idx = _idx(tmp_path).build(df, id_column="pk")
    hits = idx.query("globex incorporated", k=1)
    assert hits[0].row_id == 200
    assert "pk" in hits[0].record


def test_in_memory_backend_works():
    idx = DuckDBVectorIndex(":memory:", column="name").build(CORP)
    assert idx.query("initech systems", k=1)[0].record["name"] == "initech systems"
    idx.save()  # no-op for :memory:, must not raise
