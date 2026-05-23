import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import (
    HistoryEntry,
    PolicyDecision,
    RunHistory,
)
from goldenmatch.core.autoconfig_policy import (
    HeuristicRefitPolicy,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    FieldStats,
    MatchkeyProfile,
    ScoringProfile,
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


# ============================================================
# Task 3.2 — five rules
# ============================================================

from goldenmatch.core.autoconfig_rules import (
    DEFAULT_RULES,
    rule_blocking_too_coarse,
    rule_low_reduction_ratio,
    rule_low_transitivity,
    rule_no_matches,
    rule_unimodal_scoring,
)


def _config_with_blocking(field: str = "a", threshold: float = 0.7,
                          scorer: str = "jaro_winkler") -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=threshold,
            fields=[
                MatchkeyField(field="name", scorer=scorer, weight=1.0,
                              transforms=["lowercase"]),
                MatchkeyField(field="city", scorer=scorer, weight=1.0,
                              transforms=["lowercase"]),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=[field], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )


def _profile_blocking_too_coarse() -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(
            n_rows=1000, n_cols=3,
            column_types={"a": "text", "name": "name", "city": "geo"},
            cardinality_ratio={"a": 0.02, "name": 0.4, "city": 0.3},
        ),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=2, total_comparisons=99000,
            reduction_ratio=0.8, block_sizes_p50=100, block_sizes_p95=400,
            block_sizes_p99=950,  # 950 > 10 * (1000/2 = 500) → 5000? no, 10*500 = 5000 NOT >
            block_sizes_max=950,
        ),
        scoring=ScoringProfile(n_pairs_scored=99000, mass_above_threshold=0.1, dip_statistic=0.05),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )


def _profile_blocking_RED() -> ComplexityProfile:
    """p99 (5500) > 10 * 1000/2 (5000) → fires."""
    return ComplexityProfile(
        data=DataProfile(
            n_rows=1000, n_cols=3,
            column_types={"a": "text", "name": "name", "city": "geo"},
            cardinality_ratio={"a": 0.02, "name": 0.4, "city": 0.3},
        ),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=2, total_comparisons=99000,
            reduction_ratio=0.8, block_sizes_p50=100, block_sizes_p95=400,
            block_sizes_p99=5500, block_sizes_max=5500,
        ),
        scoring=ScoringProfile(n_pairs_scored=99000, mass_above_threshold=0.1, dip_statistic=0.05),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )


# Rule 1
def test_rule_blocking_too_coarse_fires_when_p99_dominates():
    cfg = _config_with_blocking(field="a")
    out = rule_blocking_too_coarse(_profile_blocking_RED(), cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "blocking_too_coarse"
    new_field = new_cfg.blocking.keys[0].fields[0]
    assert new_field != "a"
    assert new_field in {"name", "city"}


def test_rule_blocking_too_coarse_does_not_fire_when_p99_modest():
    cfg = _config_with_blocking(field="a")
    out = rule_blocking_too_coarse(_profile_blocking_too_coarse(), cfg, RunHistory())
    # 950 < 5000 threshold → no fire
    assert out is None


def test_rule_blocking_too_coarse_returns_none_when_no_alternate_col():
    cfg = _config_with_blocking(field="name")
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=1000, n_cols=2,
            column_types={"name": "name", "rare": "id-like"},
            cardinality_ratio={"name": 0.4, "rare": 0.99},  # nothing in [0.05, 0.5] except 'name' itself
        ),
        blocking=BlockingProfile(
            keys_used=[["name"]], n_blocks=2, total_comparisons=99000, reduction_ratio=0.8,
            block_sizes_p99=5500,
        ),
        scoring=ScoringProfile(n_pairs_scored=10, mass_above_threshold=0.1, dip_statistic=0.05),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_blocking_too_coarse(profile, cfg, RunHistory())
    assert out is None


# Rule 2
def test_rule_unimodal_scoring_fires_and_swaps_scorer():
    cfg = _config_with_blocking(scorer="jaro_winkler")
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        scoring=ScoringProfile(
            n_pairs_scored=500, dip_statistic=0.001,
            mass_above_threshold=0.4, mass_in_borderline=0.3,
        ),
        matchkey=MatchkeyProfile(per_field={
            "name": FieldStats(0.4, 0.0, 10),
            "city": FieldStats(0.1, 0.0, 5),
        }),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10, block_sizes_p99=20),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_unimodal_scoring(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    # Highest cardinality field is "name"
    name_field = next(f for f in new_cfg.matchkeys[0].fields if f.field == "name")
    assert name_field.scorer == "ensemble"


def test_rule_unimodal_scoring_does_not_fire_when_already_ensemble():
    cfg = _config_with_blocking(scorer="ensemble")
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        scoring=ScoringProfile(
            n_pairs_scored=500, dip_statistic=0.001, mass_above_threshold=0.4,
        ),
        matchkey=MatchkeyProfile(per_field={"name": FieldStats(0.4, 0.0, 10)}),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10, block_sizes_p99=20),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_unimodal_scoring(profile, cfg, RunHistory())
    assert out is None


