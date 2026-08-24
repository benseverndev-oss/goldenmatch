"""A rule can ask whether its own last action moved what it predicted.

`rule_low_transitivity` re-applied a threshold nudge on every iteration while the
metric it was reacting to fell, walking 0.70 -> 0.50 and committing v0 anyway
(#2717; the same rule caused the 2M degeneration in #195 before that). It
received `history` and never read it -- the word appeared once, in the signature.

The fix was hand-rolled for one rule. This generalises it: a rule declares what
it expects to move via `PolicyDecision.predicts`, and any rule can ask
`rule_effect_was_negative`.
"""

from __future__ import annotations

from goldenmatch.core.autoconfig_history import (
    HistoryEntry,
    PolicyDecision,
    RunHistory,
    rule_effect_was_negative,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)


def _entry(iteration: int, transitivity: float,
           decision: PolicyDecision | None) -> HistoryEntry:
    return HistoryEntry(
        iteration=iteration, config=None,
        profile=ComplexityProfile(
            data=DataProfile(n_rows=1000, n_cols=3),
            blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
            scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                                   candidates_counted=True, mass_above_threshold=1.0,
                                   dip_statistic=0.5),
            cluster=ClusterProfile(n_clusters=50, transitivity_rate=transitivity),
        ),
        decision=decision, error=None, wall_clock_ms=1,
    )


def _decision(direction: str = "up") -> PolicyDecision:
    return PolicyDecision(
        rule_name="low_transitivity", rationale="x", config_diff={},
        predicts="cluster.transitivity_rate", predicts_direction=direction,
    )


def test_reports_negative_when_the_metric_moved_the_wrong_way():
    """The Abt-Buy shape: transitivity fell 0.200 -> 0.138 across four nudges."""
    history = RunHistory()
    history.entries.append(_entry(0, 0.20, _decision()))
    history.entries.append(_entry(1, 0.14, None))
    assert rule_effect_was_negative(history, "low_transitivity") is True


def test_reports_not_negative_when_it_worked():
    history = RunHistory()
    history.entries.append(_entry(0, 0.20, _decision()))
    history.entries.append(_entry(1, 0.55, None))
    assert rule_effect_was_negative(history, "low_transitivity") is False


def test_a_move_inside_the_margin_counts_as_no_progress():
    """`transitivity_rate` samples up to 1000 triples and drifts ~0.003-0.005 on
    an unchanged config, so a move inside that band is noise, not evidence."""
    history = RunHistory()
    history.entries.append(_entry(0, 0.200, _decision()))
    history.entries.append(_entry(1, 0.204, None))
    assert rule_effect_was_negative(history, "low_transitivity", margin=0.01) is True


def test_a_down_prediction_is_read_the_other_way():
    """Not every rule wants its metric to rise."""
    history = RunHistory()
    history.entries.append(_entry(0, 0.20, _decision(direction="down")))
    history.entries.append(_entry(1, 0.10, None))
    assert rule_effect_was_negative(history, "low_transitivity") is False


def test_no_prior_firing_is_not_negative():
    assert rule_effect_was_negative(RunHistory(), "low_transitivity") is False


def test_another_rules_decision_is_not_evidence_about_this_one():
    """Only a rule's OWN action says whether ITS lever works."""
    history = RunHistory()
    other = PolicyDecision(rule_name="blocking_too_coarse", rationale="x",
                           config_diff={}, predicts="cluster.transitivity_rate")
    history.entries.append(_entry(0, 0.20, other))
    history.entries.append(_entry(1, 0.14, None))
    assert rule_effect_was_negative(history, "low_transitivity") is False


def test_a_rule_that_predicted_nothing_is_not_negative():
    """Absence of evidence is not evidence of failure -- a rule that never said
    what it expected must not mute itself."""
    history = RunHistory()
    silent = PolicyDecision(rule_name="low_transitivity", rationale="x", config_diff={})
    history.entries.append(_entry(0, 0.20, silent))
    history.entries.append(_entry(1, 0.14, None))
    assert rule_effect_was_negative(history, "low_transitivity") is False


def test_an_unreadable_metric_is_not_negative():
    history = RunHistory()
    bogus = PolicyDecision(rule_name="low_transitivity", rationale="x",
                           config_diff={}, predicts="cluster.no_such_field")
    history.entries.append(_entry(0, 0.20, bogus))
    history.entries.append(_entry(1, 0.14, None))
    assert rule_effect_was_negative(history, "low_transitivity") is False


def test_the_shipped_rule_declares_what_it_predicts():
    """The migration target: rule_low_transitivity's own decisions must carry a
    prediction, or the generic helper silently never fires for it."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        ClusterConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.autoconfig_rules import rule_low_transitivity

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk", type="weighted", threshold=0.8,
                                  fields=[MatchkeyField(field="name",
                                                        scorer="token_sort", weight=1.0)])],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["name"])]),
        cluster=ClusterConfig(split_weak_bridges=True),
    )
    out = rule_low_transitivity(_entry(0, 0.20, None).profile, cfg, RunHistory())
    assert out is not None
    assert out[1].predicts == "cluster.transitivity_rate"
    assert out[1].predicts_direction == "up"
