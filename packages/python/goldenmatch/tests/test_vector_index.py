"""Persistent vector index (#1088).

All deterministic and offline: the zero-config in-house embedder + numpy ANN
fallback need no network or torch. The cross-process test builds the index in a
subprocess and queries it here, proving the index genuinely survives across runs.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys

import numpy as np
import polars as pl
import pytest
from goldenmatch.core import ann_blocker
from goldenmatch.core.retrieval import RetrievedRecord
from goldenmatch.core.vector_index import VectorIndex

CORP = pl.DataFrame(
    {
        "name": ["acme corporation", "globex incorporated", "initech systems"],
        "city": ["NYC", "SF", "Austin"],
    }
)


class _CountingEmbedder:
    """Deterministic (cross-call stable) stub that records every text embedded."""

    def __init__(self, dim: int = 16):
        self.dim = dim
        self.embedded: list[str] = []
        self.calls = 0

    def embed_column(self, values, cache_key):  # noqa: ARG002
        self.calls += 1
        self.embedded.extend(values)
        return np.stack([self._vec(v) for v in values]).astype(np.float32)

    def _vec(self, text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
        v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) or 1.0)


# ── build + query ────────────────────────────────────────────────────────────


def test_build_then_query_anchors_exact_text(tmp_path):
    idx = VectorIndex(tmp_path / "vi", column="name").build(CORP)
    assert len(idx) == 3
    hits = idx.query("acme corporation", k=3)
    assert hits and hits[0].record["name"] == "acme corporation"
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    assert isinstance(hits[0], RetrievedRecord)


def test_query_k_cap_and_threshold(tmp_path):
    idx = VectorIndex(tmp_path / "vi", column="name").build(CORP)
    assert len(idx.query("acme", k=1)) == 1
    # a threshold above every score returns nothing.
    assert idx.query("acme", k=5, threshold=1.1) == []


def test_filters_pre_exclude(tmp_path):
    idx = VectorIndex(tmp_path / "vi", column="name").build(CORP)
    hits = idx.query("incorporated", k=5, filters={"city": "SF"})
    assert [h.record["name"] for h in hits] == ["globex incorporated"]
    # a filter that matches nothing yields nothing.
    assert idx.query("acme", k=5, filters={"city": "Mars"}) == []


def test_empty_query_and_empty_index(tmp_path):
    idx = VectorIndex(tmp_path / "vi", column="name").build(CORP)
    assert idx.query("", k=3) == []
    empty = VectorIndex(tmp_path / "empty", column="name")
    assert len(empty) == 0
    assert empty.query("acme", k=3) == []


# ── persistence: survives reload + cross-process ─────────────────────────────


def test_persist_and_reload_in_process(tmp_path):
    d = tmp_path / "vi"
    VectorIndex(d, column="name").build(CORP).save()
    assert (d / "manifest.json").is_file()
    assert (d / "vectors.npy").is_file()
    assert (d / "records.parquet").is_file()

    reloaded = VectorIndex.load(d)
    assert len(reloaded) == 3
    a = reloaded.query("acme corporation", k=1)
    b = VectorIndex(tmp_path / "fresh", column="name").build(CORP).query(
        "acme corporation", k=1
    )
    assert a[0].record == b[0].record
    assert a[0].score == pytest.approx(b[0].score, abs=1e-5)


def test_index_survives_across_processes(tmp_path):
    d = tmp_path / "vi"
    code = (
        "import polars as pl, goldenmatch as gm;"
        "df = pl.DataFrame({'name': ['alpha widget','beta gadget','gamma gizmo']});"
        f"gm.VectorIndex({str(d)!r}, column='name').build(df).save()"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)

    idx = VectorIndex.load(d)  # a fresh process loads what the other wrote
    assert len(idx) == 3
    hits = idx.query("alpha widget", k=1)
    assert hits and hits[0].record["name"] == "alpha widget"


def test_manifest_records_model_dim_count(tmp_path):
    import json

    d = tmp_path / "vi"
    idx = VectorIndex(d, model="inhouse", column="name").build(CORP)
    idx.save()
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["model"] == "inhouse"
    assert manifest["column"] == "name"
    assert manifest["count"] == 3
    assert manifest["dim"] == idx.dim


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        VectorIndex.load(tmp_path / "nope")


def test_open_loads_or_creates(tmp_path):
    d = tmp_path / "vi"
    created = VectorIndex.open(d, column="name")
    assert len(created) == 0  # nothing on disk yet
    created.build(CORP).save()
    reopened = VectorIndex.open(d)
    assert len(reopened) == 3


# ── incremental add + embedding cache ────────────────────────────────────────


def test_incremental_add_grows_and_is_queryable(tmp_path):
    idx = VectorIndex(tmp_path / "vi", column="name").build(CORP)
    idx.add(pl.DataFrame({"name": ["umbrella corp"], "city": ["Raccoon"]}))
    assert len(idx) == 4
    assert idx.query("umbrella corp", k=1)[0].record["name"] == "umbrella corp"
    # original rows still retrievable.
    assert idx.query("acme corporation", k=1)[0].record["name"] == "acme corporation"


def test_add_on_empty_index_builds(tmp_path):
    idx = VectorIndex(tmp_path / "vi", column="name")
    idx.add(CORP)
    assert len(idx) == 3


def test_embedding_cache_never_reembeds_a_text(tmp_path):
    stub = _CountingEmbedder()
    dupe = pl.DataFrame({"name": ["acme", "globex", "acme"]})  # 'acme' twice
    idx = VectorIndex(tmp_path / "vi", column="name", embedder=stub).build(dupe)
    # add a row whose text is already indexed + one new text.
    idx.add(pl.DataFrame({"name": ["acme", "initech"]}))
    idx.query("acme", k=1)  # query text 'acme' is already cached too
    # No text is ever embedded twice.
    assert sorted(stub.embedded) == sorted(set(stub.embedded))
    assert set(stub.embedded) == {"acme", "globex", "initech"}


def test_reload_repopulates_cache_so_add_skips_known_text(tmp_path):
    d = tmp_path / "vi"
    # Build + save with a stub so the persisted vectors share the stub's dim.
    VectorIndex(d, column="name", embedder=_CountingEmbedder()).build(CORP).save()
    stub = _CountingEmbedder()
    reloaded = VectorIndex.load(d, embedder=stub)
    # 'acme corporation' is already on disk -> reused, not re-embedded; only the
    # genuinely new text hits the embedder.
    reloaded.add(pl.DataFrame({"name": ["acme corporation", "brand new co"]}))
    assert stub.embedded == ["brand new co"]


# ── numpy fallback parity ────────────────────────────────────────────────────


def test_numpy_fallback_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ann_blocker, "_HAS_FAISS", False)
    idx = VectorIndex(tmp_path / "vi", column="name").build(CORP)
    hits = idx.query("acme corporation", k=1)
    assert hits and hits[0].record["name"] == "acme corporation"


def test_id_column_used_for_row_id(tmp_path):
    df = CORP.with_columns(pl.Series("pk", [100, 200, 300], dtype=pl.Int64))
    idx = VectorIndex(tmp_path / "vi", column="name").build(df, id_column="pk")
    hits = idx.query("globex incorporated", k=1)
    assert hits[0].row_id == 200
    assert "pk" in hits[0].record  # non-internal columns are preserved