# Rule 3
def test_rule_low_reduction_fires_and_adds_multi_pass():
    cfg = _config_with_blocking()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2,
                         column_types={"name": "text", "city": "geo"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=4000,
            reduction_ratio=0.1, block_sizes_p99=15,
        ),
        scoring=ScoringProfile(n_pairs_scored=4000, mass_above_threshold=0.4, dip_statistic=0.05),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_low_reduction_ratio(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, _ = out
    assert new_cfg.blocking.strategy == "multi_pass"
    assert len(new_cfg.blocking.passes) >= 2
    soundex_pass = [p for p in new_cfg.blocking.passes if "soundex" in p.transforms]
    assert soundex_pass


# Rule 4
def test_rule_low_transitivity_lowers_threshold():
    cfg = _config_with_blocking(threshold=0.8)
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10, block_sizes_p99=20),
        scoring=ScoringProfile(n_pairs_scored=500, mass_above_threshold=0.4, dip_statistic=0.05),
        cluster=ClusterProfile(n_clusters=10, transitivity_rate=0.5),  # below 0.85
    )
    out = rule_low_transitivity(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, _ = out
    assert new_cfg.matchkeys[0].threshold == pytest.approx(0.75)


def test_rule_low_transitivity_floors_at_0_5():
    cfg = _config_with_blocking(threshold=0.5)
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10, block_sizes_p99=20),
        scoring=ScoringProfile(n_pairs_scored=500, mass_above_threshold=0.4, dip_statistic=0.05),
        cluster=ClusterProfile(n_clusters=10, transitivity_rate=0.5),
    )
    out = rule_low_transitivity(profile, cfg, RunHistory())
    assert out is None  # already at floor


# Rule 5
def test_rule_no_matches_resets_threshold_and_broadens_blocking():
    # v1.10: rule_no_matches now lowers threshold incrementally by 0.05 per iteration
    # (ctx=None path). Old one-shot reset-to-0.5 + broadened-blocking behavior replaced
    # by indicator-aware step-down. First call: 0.85 → 0.80.
    cfg = _config_with_blocking(threshold=0.85)
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10, block_sizes_p99=20),
        scoring=ScoringProfile(
            n_pairs_scored=500, candidates_compared=500,
            mass_above_threshold=0.0,  # nothing matched
            dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_no_matches(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, _ = out
    assert new_cfg.matchkeys[0].threshold == pytest.approx(0.80)


def test_rule_no_matches_does_not_fire_on_zero_candidates_compared():
    """When candidates_compared==0 (singleton trap), rule_no_matches should NOT
    fire — that's rule_blocking_singleton_trap's territory."""
    cfg = _config_with_blocking(threshold=0.85)
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.99, n_blocks=100,
                                 block_sizes_p99=1, singleton_block_count=100),
        scoring=ScoringProfile(
            n_pairs_scored=0, candidates_compared=0,  # blocking trapped everything
            mass_above_threshold=0.0,
            dip_statistic=0.0,
        ),
        cluster=ClusterProfile(transitivity_rate=1.0),
    )
    out = rule_no_matches(profile, cfg, RunHistory())
    # candidates_compared=0 → singleton trap → rule_no_matches defers
    assert out is None


def test_default_rules_list_has_five_entries():
    # Updated to 6 after rule_blocking_singleton_trap was added (2026-05-07)
    # Updated to 7 after rule_blocking_key_swap was added (2026-05-07)
    # Updated to 10 after rule_enable_llm_scorer was added (2026-05-07)
    # Reverted to 9: rule_enable_llm_scorer moved out of DEFAULT_RULES into
    # AutoConfigController._maybe_decorate_with_llm_scorer (post-iteration decoration)
    # Updated to 10: rule_uniform_heavy_blocking added (Fix 2) + rule_blocking_key_swap reordered (Fix 1)
    # Updated to 13: Phase 5 added rule_corruption_normalize, rule_cross_blocking_disagreement,
    # rule_sparse_match_expand (v1.10)
    assert len(DEFAULT_RULES) == 14  # v1.20.x #124: rule_demote_clustered_identity removed from rotation


def test_heuristic_policy_with_default_rules_fires_on_red_blocking():
    """End-to-end: HeuristicRefitPolicy with DEFAULT_RULES proposes a config
    when blocking is too coarse."""
    cfg = _config_with_blocking(field="a")
    policy = HeuristicRefitPolicy()  # uses DEFAULT_RULES
    profile = _profile_blocking_RED()
    out = policy.propose(profile, cfg, RunHistory())
    assert out is not None
    assert out is not cfg


# ============================================================
# Singleton-trap rule (added 2026-05-07)
# ============================================================

from goldenmatch.core.autoconfig_rules import rule_blocking_singleton_trap


def test_rule_singleton_trap_fires_on_mostly_singleton_blocks_with_no_pairs():
    """When blocking produces blocks but candidates_compared==0, the blocking
    is too discriminating. Rule should propose switching to ``first_token``."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[
                MatchkeyField(field="title", scorer="token_sort", weight=1.5,
                              transforms=["lowercase"]),
                MatchkeyField(field="authors", scorer="token_sort", weight=1.0,
                              transforms=["lowercase"]),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"],
                                     transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=2616, n_cols=4,
            column_types={"title": "text", "authors": "text",
                          "venue": "text", "year": "numeric"},
            cardinality_ratio={"title": 0.99, "authors": 0.95,
                                "venue": 0.005, "year": 0.005},
        ),
        blocking=BlockingProfile(
            keys_used=[["__title_key__"]], n_blocks=1201,
            total_comparisons=24, reduction_ratio=0.997,
            block_sizes_p50=2, block_sizes_p99=31, block_sizes_max=31,
            singleton_block_count=900,    # many singletons
            oversized_block_count=0,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=0, candidates_compared=0,  # no candidates compared
            mass_above_threshold=0.0,
            dip_statistic=0.0,
        ),
        cluster=ClusterProfile(transitivity_rate=1.0),
        matchkey=MatchkeyProfile(per_field={
            "title": FieldStats(0.99, 0.0, 50),
            "authors": FieldStats(0.95, 0.0, 30),
        }),
    )
    out = rule_blocking_singleton_trap(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "blocking_singleton_trap"
    # New blocking key uses the first matchkey field with first_token transform
    assert new_cfg.blocking.keys[0].fields == ["title"]
    assert "first_token" in new_cfg.blocking.keys[0].transforms


def test_rule_singleton_trap_does_not_fire_when_candidates_were_compared():
    """If candidates_compared > 0, blocking produced comparable pairs — not the trap."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="title", scorer="token_sort",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2,
                          column_types={"title": "text", "authors": "text"}),
        blocking=BlockingProfile(
            keys_used=[["__title_key__"]], n_blocks=10, total_comparisons=50,
            reduction_ratio=0.99,
            block_sizes_p50=2, block_sizes_p99=5, block_sizes_max=5,
            singleton_block_count=8,  # many singletons, but candidates_compared > 0
        ),
        scoring=ScoringProfile(n_pairs_scored=144, candidates_compared=144,
                                mass_above_threshold=0.4, dip_statistic=0.05),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_blocking_singleton_trap(profile, cfg, RunHistory())
    assert out is None


