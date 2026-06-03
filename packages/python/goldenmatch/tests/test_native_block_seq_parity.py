"""Issue #688 follow-up: the native block kernel's sequential path and rayon
path must emit byte-identical pairs.

The kernel (`score_block_pairs_arrow`) now scores small/medium calls in the
calling thread (no rayon) and only fans out to rayon above a candidate-pair
threshold, because rayon's blocking `collect` parked the calling thread on a
futex `LockLatch` for ~190s on some Linux runners (the 44x slowdown). The
threshold is overridable via `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS` (0 = always
rayon, huge = always sequential), which lets this test exercise BOTH paths on
the same input and assert they agree -- and that both agree with the Python
slow path (the source of truth).

The fixture is a SINGLE block (constant blocking key) with mixed similar /
dissimilar names. score_buckets rebuilds __block_key__ from the blocking config,
so a constant key puts every row in one block -> the block is the whole frame,
directly comparable to find_fuzzy_matches (which scores the whole frame). Names
differ within the block so scoring is non-trivial and the threshold actually
filters.

Requires the native kernel; skipped on pure-Python.
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
from goldenmatch.core._native_loader import native_available, native_module
from goldenmatch.core.matchkey import _xform_sig
from goldenmatch.core.scorer import find_fuzzy_matches

_NATIVE_ARROW = native_available() and hasattr(
    native_module(), "score_block_pairs_arrow"
)
pytestmark = pytest.mark.skipif(
    not _NATIVE_ARROW,
    reason="needs the native score_block_pairs_arrow kernel",
)


def _prepared() -> pl.DataFrame:
    # One block (constant "blk") of mixed names: alice/alica/alise are mutually
    # similar (jw > 0.7 -> pairs), robert/robbert pair, xavier matches nobody.
    field = MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)
    col = _xform_sig(field)
    names = ["alice", "alica", "alise", "robert", "robbert", "xavier"]
    return pl.DataFrame({
        "__row_id__": list(range(len(names))),
        "name": names,
        col: names,
        "blk": ["X"] * len(names),
    })


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="t", type="weighted", threshold=0.7,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def _blocking() -> BlockingConfig:
    # Block on the constant column so the whole frame is one block.
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])])


def _run(monkeypatch, min_pairs: str) -> list[tuple[int, int, float]]:
    monkeypatch.setenv("GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS", min_pairs)
    return score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())


def _keys(pairs):
    return sorted((min(a, b), max(a, b)) for a, b, _ in pairs)


def test_sequential_and_rayon_paths_agree(monkeypatch):
    rayon = _run(monkeypatch, "0")           # 0 -> always rayon
    seq = _run(monkeypatch, "100000000000")  # huge -> always sequential
    assert _keys(rayon), "fixture must emit pairs or the parity check is vacuous"
    assert _keys(seq) == _keys(rayon), (
        f"seq vs rayon pair-set mismatch:\n  seq={seq}\n  rayon={rayon}"
    )
    # Scores agree pair-for-pair too.
    for (sa, sb, ss), (ra, rb, rs) in zip(sorted(seq), sorted(rayon)):
        assert (sa, sb) == (ra, rb)
        assert ss == pytest.approx(rs, abs=1e-9)


def test_both_paths_match_slow_path(monkeypatch):
    """Both kernel paths must match find_fuzzy_matches (the source of truth).
    Valid here because the single-block fixture makes score_buckets score the
    whole frame, exactly what find_fuzzy_matches does."""
    slow = find_fuzzy_matches(
        _prepared(), _mk(), exclude_pairs=frozenset(), pre_scored_pairs=None
    )
    slow_keys = _keys(slow)
    assert slow_keys, "fixture must emit pairs or the parity check is vacuous"
    assert _keys(_run(monkeypatch, "0")) == slow_keys
    assert _keys(_run(monkeypatch, "100000000000")) == slow_keys
