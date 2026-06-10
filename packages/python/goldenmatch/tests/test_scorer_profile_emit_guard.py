"""Parity harness for the ``_emit_scoring_profile`` active-emitter guard.

``_emit_scoring_profile`` builds a ``ScoringProfile`` (a score histogram, the
Hartigan dip statistic, and two mass_* passes) and hands it to
``current_emitter().set_scoring``. The emit is a no-op under the null emitter,
but the *construction* is not: histogram_20 + hartigan_dip + mass_* iterate over
EVERY scored pair (~131M at 1M rows; the dip is a numpy/diptest sort). On the
full production pass there is NO ``profile_capture`` (every capture lives in the
auto-config controller's sample iterations), so that ~149s of work was computed
and immediately discarded -- the same dead-work pattern as #837's matched_pairs.

The guard (``if not has_active_emitter(): return``) skips construction when
nothing will consume it. These tests prove:
  (a) the returned scored-pair stream is byte-identical with/without a capture
      (the guard changes only the discarded side effect, never the output),
  (b) under an active capture the ScoringProfile is STILL emitted (byte-identical
      to pre-guard behavior -- the sample-iteration path the controller needs),
  (c) the expensive construction is genuinely SKIPPED when no capture is open
      (load-bearing: the dip helper is never called on the null-emitter path).

Run locally (targeted file only -- never the full suite on Windows):
``POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
  PYTHONPATH=<worktree>/packages/python/goldenmatch \
  .venv/Scripts/python.exe -m pytest <this file> -v``
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import scorer as scorer_mod
from goldenmatch.core.blocker import BlockResult
from goldenmatch.core.profile_emitter import profile_capture
from goldenmatch.core.scorer import score_blocks_parallel


def _block(records: list[tuple[int, str]], block_key: str = "k") -> BlockResult:
    df = pl.DataFrame({
        "__row_id__": [r[0] for r in records],
        "__source__": ["fixture"] * len(records),
        "name": [r[1] for r in records],
    })
    return BlockResult(block_key=block_key, df=df.lazy())


def _name_mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="test", type="weighted", threshold=0.85,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def _seq_blocks() -> list[BlockResult]:
    # <=2 blocks -> sequential branch (emit at scorer.py:1165).
    return [_block([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])]


def _par_blocks() -> list[BlockResult]:
    # >2 blocks -> ThreadPoolExecutor branch (emit at scorer.py:1250).
    return [
        _block([(1, "John Smith"), (2, "Jon Smith")], "b1"),
        _block([(10, "Alice Anderson"), (11, "Alice Andersen")], "b2"),
        _block([(20, "Bob Brown"), (21, "Bob Browne")], "b3"),
    ]


def test_scored_pairs_identical_with_and_without_capture_sequential():
    """Guard changes only the discarded side effect: the returned pair stream
    is byte-identical whether or not a capture is open (sequential branch)."""
    blocks = _seq_blocks()
    no_capture = score_blocks_parallel(blocks, _name_mk(), set())
    with profile_capture():
        with_capture = score_blocks_parallel(blocks, _name_mk(), set())
    assert sorted(no_capture) == sorted(with_capture)
    assert len(no_capture) > 0, "fixture must actually produce scored pairs"


def test_scored_pairs_identical_with_and_without_capture_parallel():
    """Same parity on the ThreadPoolExecutor branch (>2 blocks)."""
    blocks = _par_blocks()
    no_capture = score_blocks_parallel(blocks, _name_mk(), set())
    with profile_capture():
        with_capture = score_blocks_parallel(blocks, _name_mk(), set())
    assert sorted(no_capture) == sorted(with_capture)
    assert len(no_capture) > 0


def test_profile_still_emitted_under_active_capture():
    """The sample-iteration path the controller depends on is preserved: under
    an active capture the ScoringProfile is emitted with real stats."""
    for blocks in (_seq_blocks(), _par_blocks()):
        with profile_capture() as emitter:
            score_blocks_parallel(blocks, _name_mk(), set())
        assert emitter.scoring is not None, "capture must receive a ScoringProfile"
        assert emitter.scoring.n_pairs_scored > 0
        assert sum(emitter.scoring.score_histogram) == emitter.scoring.n_pairs_scored


def test_no_capture_skips_expensive_construction(monkeypatch):
    """Load-bearing: with no capture open the expensive profile construction is
    SKIPPED entirely -- the dip helper is never invoked on the production path.
    This is the whole point of the guard (the discarded ~149s at 1M)."""
    calls: list[int] = []
    real_dip = scorer_mod.hartigan_dip

    def _spy(scores):
        calls.append(len(scores))
        return real_dip(scores)

    monkeypatch.setattr(scorer_mod, "hartigan_dip", _spy)

    # No capture -> guard returns early, dip never runs.
    score_blocks_parallel(_seq_blocks(), _name_mk(), set())
    score_blocks_parallel(_par_blocks(), _name_mk(), set())
    assert calls == [], "null-emitter path must not build the ScoringProfile"

    # Capture open -> dip runs (the work is real when something consumes it).
    with profile_capture():
        score_blocks_parallel(_seq_blocks(), _name_mk(), set())
    assert calls, "active capture must still compute the dip statistic"