def test_rule_singleton_trap_fires_when_no_candidates_even_with_dense_blocks():
    """Even with few singletons, candidates_compared==0 still triggers the trap
    (e.g. cross-source non-overlap in match mode). The old singleton-fraction
    guard has been removed — candidates_compared is the canonical signal."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="title", scorer="token_sort",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2,
                          column_types={"title": "text", "authors": "text"}),
        blocking=BlockingProfile(
            keys_used=[["__title_key__"]], n_blocks=10, total_comparisons=0,
            reduction_ratio=1.0,
            block_sizes_p50=10, block_sizes_p99=15, block_sizes_max=15,
            singleton_block_count=2,  # only 20% singletons but candidates_compared=0
        ),
        scoring=ScoringProfile(n_pairs_scored=0, candidates_compared=0,
                                mass_above_threshold=0.0, dip_statistic=0.0),
        cluster=ClusterProfile(transitivity_rate=1.0),
    )
    out = rule_blocking_singleton_trap(profile, cfg, RunHistory())
    # candidates_compared=0 with n_blocks>0 → trap fires regardless of singleton fraction
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "blocking_singleton_trap"


def test_rule_singleton_trap_returns_none_when_no_text_field_in_matchkey():
    """Rule needs a text field in the first weighted matchkey to target."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="numeric_id", scorer="exact",
                                   weight=1.0, transforms=[])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__some_key__"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2,
                          column_types={"numeric_id": "id-like",
                                         "other": "id-like"}),
        blocking=BlockingProfile(
            keys_used=[["__some_key__"]], n_blocks=20, total_comparisons=0,
            reduction_ratio=1.0,
            block_sizes_p50=2, block_sizes_p99=3, block_sizes_max=3,
            singleton_block_count=18,
        ),
        scoring=ScoringProfile(n_pairs_scored=0, candidates_compared=0,
                                mass_above_threshold=0.0, dip_statistic=0.0),
        cluster=ClusterProfile(transitivity_rate=1.0),
    )
    out = rule_blocking_singleton_trap(profile, cfg, RunHistory())
    assert out is None  # no text field → can't target first_token


def test_default_rules_now_has_six_entries():
    """Singleton-trap rule is added to DEFAULT_RULES.
    Updated to 7 after rule_blocking_key_swap was added (2026-05-07).
    Updated to 10 after rule_enable_llm_scorer was added (2026-05-07).
    Reverted to 9: rule_enable_llm_scorer moved to post-iteration decoration.
    Updated to 10: rule_uniform_heavy_blocking added (Fix 2) + rule_blocking_key_swap reordered (Fix 1).
    Updated to 13: Phase 5 added 3 new rules (v1.10)."""
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
    assert len(DEFAULT_RULES) == 14  # v1.20.x #124: rule_demote_clustered_identity removed from rotation


def test_singleton_trap_runs_before_blocking_too_coarse():
    """Order matters: singleton-trap is more specific than blocking-too-coarse
    on the singleton pathology, so it must run first."""
    from goldenmatch.core.autoconfig_rules import (
        DEFAULT_RULES,
        rule_blocking_singleton_trap,
        rule_blocking_too_coarse,
    )
    idx_trap = DEFAULT_RULES.index(rule_blocking_singleton_trap)
    idx_coarse = DEFAULT_RULES.index(rule_blocking_too_coarse)
    assert idx_trap < idx_coarse


# ============================================================
# rule_blocking_key_swap (added 2026-05-07)
# ============================================================

from goldenmatch.core.autoconfig_rules import rule_blocking_key_swap


def _scoring_no_matches_with_candidates() -> ScoringProfile:
    """ScoringProfile representing 'pairs were compared, none matched'."""
    return ScoringProfile(
        n_pairs_scored=0,
        candidates_compared=33000,
        mass_above_threshold=0.0,
        dip_statistic=0.0,
    )


def _profile_for_swap_test() -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(
            n_rows=4910, n_cols=4,
            column_types={"title": "text", "authors": "text",
                          "venue": "text", "year": "numeric"},
            cardinality_ratio={"title": 0.99, "authors": 0.95,
                                "venue": 0.005, "year": 0.005},
        ),
        blocking=BlockingProfile(
            keys_used=[["__title_key__"]], n_blocks=1201,
            total_comparisons=33563, reduction_ratio=0.997,
            block_sizes_p50=4, block_sizes_p99=31, block_sizes_max=104,
            singleton_block_count=0,
        ),
        scoring=_scoring_no_matches_with_candidates(),
        cluster=ClusterProfile(transitivity_rate=1.0),
        matchkey=MatchkeyProfile(per_field={
            "title": FieldStats(0.99, 0.0, 50),
            "authors": FieldStats(0.95, 0.0, 30),
        }),
    )


