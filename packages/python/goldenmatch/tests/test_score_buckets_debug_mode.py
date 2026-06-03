"""GOLDENMATCH_BUCKET_DEBUG verbose timing mode must be side-effect-free on
output (it only adds per-bucket timing + a summary print).

Runs the same score_buckets call with the flag off and on and asserts the
emitted pair set is identical. Works on any path (native or pure-Python); the
flag only gates timing capture + a print.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.backends.score_buckets import score_buckets
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


def _prepared() -> pl.DataFrame:
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
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["blk"])])


def _keys(pairs):
    return sorted((min(a, b), max(a, b)) for a, b, _ in pairs)


def test_debug_flag_does_not_change_output(monkeypatch, capsys):
    monkeypatch.delenv("GOLDENMATCH_BUCKET_DEBUG", raising=False)
    off = score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEBUG", "1")
    on = score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    assert _keys(on) == _keys(off), "debug flag changed the emitted pair set"
    assert _keys(off), "fixture must emit pairs or the check is vacuous"
    # The per-bucket timing summary is appended only on the native arrow path,
    # so only assert the DEBUG line when that kernel is present.
    if _NATIVE_ARROW:
        assert "[score_buckets][DEBUG]" in capsys.readouterr().out


def test_debug_flag_off_is_quiet(monkeypatch, capsys):
    monkeypatch.setenv("GOLDENMATCH_BUCKET_DEBUG", "0")
    score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())
    assert "[score_buckets][DEBUG]" not in capsys.readouterr().out
