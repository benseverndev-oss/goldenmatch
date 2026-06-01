"""Tests for the planner-rule dispatcher (no rules registered yet).

Spec §Decision rules: rules are (predicate, action) pairs evaluated in
order; first match wins. The registry is the source of truth for which
rules exist; phases 3-6 add concrete rules to it.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_planner import (
    PlannerRule,
    apply_planner_rules,
)
from goldenmatch.core.autoconfig_planner_rules import _scoring_backend
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ProfileMeta,
)
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile


def _minimal_profile(n_rows: int = 1000) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows, n_cols=3),
        blocking=BlockingProfile(
            keys_used=[["name"]],
            n_blocks=10,
            total_comparisons=100,
            reduction_ratio=0.9,
            block_sizes_p50=10,
            block_sizes_p95=15,
            block_sizes_p99=20,
            block_sizes_max=25,
            singleton_block_count=0,
            oversized_block_count=0,
        ),
        meta=ProfileMeta(
            iteration=0,
            is_sample=False,
            sample_size=n_rows,
            n_rows_full=n_rows,
            wall_clock_ms=0,
            seed=0,
        ),
    )


def _minimal_runtime() -> RuntimeProfile:
    return RuntimeProfile(available_ram_gb=16.0, cpu_count=4, disk_free_gb=100.0)


def test_apply_planner_rules_with_no_rules_returns_default_plan():
    """Empty rule list -> default plan (polars-direct). This is the
    behavior phase 2 lands; phases 3-6 add rules."""
    plan = apply_planner_rules(
        profile=_minimal_profile(),
        runtime=_minimal_runtime(),
        n_rows_full=1000,
        rules=[],
    )
    assert plan.backend == "polars-direct"
    assert plan.rule_name == "no_rules_registered"


def test_apply_planner_rules_first_match_wins():
    """When multiple rules match, the FIRST one in the registry wins."""
    def predicate_true(profile, runtime, n_rows_full):
        return True

    def action_a(profile, runtime, n_rows_full):
        return ExecutionPlan(backend="duckdb", rule_name="rule_a")

    def action_b(profile, runtime, n_rows_full):
        return ExecutionPlan(backend="ray", rule_name="rule_b")

    plan = apply_planner_rules(
        profile=_minimal_profile(),
        runtime=_minimal_runtime(),
        n_rows_full=1000,
        rules=[
            PlannerRule(name="rule_a", predicate=predicate_true, action=action_a),
            PlannerRule(name="rule_b", predicate=predicate_true, action=action_b),
        ],
    )
    assert plan.backend == "duckdb"
    assert plan.rule_name == "rule_a"


def test_apply_planner_rules_skips_non_matching():
    def matches_n_rows_gte(threshold):
        def predicate(profile, runtime, n_rows_full):
            return n_rows_full >= threshold
        return predicate

    def action_b(profile, runtime, n_rows_full):
        return ExecutionPlan(backend="duckdb", rule_name="b_fires")

    def action_a(profile, runtime, n_rows_full):
        return ExecutionPlan(backend="ray", rule_name="a_fires")

    plan = apply_planner_rules(
        profile=_minimal_profile(),
        runtime=_minimal_runtime(),
        n_rows_full=5_000,
        rules=[
            PlannerRule(name="a", predicate=matches_n_rows_gte(1_000_000), action=action_a),
            PlannerRule(name="b", predicate=matches_n_rows_gte(1_000),    action=action_b),
        ],
    )
    assert plan.rule_name == "b_fires"


def test_apply_planner_rules_fills_rule_name_from_registry_when_action_omits():
    """If an action returns ExecutionPlan without rule_name, the dispatcher
    fills it in from the registry entry's name. This keeps rule authors
    from having to thread their own name through."""
    def always(profile, runtime, n_rows_full):
        return True

    def action_no_name(profile, runtime, n_rows_full):
        return ExecutionPlan(backend="chunked")  # rule_name omitted

    plan = apply_planner_rules(
        profile=_minimal_profile(),
        runtime=_minimal_runtime(),
        n_rows_full=1000,
        rules=[PlannerRule(name="rule_chunked", predicate=always, action=action_no_name)],
    )
    assert plan.rule_name == "rule_chunked"


def test_controller_run_attaches_execution_plan_to_history():
    """After phase 2, every successful run leaves an ExecutionPlan on
    RunHistory.execution_plan. With no rules registered, rule_name is one
    of the sentinels."""
    import goldenmatch as gm
    import polars as pl
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN

    df = pl.DataFrame({
        "name": ["alice", "alyce", "bob", "robert"] * 20,
        "email": [f"u{i}@x.com" for i in range(80)],
    })
    # No fuzzy/exact kwargs -> zero-config path -> auto_configure_df fires.
    _ = gm.dedupe_df(df)
    ctrl_state = _LAST_CONTROLLER_RUN.get()
    assert ctrl_state is not None, "controller should have run"
    _profile, history = ctrl_state
    plan = getattr(history, "execution_plan", None)
    assert plan is not None, "history should have execution_plan attached"
    # rule_name is one of the registered DEFAULT_RULES, or a sentinel if
    # no rule matched. Specific rule selection is covered by
    # test_autoconfig_planner_rules.py + per-phase integration tests.
    assert plan.rule_name is not None
    # 80-row fixture hits the simple plan (rule 2); backend is the real selector's choice.
    assert plan.backend == _scoring_backend()
