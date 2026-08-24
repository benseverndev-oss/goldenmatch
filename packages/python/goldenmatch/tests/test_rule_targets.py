"""Rules declare which RED condition they answer.

The point is coverage, not documentation. `test_rule_action_coverage.py` asserts
every reachable RED condition is claimed by at least one rule, and that gate can
only key on a declaration. A rule that answers nothing named is the accident
this removes.
"""

from __future__ import annotations

from goldenmatch.core.autoconfig_rules import DEFAULT_RULES, targets
from goldenmatch.core.complexity_profile import RED_REASONS


def test_decorator_records_the_reasons_on_the_function():
    @targets("cluster_giant")
    def rule_stub(profile, current, history):
        return None

    assert rule_stub.targets == ("cluster_giant",)


def test_decorator_accepts_several_reasons():
    """One action can answer more than one condition -- a blocking key swap
    addresses both 'no blocks' and 'nothing above threshold'."""

    @targets("blocking_no_blocks", "scoring_no_candidates")
    def rule_stub(profile, current, history):
        return None

    assert rule_stub.targets == ("blocking_no_blocks", "scoring_no_candidates")


def test_the_decorator_returns_the_rule_unchanged():
    """It annotates; it must not wrap. A wrapper would break `is` identity
    against DEFAULT_RULES entries and the oscillation guard's rule lookup."""

    def rule_stub(profile, current, history):
        return "sentinel"

    assert targets("cluster_giant")(rule_stub) is rule_stub
    assert rule_stub(None, None, None) == "sentinel"


def test_every_default_rule_declares_its_targets():
    undeclared = [r.__name__ for r in DEFAULT_RULES if not getattr(r, "targets", ())]
    assert undeclared == [], f"rules with no declared target: {undeclared}"


def test_declared_targets_are_real_reasons():
    """A typo'd slug would silently satisfy nothing and leave a real hole."""
    for rule in DEFAULT_RULES:
        for reason in getattr(rule, "targets", ()):
            assert reason in RED_REASONS, (
                f"{rule.__name__} targets unknown reason {reason!r}; "
                f"known: {sorted(RED_REASONS)}"
            )


def test_the_policy_stamps_the_declaration_onto_the_decision():
    """The audit trail should record what an action was MEANT to fix. Rules do
    not repeat themselves -- the decorator is the single source."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.autoconfig_history import PolicyDecision, RunHistory
    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ClusterProfile,
        ComplexityProfile,
        DataProfile,
        ScoringProfile,
    )

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk", type="weighted", threshold=0.7,
                                  fields=[MatchkeyField(field="name",
                                                        scorer="token_sort", weight=1.0)])],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["name"])]),
    )

    @targets("cluster_low_transitivity")
    def rule_stub(profile, current, history):
        return current.model_copy(update={"llm_boost": True}), PolicyDecision(
            rule_name="stub", rationale="r", config_diff={"llm_boost": True},
        )

    profile = ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0,
                               dip_statistic=0.5),
        cluster=ClusterProfile(n_clusters=50, transitivity_rate=0.2),
    )
    # The policy attaches the decision to the LAST history entry, so one has to
    # exist or the assertion below passes vacuously.
    from goldenmatch.core.autoconfig_history import HistoryEntry

    history = RunHistory()
    history.entries.append(HistoryEntry(iteration=0, config=cfg, profile=profile,
                                        decision=None, error=None, wall_clock_ms=1))
    policy = HeuristicRefitPolicy(rules=[rule_stub])
    assert policy.propose(profile, cfg, history) is not None

    stamped = history.entries[-1].decision
    assert stamped is not None, "the policy did not attach the decision"
    assert stamped.targets == ("cluster_low_transitivity",)
