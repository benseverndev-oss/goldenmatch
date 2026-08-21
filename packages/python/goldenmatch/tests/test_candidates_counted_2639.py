"""`candidates_compared == 0` must not be read as "zero candidates" (#2639).

`scorer.py` skips the candidate-count loop above 10,000 blocks, because
`Block.n_rows()` materialises and doing that serially for tens of thousands of
blocks costs real time. The skip is deliberate and logged. What it leaves behind
is a `candidates_compared` of 0 that means NOT COUNTED, indistinguishable from a
measured zero -- and two consumers read that field as a decision signal.

Measured on run 31995984041, both shapes at 100,000 rows:

    person@100k   84,293 blocks   n_pairs_scored 0          candidates_compared 0
    biblio@100k   22,151 blocks   n_pairs_scored 1,493,182  candidates_compared 0

biblio scored 1.49M pairs with 99.9998% of the mass above threshold and still
reports `candidates_compared == 0`. Any dataset with fine-grained blocking is
past the gate, so at production scale this field is structurally zero.

The two consumers break in OPPOSITE directions, which is why one fix cannot be
"treat 0 as zero" or "treat 0 as unknown" globally:

  * `rule_blocking_singleton_trap` guards with `if candidates_compared > 0:
    return None`. Above the gate that guard never fires, so a dataset that
    scored 1.49M pairs stays eligible for singleton-trap remediation -- whose
    action COARSENS blocking to `first_token`.
  * `_maybe_decorate_with_llm_scorer` early-returns on `candidates_compared ==
    0`, so the escalation is silently dead at exactly the scale it exists for.

`ScoringProfile.health()` also reads the field, at
`candidates_compared == 0 and n_pairs_scored == 0`. That one is left alone
deliberately: it can only fire when both are zero, and `mass_above_threshold` is
necessarily 0.0 in that case, so the very next clause returns RED anyway. The
verdict does not change, and rewriting a rule whose behaviour is unaffected
would be churn on a path that decides whether a user's run is refused.
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_blocking_singleton_trap
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            passes=[BlockingKeyConfig(fields=["name"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="mk", type="weighted", threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            ),
        ],
    )


def _profile(scoring: ScoringProfile) -> ComplexityProfile:
    """A profile whose blocking is fine-grained and healthy -- 84,293 blocks,
    the person@100k shape that provoked this."""
    return ComplexityProfile(
        data=DataProfile(n_rows=100_000, n_cols=3,
                         column_types={"name": "name", "zip": "text", "dob": "date"}),
        blocking=BlockingProfile(
            keys_used=[["name"]], n_blocks=84_293,
            total_comparisons=121_372_923, reduction_ratio=0.975725,
            block_sizes_p50=2, block_sizes_p95=10, block_sizes_p99=72,
            block_sizes_max=2_170, singleton_block_count=0,
        ),
        scoring=scoring,
    )


def test_singleton_trap_abstains_when_the_count_was_not_taken():
    """The biblio case: 1.49M pairs scored, count skipped, and the rule must
    NOT offer to coarsen blocking.

    This is the live misfire. Today the `candidates_compared > 0` guard cannot
    fire, so the rule proceeds on a shape that scored a million and a half
    pairs.
    """
    scoring = ScoringProfile(
        n_pairs_scored=1_493_182,
        candidates_compared=0,      # not counted -- 22,151 blocks, past the gate
        candidates_counted=False,
        dip_statistic=0.013133,
        mass_above_threshold=0.999998,
        mass_in_borderline=0.175348,
    )
    out = rule_blocking_singleton_trap(_profile(scoring), _config(), RunHistory())
    assert out is None


def test_singleton_trap_still_fires_on_a_measured_zero():
    """The guard against over-correcting: when the count WAS taken and really is
    zero, the rule must still fire. A fix that simply disabled the rule would
    pass the test above and be useless."""
    scoring = ScoringProfile(
        n_pairs_scored=0,
        candidates_compared=0,
        candidates_counted=True,    # measured, genuinely zero
        mass_above_threshold=0.0,
    )
    out = rule_blocking_singleton_trap(_profile(scoring), _config(), RunHistory())
    assert out is not None, "a measured zero with blocks present IS the trap"


def test_singleton_trap_does_not_fire_when_candidates_were_measured():
    """Unchanged behaviour: a measured non-zero count is not the trap."""
    scoring = ScoringProfile(
        n_pairs_scored=500, candidates_compared=10_000,
        candidates_counted=True, mass_above_threshold=0.4,
    )
    out = rule_blocking_singleton_trap(_profile(scoring), _config(), RunHistory())
    assert out is None


def test_default_profile_is_not_counted():
    """A default-constructed ScoringProfile has no measurement behind it, so it
    must not claim one. The all-zero fallback is what the emitter produces when
    scoring never ran at all."""
    assert ScoringProfile().candidates_counted is False


def test_health_verdict_is_unchanged_by_the_flag():
    """`health()` is deliberately untouched. Pinned so a later change to it is a
    decision rather than a side effect: with both counters zero the verdict is
    RED either way, because mass_above_threshold is 0.0 and the next clause
    catches it."""
    from goldenmatch.core.complexity_profile import HealthVerdict

    for counted in (True, False):
        sp = ScoringProfile(n_pairs_scored=0, candidates_compared=0,
                            candidates_counted=counted, mass_above_threshold=0.0)
        assert sp.health() == HealthVerdict.RED