def _config_for_swap_test() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.5,
            fields=[
                MatchkeyField(field="title", scorer="token_sort", weight=1.5,
                              transforms=["lowercase"]),
                MatchkeyField(field="authors", scorer="token_sort", weight=1.0,
                              transforms=["lowercase"]),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"],
                                     transforms=["lowercase"])],
            max_block_size=50000, skip_oversized=False,
        ),
    )


def _history_with_prior_decision() -> RunHistory:
    """RunHistory with one prior iteration that already fired a rule."""
    h = RunHistory()
    h.entries.append(HistoryEntry(
        iteration=0,
        config=_config_for_swap_test(),
        profile=_profile_for_swap_test(),
        decision=PolicyDecision(rule_name="no_matches", rationale="prior",
                                config_diff={}),
        error=None, wall_clock_ms=10,
    ))
    return h


def test_rule_key_swap_fires_after_prior_decision_with_no_matches():
    """The DBLP-ACM scenario: iter 0 lowered threshold, iter 1 still has
    candidates but no matches. Rule should propose first_token blocking
    on the dominant text field."""
    cfg = _config_for_swap_test()
    profile = _profile_for_swap_test()
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "blocking_key_swap"
    assert new_cfg.blocking.keys[0].fields == ["title"]
    assert "first_token" in new_cfg.blocking.keys[0].transforms


def test_rule_key_swap_does_not_fire_on_first_iteration():
    """No prior decisions → rule does not fire (other rules handle iter 0)."""
    cfg = _config_for_swap_test()
    profile = _profile_for_swap_test()
    history = RunHistory()  # empty — no prior decisions
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is None


def test_rule_key_swap_does_not_fire_when_candidates_zero():
    """No candidates compared → singleton-trap rule's territory, not this one."""
    cfg = _config_for_swap_test()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2,
                          column_types={"title": "text", "authors": "text"}),
        blocking=BlockingProfile(keys_used=[["x"]], n_blocks=10,
                                  reduction_ratio=0.95),
        scoring=ScoringProfile(
            n_pairs_scored=0,
            candidates_compared=0,    # NOT this rule's case
            mass_above_threshold=0.0,
        ),
        cluster=ClusterProfile(transitivity_rate=1.0),
        matchkey=MatchkeyProfile(per_field={"title": FieldStats(0.5, 0.0, 10)}),
    )
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is None


def test_rule_key_swap_does_not_fire_when_matches_exist():
    """mass_above_threshold > 0 → blocking is producing matches; not the trap."""
    cfg = _config_for_swap_test()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=4910, n_cols=2,
                          column_types={"title": "text", "authors": "text"}),
        blocking=BlockingProfile(keys_used=[["__title_key__"]], n_blocks=1201,
                                  reduction_ratio=0.997, block_sizes_p99=31),
        scoring=ScoringProfile(
            n_pairs_scored=144,
            candidates_compared=33000,
            mass_above_threshold=0.4,    # matches exist
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={"title": FieldStats(0.99, 0.0, 50)}),
    )
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is None


def test_rule_key_swap_does_not_fire_when_already_first_token():
    """Avoid oscillation: if blocking already uses first_token on the same field,
    don't propose the same change."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.5,
            fields=[MatchkeyField(field="title", scorer="token_sort",
                                   weight=1.5, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["title"],
                                     transforms=["lowercase", "first_token"])],
            max_block_size=50000, skip_oversized=False,
        ),
    )
    profile = _profile_for_swap_test()
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is None


def test_rule_key_swap_returns_none_when_no_text_field_in_matchkey():
    """Need a text field in the first weighted matchkey to swap onto."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.5,
            fields=[MatchkeyField(field="numeric_id", scorer="exact",
                                   weight=1.0, transforms=[])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__some_key__"],
                                     transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2,
                          column_types={"numeric_id": "id-like",
                                         "other": "id-like"}),
        blocking=BlockingProfile(keys_used=[["x"]], n_blocks=10,
                                  reduction_ratio=0.95),
        scoring=_scoring_no_matches_with_candidates(),
        cluster=ClusterProfile(transitivity_rate=1.0),
        matchkey=MatchkeyProfile(per_field={"numeric_id": FieldStats(0.99, 0.0, 5)}),
    )
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is None


def test_default_rules_now_has_seven_entries():
    """Adding rule_blocking_key_swap brings the count to 7.
    Updated to 10 after rule_enable_llm_scorer was added (2026-05-07).
    Reverted to 9: rule_enable_llm_scorer moved to post-iteration decoration.
    Updated to 10: rule_uniform_heavy_blocking added (Fix 2) + rule_blocking_key_swap reordered (Fix 1).
    Updated to 13: Phase 5 added 3 new rules (v1.10)."""
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
    assert len(DEFAULT_RULES) == 14  # v1.20.x #124: rule_demote_clustered_identity removed from rotation


def test_rule_key_swap_is_before_rule_no_matches():
    """Fix 1: rule_blocking_key_swap fires BEFORE rule_no_matches (and
    rule_low_transitivity). When blocking is fundamentally wrong (mass_above==0),
    the structural fix (swap key) must take priority over tuning rules (lower threshold).
    The old behavior was iter-1+ fallback AFTER no_matches; now it fires earlier as a
    structural check, with history.decisions guard ensuring iter-0 safety."""
    from goldenmatch.core.autoconfig_rules import (
        DEFAULT_RULES,
        rule_blocking_key_swap,
        rule_no_matches,
    )
    idx_no_matches = DEFAULT_RULES.index(rule_no_matches)
    idx_swap = DEFAULT_RULES.index(rule_blocking_key_swap)
    assert idx_swap < idx_no_matches, "key_swap must run before no_matches (structural before tuning)"


