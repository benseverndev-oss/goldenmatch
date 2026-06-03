"""Issue #688 regression: the native block scorer must stay correct AND fast
when the loaded goldenmatch-native wheel predates ``build_exclude_set`` (#552).

The published ``goldenmatch-native 0.1.0`` wheel (2026-05-27) shipped one day
before ``build_exclude_set`` / ``ExcludeSet`` (#552, 2026-05-28). Against that
wheel the caller's ``native_module().build_exclude_set`` raises AttributeError
and the worker takes the legacy exclude branch. The old legacy branch passed the
full exclude as a fresh Vec on every bucket call, making the kernel rebuild a
HashSet per call -- O(buckets * |exclude|), the 44x slowdown reported in #688.

The fix: on that branch, pass an EMPTY exclude to the kernel and drop excluded
pairs in Python after emit. This test pins the correctness invariant of that
fallback (excluded pairs never leak; output equals the Arc-handle path) by
simulating a wheel without ``build_exclude_set``.

Requires the native kernel (score_block_pairs_arrow); skipped on pure-Python.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.backends import score_buckets as sb
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core._native_loader import native_available, native_module
from goldenmatch.core.matchkey import _xform_sig

_NATIVE_ARROW = native_available() and hasattr(
    native_module(), "score_block_pairs_arrow"
)
pytestmark = pytest.mark.skipif(
    not _NATIVE_ARROW,
    reason="needs the native score_block_pairs_arrow kernel",
)


class _NoExcludeSetModule:
    """Proxy over the real native module that hides ``build_exclude_set``,
    simulating the pre-#552 published wheel."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        if name in ("build_exclude_set", "ExcludeSet"):
            raise AttributeError(name)
        return getattr(self._real, name)


def _prepared() -> pl.DataFrame:
    # One block of four identical names -> all 6 intra-block pairs score 1.0 on
    # jaro_winkler, so every pair is above threshold and emitted.
    mk_field = MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)
    name_col = _xform_sig(mk_field)
    names = ["alice", "alice", "alice", "alice"]
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": names,
        name_col: names,
        "__block_key__": ["b"] * 4,
    })


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="t", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def _blocking() -> BlockingConfig:
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["name"])])


def _keys(pairs):
    return sorted((min(a, b), max(a, b)) for a, b, _ in pairs)


def _run(monkeypatch, *, stale: bool, matched):
    if stale:
        real = native_module()
        monkeypatch.setattr(sb, "native_module", lambda: _NoExcludeSetModule(real))
    return sb.score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set(matched))


def test_no_exclude_all_pairs_emitted(monkeypatch):
    """Sanity: with no exclude, both paths emit all 6 pairs of the 4-row block."""
    handle = _run(monkeypatch, stale=False, matched=set())
    assert len(handle) == 6
    stale = _run(monkeypatch, stale=True, matched=set())
    assert _keys(stale) == _keys(handle)


def test_stale_wheel_still_excludes_matched_pair(monkeypatch):
    """The legacy (stale-wheel) branch must still drop an excluded pair -- the
    empty-exclude + Python post-filter fallback, not a silent regression."""
    excluded = {(0, 1)}
    stale = _run(monkeypatch, stale=True, matched=excluded)
    assert (0, 1) not in _keys(stale), "excluded pair leaked on the stale-wheel path"
    assert len(stale) == 5


def test_stale_and_handle_paths_agree(monkeypatch):
    """Output pair set is identical between the Arc-handle path (current wheel)
    and the empty-exclude fallback (stale wheel) for the same exclude."""
    excluded = {(0, 1), (2, 3)}
    handle = _run(monkeypatch, stale=False, matched=excluded)
    stale = _run(monkeypatch, stale=True, matched=excluded)
    assert _keys(stale) == _keys(handle)
    assert (0, 1) not in _keys(stale) and (2, 3) not in _keys(stale)
