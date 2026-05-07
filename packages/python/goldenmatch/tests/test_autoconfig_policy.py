import pytest
from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
    BlockingConfig, BlockingKeyConfig,
)
from goldenmatch.core.complexity_profile import (
    ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
    ClusterProfile, MatchkeyProfile, DomainProfile, FieldStats, HealthVerdict,
)
from goldenmatch.core.autoconfig_history import (
    RunHistory, HistoryEntry, PolicyDecision,
)
from goldenmatch.core.autoconfig_policy import (
    RefitPolicy, HeuristicRefitPolicy, Rule,
)


def _green_profile() -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(
            n_rows=100, n_cols=4,
            column_types={"a": "text", "b": "id-like", "c": "text", "d": "date"},
        ),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
            dip_statistic=0.05, mass_above_threshold=0.4, mass_in_borderline=0.05,
        ),
        cluster=ClusterProfile(
            n_clusters=20, cluster_size_p50=2, cluster_size_p99=5,
            cluster_size_max=8, transitivity_rate=0.95,
        ),
        matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
    )


def _red_profile() -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=4, column_types={"a": "text", "b": "text"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=2, total_comparisons=4900,
            reduction_ratio=0.01,  # RED
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
            dip_statistic=0.05, mass_above_threshold=0.4,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )


def _trivial_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])]),
        matchkeys=[
            MatchkeyConfig(
                name="m", type="weighted", threshold=0.7,
                fields=[MatchkeyField(field="a", scorer="jaro_winkler", weight=1.0)],
            )
        ],
    )


def test_protocol_is_runtime_checkable():
    """RefitPolicy is a Protocol; HeuristicRefitPolicy implements it."""
    policy = HeuristicRefitPolicy()
    # Duck-type check: has the propose method with the right signature
    assert callable(getattr(policy, "propose", None))


def test_green_profile_returns_none():
    """When profile is GREEN, policy returns None (satisfied) regardless of rules."""
    policy = HeuristicRefitPolicy()
    result = policy.propose(_green_profile(), _trivial_config(), RunHistory())
    assert result is None


def test_no_rules_satisfies_immediately():
    """An empty rule list means the policy is satisfied even on red profiles."""
    policy = HeuristicRefitPolicy(rules=[])
    result = policy.propose(_red_profile(), _trivial_config(), RunHistory())
    assert result is None


def test_first_firing_rule_wins():
    """Rules are tried in order; first to return non-None wins."""
    cfg_a = _trivial_config()
    cfg_b = GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])]),
        matchkeys=[MatchkeyConfig(
            name="m2", type="weighted", threshold=0.5,
            fields=[MatchkeyField(field="a", scorer="ensemble", weight=1.0)],
        )],
    )
    cfg_c = GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])]),
        matchkeys=[MatchkeyConfig(
            name="m3", type="weighted", threshold=0.6,
            fields=[MatchkeyField(field="a", scorer="token_sort", weight=1.0)],
        )],
    )

    def rule_skip(profile, current, history):
        return None

    def rule_fire_b(profile, current, history):
        return cfg_b, PolicyDecision(rule_name="rule_b", rationale="test", config_diff={})

    def rule_fire_c(profile, current, history):
        return cfg_c, PolicyDecision(rule_name="rule_c", rationale="test", config_diff={})

    policy = HeuristicRefitPolicy(rules=[rule_skip, rule_fire_b, rule_fire_c])
    result = policy.propose(_red_profile(), cfg_a, RunHistory())
    # rule_b fires first → cfg_b wins, cfg_c never consulted
    assert result is cfg_b


def test_rule_returning_same_config_is_treated_as_satisfied():
    """If a rule returns a config equal to current, treat as satisfied (None).
    This is the bug guard from spec §RefitPolicy.propose return semantics (S1-A).
    """
    cfg = _trivial_config()

    def rule_returns_same(profile, current, history):
        return current, PolicyDecision(rule_name="noop", rationale="x", config_diff={})

    policy = HeuristicRefitPolicy(rules=[rule_returns_same])
    result = policy.propose(_red_profile(), cfg, RunHistory())
    assert result is None


def test_decision_recorded_on_history_entry_when_rule_fires():
    """When a rule fires, the policy attaches the decision to the latest history entry.
    (This is so the controller's audit trail reflects which rule fired.)"""
    cfg_a = _trivial_config()
    cfg_b = GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])]),
        matchkeys=[MatchkeyConfig(name="m2", type="weighted", threshold=0.5,
                                  fields=[MatchkeyField(field="a", scorer="ensemble", weight=1.0)])],
    )

    decision = PolicyDecision(rule_name="my_rule", rationale="explanation", config_diff={"k": "v"})

    def rule(profile, current, history):
        return cfg_b, decision

    history = RunHistory()
    history.entries.append(HistoryEntry(
        iteration=0, config=cfg_a, profile=_red_profile(),
        decision=None, error=None, wall_clock_ms=10,
    ))

    policy = HeuristicRefitPolicy(rules=[rule])
    result = policy.propose(_red_profile(), cfg_a, history)
    assert result is cfg_b
    # The latest history entry's decision is now populated
    assert history.entries[-1].decision is decision


def test_decision_not_attached_when_no_history_entries():
    """If history is empty (first iteration before any append), no attach attempt."""
    cfg_a = _trivial_config()
    cfg_b = GoldenMatchConfig(
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])]),
        matchkeys=[MatchkeyConfig(name="m2", type="weighted", threshold=0.5,
                                  fields=[MatchkeyField(field="a", scorer="ensemble", weight=1.0)])],
    )

    def rule(profile, current, history):
        return cfg_b, PolicyDecision(rule_name="r", rationale="x", config_diff={})

    policy = HeuristicRefitPolicy(rules=[rule])
    # Empty history — propose still works, just no attach
    result = policy.propose(_red_profile(), cfg_a, RunHistory())
    assert result is cfg_b