def test_rule_key_swap_drops_derived_column_exact_matchkey():
    """When swapping blocking off __title_key__, also drop the
    domain_exact_title_key (an exact matchkey on the same derived column),
    because its premise (the domain-extraction grouping) is invalidated."""
    cfg = GoldenMatchConfig(
        matchkeys=[
            # The fuzzy matchkey we want to keep
            MatchkeyConfig(
                name="m", type="weighted", threshold=0.5,
                fields=[
                    MatchkeyField(field="title", scorer="token_sort", weight=1.5,
                                  transforms=["lowercase"]),
                ],
            ),
            # The domain-extraction-emitted exact matchkey we want dropped
            MatchkeyConfig(
                name="domain_exact_title_key", type="exact",
                fields=[MatchkeyField(field="__title_key__",
                                       transforms=["lowercase"])],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"],
                                     transforms=["lowercase"])],
            max_block_size=50000, skip_oversized=False,
        ),
    )
    profile = _profile_for_swap_test()
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is not None
    new_cfg, decision = out
    # Only the fuzzy matchkey survives
    assert len(new_cfg.matchkeys) == 1
    assert new_cfg.matchkeys[0].name == "m"
    assert new_cfg.matchkeys[0].type == "weighted"


def test_rule_key_swap_keeps_user_facing_exact_matchkeys():
    """Don't drop exact matchkeys on regular columns — only the derived ones
    paired with domain extraction."""
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m", type="weighted", threshold=0.5,
                fields=[MatchkeyField(field="title", scorer="token_sort",
                                       weight=1.5, transforms=["lowercase"])],
            ),
            MatchkeyConfig(
                name="exact_email", type="exact",
                fields=[MatchkeyField(field="email",  # NOT derived
                                       transforms=["lowercase"])],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"],
                                     transforms=["lowercase"])],
            max_block_size=50000, skip_oversized=False,
        ),
    )
    profile = _profile_for_swap_test()
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is not None
    new_cfg, _ = out
    # Both matchkeys survive (the user-defined exact one is preserved)
    assert len(new_cfg.matchkeys) == 2
    names = {mk.name for mk in new_cfg.matchkeys}
    assert names == {"m", "exact_email"}


def test_rule_key_swap_keeps_exact_matchkey_with_mixed_derived_and_regular_fields():
    """An exact matchkey with at least one non-derived field is user-meaningful;
    don't drop it."""
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m", type="weighted", threshold=0.5,
                fields=[MatchkeyField(field="title", scorer="token_sort",
                                       weight=1.5, transforms=["lowercase"])],
            ),
            MatchkeyConfig(
                name="mixed_exact", type="exact",
                fields=[
                    MatchkeyField(field="__title_key__", transforms=["lowercase"]),
                    MatchkeyField(field="year", transforms=[]),  # regular column
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__title_key__"],
                                     transforms=["lowercase"])],
            max_block_size=50000, skip_oversized=False,
        ),
    )
    profile = _profile_for_swap_test()
    history = _history_with_prior_decision()
    out = rule_blocking_key_swap(profile, cfg, history)
    assert out is not None
    new_cfg, _ = out
    # Both survive — the mixed matchkey has a non-derived field, user might
    # have a real reason for it
    assert len(new_cfg.matchkeys) == 2


# ============================================================
# Tier 1b — rule_recall_gap_suspected (added autoconfig-tier1-tier2)
# ============================================================

from goldenmatch.core.autoconfig_rules import (
    rule_blocking_field_null_heavy,
    rule_recall_gap_suspected,
)


def test_rule_recall_gap_fires_on_high_random_pair_rate():
    """random_pair_above_threshold_rate > 0.05 with single-pass blocking
    triggers a multi-pass proposal."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["soc_sec_id"], transforms=["strip"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=2000, n_cols=4,
            column_types={"soc_sec_id": "id-like", "name": "name",
                          "address": "text", "city": "geo"},
            cardinality_ratio={"soc_sec_id": 0.7, "name": 0.5,
                                "address": 0.6, "city": 0.4},
            null_rate={"soc_sec_id": 0.0, "name": 0.05,
                        "address": 0.05, "city": 0.05},
        ),
        blocking=BlockingProfile(
            keys_used=[["soc_sec_id"]], n_blocks=400, total_comparisons=900,
            reduction_ratio=0.999, block_sizes_p99=5,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=700, candidates_compared=900,
            mass_above_threshold=1.0, dip_statistic=0.04,
            random_pair_above_threshold_rate=0.08,    # signal: 8% of random pairs match
        ),
        cluster=ClusterProfile(transitivity_rate=0.9),
        matchkey=MatchkeyProfile(per_field={"name": FieldStats(0.5, 0.0, 8)}),
    )
    out = rule_recall_gap_suspected(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "recall_gap_suspected"
    assert new_cfg.blocking.strategy == "multi_pass"
    assert len(new_cfg.blocking.passes) >= 2
    # Second pass should be on a non-blocking column
    second_pass_field = new_cfg.blocking.passes[1].fields[0]
    assert second_pass_field != "soc_sec_id"


def test_rule_recall_gap_does_not_fire_when_probe_rate_low():
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["soc_sec_id"], transforms=["strip"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.99, n_blocks=50),
        scoring=ScoringProfile(
            n_pairs_scored=50, candidates_compared=100,
            mass_above_threshold=0.5, dip_statistic=0.05,
            random_pair_above_threshold_rate=0.02,    # below threshold
        ),
        cluster=ClusterProfile(transitivity_rate=0.9),
    )
    out = rule_recall_gap_suspected(profile, cfg, RunHistory())
    assert out is None


def test_rule_recall_gap_does_not_fire_when_probe_none():
    """When random_pair_above_threshold_rate is None (probe not run), don't fire."""
    cfg = _config_with_blocking()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        scoring=ScoringProfile(random_pair_above_threshold_rate=None),
        cluster=ClusterProfile(transitivity_rate=0.9),
    )
    out = rule_recall_gap_suspected(profile, cfg, RunHistory())
    assert out is None


