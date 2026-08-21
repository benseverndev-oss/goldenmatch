"""The probabilistic pipeline path must emit a ScoringProfile.

This is the seventh scoring entry point found in the person@100k
investigation, and the one that actually scores that shape.

The route field (#2649) is what identified it, and it did so by ELIMINATION
rather than by another guess. Run 32048768733:

    person@100k   route = (none -- nothing emitted)   0 pairs
    biblio@100k   route = scorer.parallel             1,493,182 pairs

biblio goes through `core/scorer.py`, which always emitted. person emitted from
none of the six instrumented paths -- and because the four `fs_out_of_core`
orchestrators stamp their route at FUNCTION ENTRY, an empty route proves none of
them were even called. `find_fuzzy_matches` was a dead end too: that is the
WEIGHTED scorer, and person is probabilistic.

What actually scores person is `_score_probabilistic_matchkey` in
`core/pipeline.py`, which had zero emit calls. Without it the emitter keeps the
all-zero default, `health()` reads "nothing happened" -> RED, and at
`n_rows >= REFUSE_AT_N` the controller REFUSES a run whose clustering produced
91,527 clusters at transitivity 1.0.

## Why this emit is more precise than the bucket one

`score_buckets` had no candidate total available, so #2648 reports
`candidates_counted=False`. Here the function's own tail already holds both
quantities it needs -- `pairs` (above the cut) and `link_threshold` (the cut) --
so the profile is built from the real numbers with no fallback.
"""
from __future__ import annotations

import polars as pl
from goldenmatch._api import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.profile_emitter import profile_capture


def _df() -> pl.DataFrame:
    # 3 true-match pairs (0-1, 2-3, 4-5) sharing zip blocks.
    return pl.DataFrame({
        "first_name": ["John", "Jon", "Jane", "Jane", "Bob", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Doe", "Lee", "Lee"],
        "zip": ["111", "111", "222", "222", "333", "333"],
    })


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fs", type="probabilistic",
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler",
                                  levels=3, partial_threshold=0.8),
                    MatchkeyField(field="last_name", scorer="jaro_winkler",
                                  levels=2, partial_threshold=0.85),
                    MatchkeyField(field="zip", scorer="exact", levels=2),
                ],
            ),
        ],
    )


def test_probabilistic_dedupe_emits_a_scoring_profile():
    """The live bug: person's actual scoring path reported nothing."""
    with profile_capture() as emitter:
        dedupe_df(_df(), config=_config())

    assert emitter.scoring is not None, "no ScoringProfile reached the emitter"
    assert emitter.scoring.n_pairs_scored > 0, (
        "the fixture has 3 true pairs; a zero here means the emit did not "
        "see the scored pairs"
    )


def test_probabilistic_profile_names_its_route():
    """Route parity with every other emit site. Without this, the next
    investigation is back to inference."""
    with profile_capture() as emitter:
        dedupe_df(_df(), config=_config())

    assert emitter.scoring.route == "pipeline.probabilistic"


def test_probabilistic_profile_does_not_read_as_nothing_happened():
    """The consequence, not the mechanism. This predicate is what refuses a
    user's run at `n_rows >= REFUSE_AT_N`."""
    with profile_capture() as emitter:
        dedupe_df(_df(), config=_config())

    sp = emitter.scoring
    assert not (sp.candidates_compared == 0 and sp.n_pairs_scored == 0)
    assert sp.mass_above_threshold > 0.0


def test_emitting_does_not_change_the_result():
    """A telemetry fix that moved a cluster would be far worse than the bug it
    fixes. The emitter is a no-op without a capture, so both runs must agree."""
    without = dedupe_df(_df(), config=_config())
    with profile_capture():
        within = dedupe_df(_df(), config=_config())

    # Compare MEMBERSHIP, order-insensitively.
    #
    # Not a shape: a change that moved a record between clusters would leave any
    # count identical, so a shape check would not catch the failure this test
    # exists for.
    #
    # Not raw equality either. CI caught `[4, 5] != [5, 4]` -- the same members
    # in a different order, which comes from parallel completion order and is
    # not part of the contract. It passed locally because a single-threaded run
    # happened to be stable, which is exactly how an order-sensitive assertion
    # becomes a flaky test that gets deleted rather than fixed.
    def _members(clusters):
        return {
            cid: {k: (sorted(v) if isinstance(v, list) else v)
                  for k, v in rec.items()}
            for cid, rec in clusters.items()
        }

    assert _members(without.clusters) == _members(within.clusters)
