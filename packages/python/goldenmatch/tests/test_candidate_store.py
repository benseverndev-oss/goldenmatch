"""Tests for pluggable candidate stores + match_one store routing."""

from __future__ import annotations

import importlib.util

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import candidate_store as cs
from goldenmatch.core.candidate_store import (
    FrameCandidateStore,
    resolve_base_store_kind,
)
from goldenmatch.core.match_one import match_one

_HAS_LANCE = importlib.util.find_spec("lance") is not None


def _df(n: int = 6) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": list(range(100, 100 + n)),
            "__block_key__": [f"k{i % 3}" for i in range(n)],
            "name": [f"name_{i}" for i in range(n)],
            "zip": [f"{90000 + (i % 3)}" for i in range(n)],
        }
    )


# ---- FrameCandidateStore ------------------------------------------------------

def test_frame_store_take_positions():
    store = FrameCandidateStore(_df())
    rows, ids = store.take([0, 2, 5])
    assert ids == [100, 102, 105]
    assert [r["name"] for r in rows] == ["name_0", "name_2", "name_5"]


def test_frame_store_take_drops_out_of_range():
    store = FrameCandidateStore(_df(4))
    rows, ids = store.take([1, 99, 3])
    assert ids == [101, 103]
    assert len(rows) == 2


def test_frame_store_take_empty():
    store = FrameCandidateStore(_df())
    assert store.take([]) == ([], [])
    assert store.take([999]) == ([], [])


def test_frame_store_gather_block():
    store = FrameCandidateStore(_df(6))
    rows, ids = store.gather_block("k0")
    assert ids == [100, 103]  # positions 0 and 3 -> k0
    assert all(r["__block_key__"] == "k0" for r in rows)


def test_frame_store_len():
    assert len(FrameCandidateStore(_df(7))) == 7


# ---- match_one ANN routing ----------------------------------------------------

class _FakeEmbedder:
    def embed_column(self, texts, cache_key=None):
        return [[0.0, 1.0] for _ in texts]


class _FakeANN:
    def __init__(self, positions):
        self._positions = positions

    def query_one(self, _embedding):
        return [(p, 1.0) for p in self._positions]


def _mk():
    return MatchkeyConfig(
        name="t",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def test_match_one_ann_finds_exact_name():
    df = _df(6)
    # candidate positions 1,4 ; record name equals name_4 -> exact match on it
    matches = match_one(
        {"name": "name_4"}, df, _mk(),
        ann_blocker=_FakeANN([1, 4]), embedder=_FakeEmbedder(), ann_column="name", top_k=5,
    )
    ids = [rid for rid, _ in matches]
    assert 104 in ids  # row_id at position 4


def test_match_one_ann_does_not_materialize_full_frame(monkeypatch):
    """Regression lock: the ANN path must NOT df.to_dicts() the whole base per
    probe — only the candidate subset."""
    df = _df(1000)
    seen_heights = []
    orig = pl.DataFrame.to_dicts

    def spy(self, *a, **k):
        seen_heights.append(self.height)
        return orig(self, *a, **k)

    monkeypatch.setattr(pl.DataFrame, "to_dicts", spy)
    match_one(
        {"name": "name_7"}, df, _mk(),
        ann_blocker=_FakeANN([3, 7, 11]), embedder=_FakeEmbedder(), ann_column="name", top_k=3,
    )
    # to_dicts only ever called on the <=3-row candidate subset, never the 1000-row base
    assert seen_heights and max(seen_heights) <= 3


# ---- resolver -----------------------------------------------------------------

def test_resolve_explicit_memory(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_BASE_STORE", raising=False)
    assert resolve_base_store_kind(10**9, configured="memory") == "memory"


def test_resolve_explicit_lance_when_available(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_BASE_STORE", raising=False)
    monkeypatch.setattr(cs, "lance_available", lambda: True)
    assert resolve_base_store_kind(10, configured="lance") == "lance"


def test_resolve_lance_falls_back_when_missing(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_BASE_STORE", raising=False)
    monkeypatch.setattr(cs, "lance_available", lambda: False)
    assert resolve_base_store_kind(10**9, configured="lance") == "memory"


def test_resolve_auto_threshold(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_BASE_STORE", raising=False)
    monkeypatch.setattr(cs, "lance_available", lambda: True)
    assert resolve_base_store_kind(1000, threshold_rows=2_000_000) == "memory"
    assert resolve_base_store_kind(3_000_000, threshold_rows=2_000_000) == "lance"


def test_resolve_env_override(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BASE_STORE", "memory")
    assert resolve_base_store_kind(10**9) == "memory"


# ---- LanceCandidateStore parity ----------------------------------------------

@pytest.mark.skipif(not _HAS_LANCE, reason="lance not installed")
def test_lance_store_matches_frame(tmp_path):
    from goldenmatch.core.candidate_store import LanceCandidateStore

    df = _df(20)
    frame = FrameCandidateStore(df)
    lance_store = LanceCandidateStore.from_frame(df, str(tmp_path / "base.lance"))

    for positions in ([0, 5, 19], [3], [], [7, 7, 2]):
        fr_rows, fr_ids = frame.take(positions)
        ln_rows, ln_ids = lance_store.take(positions)
        assert ln_ids == fr_ids
        assert [r["name"] for r in ln_rows] == [r["name"] for r in fr_rows]

    fr_rows, fr_ids = frame.gather_block("k1")
    ln_rows, ln_ids = lance_store.gather_block("k1")
    assert sorted(ln_ids) == sorted(fr_ids)


@pytest.mark.skipif(not _HAS_LANCE, reason="lance not installed")
def test_match_one_with_lance_store_parity(tmp_path):
    from goldenmatch.core.candidate_store import LanceCandidateStore

    df = _df(20)
    store = LanceCandidateStore.from_frame(df, str(tmp_path / "b.lance"))
    kw = dict(ann_blocker=_FakeANN([2, 9, 14]), embedder=_FakeEmbedder(), ann_column="name", top_k=5)
    mem = match_one({"name": "name_9"}, df, _mk(), **kw)
    lan = match_one({"name": "name_9"}, df, _mk(), store=store, **kw)
    assert mem == lan