def test_rule_recall_gap_does_not_fire_on_already_multipass():
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["soc_sec_id"], transforms=["strip"])],
            passes=[
                BlockingKeyConfig(fields=["soc_sec_id"], transforms=["strip"]),
                BlockingKeyConfig(fields=["surname"], transforms=["soundex"]),
            ],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2000, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.99, n_blocks=400),
        scoring=ScoringProfile(
            n_pairs_scored=700, mass_above_threshold=0.6,
            random_pair_above_threshold_rate=0.10,
        ),
        cluster=ClusterProfile(transitivity_rate=0.9),
    )
    out = rule_recall_gap_suspected(profile, cfg, RunHistory())
    assert out is None


# ============================================================
# Tier 1c — rule_blocking_field_null_heavy (added autoconfig-tier1-tier2)
# ============================================================


def test_rule_null_heavy_fires_on_high_null_blocking_field():
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["sparse_id"], transforms=["strip"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=1000, n_cols=3,
            column_types={"sparse_id": "id-like", "name": "name", "city": "geo"},
            cardinality_ratio={"sparse_id": 0.7, "name": 0.5, "city": 0.4},
            null_rate={"sparse_id": 0.30, "name": 0.0, "city": 0.0},  # 30% null
        ),
        blocking=BlockingProfile(reduction_ratio=0.99, n_blocks=100),
        scoring=ScoringProfile(),
        cluster=ClusterProfile(transitivity_rate=0.9),
    )
    out = rule_blocking_field_null_heavy(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "blocking_field_null_heavy"
    assert new_cfg.blocking.strategy == "multi_pass"
    second_pass = new_cfg.blocking.passes[1]
    assert second_pass.fields[0] in {"name", "city"}


def test_rule_null_heavy_does_not_fire_on_low_null_field():
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["dense_id"], transforms=["strip"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=1000, n_cols=2,
            null_rate={"dense_id": 0.02, "name": 0.0},
        ),
        scoring=ScoringProfile(),
        cluster=ClusterProfile(transitivity_rate=0.9),
    )
    out = rule_blocking_field_null_heavy(profile, cfg, RunHistory())
    assert out is None


def test_default_rules_now_has_nine_entries():
    """Adding rule_blocking_field_null_heavy and rule_recall_gap_suspected brought the count to 9.
    Updated to 10: rule_uniform_heavy_blocking added (Fix 2) + rule_blocking_key_swap reordered (Fix 1).
    (rule_enable_llm_scorer was moved out of DEFAULT_RULES into
    AutoConfigController._maybe_decorate_with_llm_scorer post-iteration decoration.)
    Updated to 13: Phase 5 added 3 new rules (v1.10)."""
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
    assert len(DEFAULT_RULES) == 14  # v1.20.x #124: rule_demote_clustered_identity removed from rotation


def test_null_heavy_runs_before_no_matches_and_recall_gap_runs_last():
    """Order: rule_blocking_field_null_heavy first (structural check),
    rule_recall_gap_suspected after no_matches (probe signal, no LLM rule in the table).
    v1.11: rule_demote_clustered_identity moved to position 7 (before generic refit rules);
    rule_sparse_match_expand is now last (position 14), recall_gap is second-to-last."""
    from goldenmatch.core.autoconfig_rules import (
        DEFAULT_RULES,
        rule_blocking_field_null_heavy,
        rule_no_matches,
        rule_recall_gap_suspected,
    )
    idx_null_heavy = DEFAULT_RULES.index(rule_blocking_field_null_heavy)
    idx_no_matches = DEFAULT_RULES.index(rule_no_matches)
    idx_recall_gap = DEFAULT_RULES.index(rule_recall_gap_suspected)
    assert idx_null_heavy < idx_no_matches, "null_heavy must run before no_matches"
    assert idx_recall_gap > idx_no_matches, "recall_gap must run after no_matches"
    # v1.11: recall_gap is second-to-last; sparse_match_expand is last; demote_clustered_identity moved to position 7
    assert idx_recall_gap == len(DEFAULT_RULES) - 2, "recall_gap must be second-to-last (sparse_match_expand last, demote_clustered_identity at position 7)"


# ============================================================
# rule_enable_llm_scorer (added 2026-05-07)
# ============================================================

from goldenmatch.core.autoconfig_rules import (
    _llm_api_key_available,
    rule_enable_llm_scorer,
)


def _config_no_llm_scorer():
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
        # llm_scorer left as default None
    )


def _profile_borderline_heavy():
    return ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10,
                                  block_sizes_p99=20),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=300,
            mass_above_threshold=0.4,
            mass_in_borderline=0.25,    # > 0.10
            dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )


def test_rule_enable_llm_scorer_fires_with_borderline_and_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _config_no_llm_scorer()
    out = rule_enable_llm_scorer(_profile_borderline_heavy(), cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "enable_llm_scorer"
    assert new_cfg.llm_scorer is not None
    assert new_cfg.llm_scorer.enabled is True
    assert new_cfg.llm_scorer.candidate_lo == 0.60
    assert new_cfg.llm_scorer.candidate_hi == 0.90


def test_rule_enable_llm_scorer_silent_when_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _config_no_llm_scorer()
    out = rule_enable_llm_scorer(_profile_borderline_heavy(), cfg, RunHistory())
    assert out is None


def test_rule_enable_llm_scorer_does_not_fire_with_low_borderline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _config_no_llm_scorer()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10,
                                  block_sizes_p99=20),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=300,
            mass_above_threshold=0.4,
            mass_in_borderline=0.05,    # < 0.10
            dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    out = rule_enable_llm_scorer(profile, cfg, RunHistory())
    assert out is None


