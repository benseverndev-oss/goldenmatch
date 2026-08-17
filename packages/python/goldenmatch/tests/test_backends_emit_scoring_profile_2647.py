"""Every scoring backend must emit a ScoringProfile, or zero-config refuses (#2647).

`core/scorer.py` emits a `ScoringProfile` from its two scoring entry points.
`score_buckets`, `score_blocks_duckdb` and `score_blocks_ray` call
`_score_one_block` directly and bypass that wrapper, so a run routed to any of
them leaves the emitter holding the all-zero `ScoringProfile()` default.

`ScoringProfile.health()` reads that as *"No candidates compared and no pairs
scored -> RED (nothing happened)"*, the rollup goes RED, and at
`n_rows >= REFUSE_AT_N` `AutoConfigController` REFUSES the run.

Measured on person@100,000 (run 32040304803), which routes to the bucket
backend -- the DEFAULT scorer since #526, selected at exactly the sizes where
`REFUSE_AT_N` applies:

    blocking  GREEN   84,293 blocks, 121,372,923 comparisons, reduction 0.9757
    scoring   RED     every field zero
    cluster   GREEN   91,527 clusters from 100,000 rows, max size 3,
                      transitivity 1.0

Clustering cannot happen without scoring. 91,527 clusters from 100,000 rows is
~8,473 merges, so pairs were scored, cleared a threshold, and were clustered --
every stage downstream of scoring has real data while scoring itself reports
nothing. The user is told the config is degenerate; the evidence says it worked.

## What these tests assert, and what they deliberately do not

They assert a NON-DEFAULT profile reaches the emitter, and that its verdict is
not the "nothing happened" RED. They do NOT assert `candidates_compared`,
because the bucket path has no cheap total -- bucket sizes are never
accumulated, and `_pair_count` is used only for the oversized-tile budget.
Reporting `candidates_counted=False` there is the honest answer and is exactly
the state #2644 added the flag to express: the pair-derived signals
(`n_pairs_scored`, `mass_above_threshold`, `dip_statistic`, the histogram) are
real, and the count is absent rather than zero.

That is sufficient to clear the refusal, because the "nothing happened" clause
requires BOTH counters to be zero.
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
from goldenmatch.core.complexity_profile import HealthVerdict, ScoringProfile
from goldenmatch.core.matchkey import _xform_sig
from goldenmatch.core.profile_emitter import profile_capture


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


def test_bucket_backend_emits_a_scoring_profile():
    """The live bug. `score_buckets` is the DEFAULT scorer and emits nothing."""
    with profile_capture() as emitter:
        pairs = score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    assert pairs, "fixture must produce pairs, or this proves nothing"
    assert emitter.scoring is not None, "no ScoringProfile reached the emitter"
    assert emitter.scoring != ScoringProfile(), "the all-zero default is not a report"
    assert emitter.scoring.n_pairs_scored == len(pairs)


def test_bucket_backend_profile_does_not_read_as_nothing_happened():
    """The consequence, not just the mechanism: the emitted profile must not
    grade RED via the "nothing happened" clause, because that is what refuses
    the user's run at `n_rows >= REFUSE_AT_N`."""
    with profile_capture() as emitter:
        score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    sp = emitter.scoring
    assert not (sp.candidates_compared == 0 and sp.n_pairs_scored == 0), (
        "this is the exact predicate that refuses the run"
    )
    assert sp.mass_above_threshold > 0.0
    assert sp.health() != HealthVerdict.RED


def test_bucket_backend_is_honest_about_the_candidate_count():
    """`candidates_counted` must be False here rather than claiming a measured
    zero. The bucket path never accumulates bucket sizes, so the count is
    genuinely absent -- and #2644 exists so that absence is sayable."""
    with profile_capture() as emitter:
        score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    assert emitter.scoring.candidates_counted is False


def test_emitting_does_not_change_the_pairs():
    """The emitter is a no-op without an active capture, so scoring output must
    be identical either way. A telemetry fix that moved a pair would be a far
    worse bug than the one it fixes."""
    without = score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())
    with profile_capture():
        within = score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    key = lambda ps: sorted((min(a, b), max(a, b), round(s, 6)) for a, b, s in ps)  # noqa: E731
    assert key(without) == key(within)


def test_duckdb_backend_emits_a_scoring_profile():
    """Same gap, different backend. Skipped rather than xfailed when duckdb is
    absent: a missing optional dependency is not evidence about the code."""
    duckdb = pytest.importorskip("duckdb")  # noqa: F841
    from goldenmatch.backends.score_duckdb import score_blocks_duckdb
    from goldenmatch.core.blocker import build_blocks

    blocks = build_blocks(_prepared(), _blocking())
    with profile_capture() as emitter:
        pairs = score_blocks_duckdb(blocks, _mk(), matched_pairs=set())

    assert pairs, "fixture must produce pairs, or this proves nothing"
    assert emitter.scoring is not None, "no ScoringProfile reached the emitter"
    assert emitter.scoring.n_pairs_scored == len(pairs)
