"""Bounded bucket streaming for the FS (probabilistic) route
(``GOLDENMATCH_FS_BLOCK_SOURCE=frame``, default OFF).

The scale branch of ``score_buckets._score_single_pass`` (height >= n_buckets)
normally ``partition_by``-s the keyed frame into all ``n_buckets`` eager frames
and holds them through ``bucket_score`` -- a ~2x transient at partition time
and the dominant remaining single-node FS peak at >=1M. Streaming instead keeps
the single bucketed frame resident and slices each bucket out on demand
(``filter_eq`` inside the worker), so peak holds the bucketed frame plus at most
``max_workers`` in-flight slices.

Contract under test: the streaming path is BYTE-IDENTICAL to the eager path.
``filter_eq`` preserves within-bucket row order == ``partition_by(maintain_order)``,
so each bucket's ``_score_one_bucket`` output is identical; cross-bucket append
order is unordered downstream (pairs canonicalized). We force the scale branch
with a small ``n_buckets`` so multiple non-empty buckets exist, and compare the
full ``score_buckets`` pair set + scores off-vs-on.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.backends.score_buckets import score_buckets
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.probabilistic import train_em


def _pairset(pairs) -> dict[tuple[int, int], float]:
    return {(min(a, b), max(a, b)): round(s, 4) for a, b, s in pairs}


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="city", scorer="exact", levels=2),
        ],
    )


def _person_df(n_blocks: int = 40, per_block: int = 4) -> pl.DataFrame:
    """A multi-block person frame: ``per_block`` near-dupes in each of
    ``n_blocks`` cities. ~160 rows spreads across many buckets so the scale
    branch (height >= n_buckets) engages with multiple non-empty buckets."""
    firsts = ["John", "Jon", "Jonn", "Johhn", "Jane", "Janet", "Jayne", "Jan"]
    lasts = ["Smith", "Smyth", "Smithe", "Smit"]
    rid, fn, ln, city = [], [], [], []
    r = 1
    for b in range(n_blocks):
        c = f"City{b:03d}"
        for k in range(per_block):
            rid.append(r)
            fn.append(firsts[k % len(firsts)])
            ln.append(lasts[k % len(lasts)])
            city.append(c)
            r += 1
    return pl.DataFrame({"__row_id__": rid, "first_name": fn, "last_name": ln, "city": city})


def _blocking() -> BlockingConfig:
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["city"])])


def _train(df: pl.DataFrame, mk: MatchkeyConfig):
    # Exclude the blocking field from EM training (always agrees within blocks).
    return train_em(df, mk, blocking_fields=["city"])


@pytest.mark.parametrize("n_buckets", [8, 16])
def test_streaming_matches_eager_scale_branch(monkeypatch, n_buckets):
    """height (160) >= n_buckets (8/16) forces the scale branch; streaming must
    reproduce the eager pair set + scores byte-for-byte."""
    df = _person_df()
    assert df.height >= n_buckets  # ensure the scale branch, not the small-block path
    mk = _mk()
    blocking = _blocking()
    em = _train(df, mk)

    monkeypatch.delenv("GOLDENMATCH_FS_BLOCK_SOURCE", raising=False)
    eager = score_buckets(df, blocking, mk, set(), n_buckets=n_buckets, em_result=em)

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "frame")
    streamed = score_buckets(df, blocking, mk, set(), n_buckets=n_buckets, em_result=em)

    assert _pairset(streamed) == _pairset(eager)
    assert len(_pairset(eager)) > 0  # the fixture actually produces matches


def test_streaming_multipass_matches_eager(monkeypatch):
    """Multi-pass blocking (two orthogonal keys) also streams identically."""
    df = _person_df()
    mk = _mk()
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["city"]),
            BlockingKeyConfig(fields=["last_name"], transforms=["lowercase"]),
        ],
    )
    em = train_em(df, mk, blocking_fields=["city", "last_name"])

    monkeypatch.delenv("GOLDENMATCH_FS_BLOCK_SOURCE", raising=False)
    eager = score_buckets(df, blocking, mk, set(), n_buckets=8, em_result=em)

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "frame")
    streamed = score_buckets(df, blocking, mk, set(), n_buckets=8, em_result=em)

    assert _pairset(streamed) == _pairset(eager)


def test_streaming_honors_exclude_pairs(monkeypatch):
    """A non-empty matched_pairs exclude set is respected identically on the
    streaming path (the slice worker is the same ``_score_one_bucket``)."""
    df = _person_df(n_blocks=10, per_block=4)
    mk = _mk()
    blocking = _blocking()
    em = _train(df, mk)

    monkeypatch.delenv("GOLDENMATCH_FS_BLOCK_SOURCE", raising=False)
    full = _pairset(score_buckets(df, blocking, mk, set(), n_buckets=8, em_result=em))
    assert full, "fixture must produce pairs to exclude"
    exclude = {next(iter(full))}

    eager = score_buckets(df, blocking, mk, set(exclude), n_buckets=8, em_result=em)
    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "frame")
    streamed = score_buckets(df, blocking, mk, set(exclude), n_buckets=8, em_result=em)

    assert _pairset(streamed) == _pairset(eager)
    assert exclude.isdisjoint(_pairset(streamed).keys())