def test_rule_enable_llm_scorer_does_not_fire_when_no_candidates(monkeypatch):
    """No scoring data yet → don't speculatively enable LLM."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _config_no_llm_scorer()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10,
                                  block_sizes_p99=20),
        scoring=ScoringProfile(
            n_pairs_scored=0, candidates_compared=0,
            mass_above_threshold=0.0,
            mass_in_borderline=0.0,
            dip_statistic=0.0,
        ),
        cluster=ClusterProfile(transitivity_rate=1.0),
    )
    out = rule_enable_llm_scorer(profile, cfg, RunHistory())
    assert out is None


def test_rule_enable_llm_scorer_does_not_fire_when_already_enabled(monkeypatch):
    from goldenmatch.config.schemas import LLMScorerConfig
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _config_no_llm_scorer().model_copy(update={
        "llm_scorer": LLMScorerConfig(enabled=True, candidate_lo=0.7, candidate_hi=0.95),
    })
    out = rule_enable_llm_scorer(_profile_borderline_heavy(), cfg, RunHistory())
    assert out is None


def test_default_rules_now_has_ten_entries():
    """rule_uniform_heavy_blocking was added (Fix 2) and rule_blocking_key_swap
    was reordered (Fix 1), bringing the count to 10.
    (rule_enable_llm_scorer remains outside DEFAULT_RULES as post-iteration decoration.)
    Updated to 13: Phase 5 added rule_corruption_normalize, rule_cross_blocking_disagreement,
    rule_sparse_match_expand (v1.10)."""
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
    assert len(DEFAULT_RULES) == 14  # v1.20.x #124: rule_demote_clustered_identity removed from rotation


def test_rule_enable_llm_scorer_not_in_default_rules():
    """rule_enable_llm_scorer must NOT be in DEFAULT_RULES — it runs as a
    post-iteration decoration via _maybe_decorate_with_llm_scorer, not inside
    the iteration loop. On DQbench, structural rules dominate the budget and
    the rule would never get a turn."""
    from goldenmatch.core.autoconfig_rules import (
        DEFAULT_RULES,
        rule_enable_llm_scorer,
    )
    assert rule_enable_llm_scorer not in DEFAULT_RULES


def test_llm_api_key_helper_reads_openai_or_anthropic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not _llm_api_key_available()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _llm_api_key_available()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    assert _llm_api_key_available()


# ============================================================
# rule_uniform_heavy_blocking (added 2026-05-07)
# ============================================================

from goldenmatch.core.autoconfig_rules import rule_uniform_heavy_blocking


def test_rule_uniform_heavy_blocking_fires_on_t2_signature():
    """T2-like: 25 blocks × avg 80 records, mass_above=1.0, mass_borderline=0.95.
    Should propose switch to a high-cardinality identity column."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="customer_name", scorer="ensemble",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city", "product_category"],
                                     transforms=["lowercase"])],
            passes=[
                BlockingKeyConfig(fields=["city", "product_category"], transforms=["lowercase"]),
                BlockingKeyConfig(fields=["product_category"], transforms=["lowercase"]),
            ],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=2000, n_cols=8,
            column_types={
                "city": "geo", "product_category": "text",
                "customer_email": "email", "customer_name": "name",
                "phone_number": "phone", "billing_zip": "zip",
                "customer_id": "id-like", "order_total": "numeric",
            },
            cardinality_ratio={
                "city": 0.05, "product_category": 0.02,
                "customer_email": 0.95, "customer_name": 0.85,
                "phone_number": 0.85, "billing_zip": 0.20,
                "customer_id": 0.99, "order_total": 0.50,
            },
        ),
        blocking=BlockingProfile(
            keys_used=[["city", "product_category"]], n_blocks=25,
            total_comparisons=79767, reduction_ratio=0.96,
            block_sizes_p50=80, block_sizes_p95=98, block_sizes_p99=98,
            block_sizes_max=98,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=598, candidates_compared=79767,
            mass_above_threshold=1.0, mass_in_borderline=0.9465,
        ),
        cluster=ClusterProfile(transitivity_rate=0.021),
        matchkey=MatchkeyProfile(per_field={
            "customer_name": FieldStats(0.85, 0.0, 12),
        }),
    )
    out = rule_uniform_heavy_blocking(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    assert decision.rule_name == "uniform_heavy_blocking"
    # Should pick customer_email or customer_name (high-cardinality identity)
    # customer_email (0.95) is excluded by cardinality ceiling; customer_name (0.85) should win
    new_field = new_cfg.blocking.keys[0].fields[0]
    assert new_field in {"customer_email", "customer_name"}
    # Multi-pass dropped
    assert len(new_cfg.blocking.passes or []) == 0


def test_rule_uniform_heavy_blocking_does_not_fire_when_blocks_small():
    """When avg block size < 30, this rule doesn't fire (different pathology)."""
    cfg = _config_with_blocking()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2000, n_cols=4),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=500, total_comparisons=1000,
            reduction_ratio=0.99, block_sizes_p99=5,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, candidates_compared=1000,
            mass_above_threshold=1.0, mass_in_borderline=0.6,
        ),
        cluster=ClusterProfile(transitivity_rate=0.5),
    )
    out = rule_uniform_heavy_blocking(profile, cfg, RunHistory())
    assert out is None


def test_rule_uniform_heavy_blocking_does_not_fire_when_few_candidates():
    """When candidates_compared < n_rows, blocking is too tight, not too loose."""
    cfg = _config_with_blocking()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2000, n_cols=4),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=20, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p99=100,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, candidates_compared=500,  # < n_rows
            mass_above_threshold=1.0, mass_in_borderline=0.6,
        ),
        cluster=ClusterProfile(transitivity_rate=0.5),
    )
    out = rule_uniform_heavy_blocking(profile, cfg, RunHistory())
    assert out is None


