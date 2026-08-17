"""A ScoringProfile must name the code path that produced it.

There are at least six scoring entry points -- `core/scorer.py`'s two,
`score_buckets` (list and arrow), `score_duckdb`, `ray_backend`, and the four
orchestrators in `backends/fs_out_of_core.py`. "Which one scored this run?" has
been answered by INFERENCE three times in the person@100k investigation and was
wrong twice:

  * #2647 assumed the bucket backend, fixed it (#2648), and person did not move;
  * the FS orchestrators turned out to be a fifth path with no emission at all.

Each wrong guess cost a full CI round. `route` makes the profile self-describing,
so an empty scoring profile on a run whose clustering plainly worked localises
the gap from the artifact instead of from a hypothesis.
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
from goldenmatch.core.complexity_profile import ScoringProfile
from goldenmatch.core.matchkey import _xform_sig
from goldenmatch.core.profile_emitter import profile_capture
from goldenmatch.core.scorer import note_scoring_route


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


def test_default_profile_names_no_producer():
    """Empty is the honest default: the all-zero profile nobody wrote to."""
    assert ScoringProfile().route == ""


def test_bucket_backend_names_itself():
    with profile_capture() as emitter:
        score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())

    assert emitter.scoring.route.startswith("buckets")


def test_orchestrator_marker_stores_a_route_when_nothing_scored_yet():
    """The FS case: the orchestrator chose a branch and delegated, and no
    profile exists yet. A route-only profile beats an anonymous empty one."""
    with profile_capture() as emitter:
        note_scoring_route("fs.sequential")

    assert emitter.scoring is not None
    assert emitter.scoring.route == "fs.sequential"
    assert emitter.scoring.n_pairs_scored == 0


def test_orchestrator_marker_preserves_a_measured_profile():
    """The nesting case, and the one that must not regress: when scoring HAS
    reported, the outer marker records how it was reached WITHOUT overwriting a
    single measured field. A marker that clobbered real numbers would recreate
    the bug it exists to diagnose."""
    with profile_capture() as emitter:
        score_buckets(_prepared(), _blocking(), _mk(), matched_pairs=set())
        measured = emitter.scoring
        assert measured.n_pairs_scored > 0
        note_scoring_route("fs.sequential")

    after = emitter.scoring
    assert after.route == f"fs.sequential>{measured.route}"
    assert after.n_pairs_scored == measured.n_pairs_scored
    assert after.mass_above_threshold == measured.mass_above_threshold
    assert after.candidates_counted == measured.candidates_counted


def test_marker_is_a_noop_without_a_capture():
    """Production opens no capture, so this must cost nothing and raise
    nothing when called outside one."""
    note_scoring_route("fs.sequential")  # must not raise