def test_rule_uniform_heavy_blocking_does_not_fire_when_mass_above_low():
    """When most pairs DON'T match, this isn't the over-coarse pathology."""
    cfg = _config_with_blocking()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2000, n_cols=4,
                          cardinality_ratio={"a": 0.01, "name": 0.85}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=20, total_comparisons=80000,
            reduction_ratio=0.96, block_sizes_p99=100,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=80000,
            mass_above_threshold=0.1,  # below 0.5
            mass_in_borderline=0.6,
        ),
        cluster=ClusterProfile(transitivity_rate=0.5),
    )
    out = rule_uniform_heavy_blocking(profile, cfg, RunHistory())
    assert out is None


def test_rule_uniform_heavy_blocking_does_not_fire_when_mass_borderline_low():
    """When mass_in_borderline < 0.5, matches are confident — may be real matches."""
    cfg = _config_with_blocking()
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2000, n_cols=4,
                          cardinality_ratio={"a": 0.01, "name": 0.85},
                          column_types={"a": "text", "name": "name"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=20, total_comparisons=80000,
            reduction_ratio=0.96, block_sizes_p99=100,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=80000,
            mass_above_threshold=0.9,
            mass_in_borderline=0.2,  # below 0.5 — confident matches
        ),
        cluster=ClusterProfile(transitivity_rate=0.5),
    )
    out = rule_uniform_heavy_blocking(profile, cfg, RunHistory())
    assert out is None


def test_rule_uniform_heavy_blocking_returns_none_when_no_alternate_field():
    """No high-cardinality identity-bearing field available → don't fire."""
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="city", scorer="exact",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=2000, n_cols=2,
            column_types={"city": "geo", "state": "geo"},
            cardinality_ratio={"city": 0.05, "state": 0.01},
        ),
        blocking=BlockingProfile(
            keys_used=[["city"]], n_blocks=25,
            total_comparisons=79767, reduction_ratio=0.96,
            block_sizes_p99=98,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=598, candidates_compared=79767,
            mass_above_threshold=1.0, mass_in_borderline=0.95,
        ),
        cluster=ClusterProfile(transitivity_rate=0.021),
    )
    out = rule_uniform_heavy_blocking(profile, cfg, RunHistory())
    assert out is None


# ── Ordering tests for Fix 1 + Fix 2 ────────────────────────────────────────


def test_default_rules_order_blocking_key_swap_before_low_transitivity():
    """Fix 1 — when blocking is fundamentally wrong (mass_above=0), swap
    the key BEFORE tuning the threshold. Otherwise low_transitivity fires
    iteration after iteration, lowering threshold uselessly."""
    from goldenmatch.core.autoconfig_rules import (
        DEFAULT_RULES,
        rule_blocking_key_swap,
        rule_low_transitivity,
    )
    idx_swap = DEFAULT_RULES.index(rule_blocking_key_swap)
    idx_lt = DEFAULT_RULES.index(rule_low_transitivity)
    assert idx_swap < idx_lt


def test_default_rules_uniform_heavy_after_blocking_too_coarse():
    """rule_blocking_too_coarse handles skewed (p99 outlier);
    rule_uniform_heavy_blocking handles uniform-large. Both target
    structural blocking issues; uniform-heavy comes after the more
    specific p99-outlier check."""
    from goldenmatch.core.autoconfig_rules import (
        DEFAULT_RULES,
        rule_blocking_too_coarse,
        rule_uniform_heavy_blocking,
    )
    idx_too_coarse = DEFAULT_RULES.index(rule_blocking_too_coarse)
    idx_uniform = DEFAULT_RULES.index(rule_uniform_heavy_blocking)
    assert idx_too_coarse < idx_uniform


def test_default_rules_now_has_ten_entries_final():
    """Fix 1 (reorder) + Fix 2 (new rule) → 10 rules total.
    Updated to 13: Phase 5 added 3 new indicator-aware rules (v1.10)."""
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
    assert len(DEFAULT_RULES) == 14  # v1.20.x #124: rule_demote_clustered_identity removed from rotation


# ============================================================
# Task 3.2 — RefitPolicy.propose accepts optional ctx kwarg
# ============================================================

def test_heuristic_propose_accepts_ctx_kwarg():
    """HeuristicRefitPolicy.propose accepts an optional ctx kwarg."""
    import inspect

    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
    pol = HeuristicRefitPolicy()
    sig = inspect.signature(pol.propose)
    assert "ctx" in sig.parameters


def test_llm_propose_accepts_and_forwards_ctx():
    """LLMRefitPolicy.propose accepts ctx and forwards to base."""
    import inspect

    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy, LLMRefitPolicy
    pol = LLMRefitPolicy(base=HeuristicRefitPolicy())
    sig = inspect.signature(pol.propose)
    assert "ctx" in sig.parameters


# ============================================================
# Task 3.3 — Controller backward compat with old 3-arg custom policy
# ============================================================

def test_controller_supports_old_shape_3arg_custom_policy():
    """A custom policy with 3-arg propose (no ctx) still works."""
    import polars as pl
    from goldenmatch.core.autoconfig_controller import (
        AutoConfigController,
        ControllerBudget,
    )

    class _OldShapePolicy:
        def propose(self, profile, current, history):
            return None    # always satisfied

    # Two columns, two rows → enters the iteration loop and calls policy.propose
    df = pl.DataFrame({"name": ["alice", "bob"], "email": ["a@x.com", "b@x.com"]})
    controller = AutoConfigController(
        policy=_OldShapePolicy(),
        budget=ControllerBudget(max_iterations=2, sample_skip_below=1),
    )
    config, profile, history = controller.run(df)
    assert profile is not None    # didn't TypeError on 4-arg call
