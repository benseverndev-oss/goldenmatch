from goldenpipe.autoconfig_planner import (
    SCALE_ROUTE_MIN_ROWS,
    ComplexityProfile,
    PipePlan,
    PipePlannerRule,
    PipeProfile,
    PlannedStage,
    PlannerInput,
    apply_scale_hints,
    band_of,
    plan_pipeline,
)


def _profile(**kw):
    base = dict(n_rows=100, n_cols=3, column_names=("a", "b", "c"),
                dtypes=("String", "Int64", "String"),
                inferred_domain=None, domain_confidence=0.0)
    base.update(kw)
    return PipeProfile(**base)


def _complexity(**kw):
    base = dict(max_null_density=0.0, mean_null_density=0.0)
    base.update(kw)
    return ComplexityProfile(**base)


def _planner_input(*, max_null_density=0.0, mean_null_density=0.0, **profile_kw):
    return PlannerInput(
        runtime=_profile(**profile_kw),
        complexity=_complexity(max_null_density=max_null_density,
                               mean_null_density=mean_null_density),
    )


def test_plan_pipeline_first_match_wins_else_default():
    fired = PipePlannerRule(
        rule_name="fired",
        predicate=lambda inp: inp.runtime.n_rows == 100,
        action=lambda inp: PipePlan(stages=(PlannedStage("x", {}),), rule_name="fired",
                                    confidence=0.9, evidence={"n_rows": inp.runtime.n_rows}),
    )
    plan = plan_pipeline(_planner_input(), rules=[fired])
    assert plan.rule_name == "fired"
    assert plan.stages == (PlannedStage("x", {}),)
    assert plan.evidence == {"n_rows": 100}


def test_plan_pipeline_falls_through_to_default():
    never = PipePlannerRule("never", lambda inp: False,
                            lambda inp: PipePlan((), "never", 0.0, {}))
    plan = plan_pipeline(_planner_input(), rules=[never])
    assert plan.rule_name == "default"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )


def test_structs_are_frozen():
    import dataclasses

    import pytest
    inp = _planner_input()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.runtime.n_rows = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.complexity.max_null_density = 0.5  # type: ignore[misc]


from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES  # noqa: E402


def test_rule_pathological_skips_dedupe():
    plan = plan_pipeline(_planner_input(n_rows=1))
    assert plan.rule_name == "pathological"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform",
    )
    assert plan.confidence == 1.0


def test_rule_confident_schema_prepends_infer_schema():
    plan = plan_pipeline(_planner_input(inferred_domain="finance", domain_confidence=0.8))
    assert plan.rule_name == "confident_schema"
    assert tuple(s.name for s in plan.stages) == (
        "infer_schema", "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )
    assert plan.stages[0].config == {"domain": "finance"}
    assert plan.confidence == 0.8


def test_rule_weak_domain_is_default():
    plan = plan_pipeline(_planner_input(inferred_domain="finance", domain_confidence=0.4))
    assert plan.rule_name == "default"
    assert all(s.name != "infer_schema" for s in plan.stages)


def test_default_rules_is_the_module_table():
    assert plan_pipeline(_planner_input(n_rows=1)).rule_name == "pathological"
    assert len(DEFAULT_RULES) >= 2


def test_band_of_boundaries():
    assert band_of(0.7) == "green"
    assert band_of(0.71) == "green"
    assert band_of(0.69) == "amber"
    assert band_of(0.4) == "amber"
    assert band_of(0.39) == "red"
    assert band_of(0.0) == "red"


def test_rule_low_confidence_is_red_and_safe_default():
    plan = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.7))
    assert plan.rule_name == "low_confidence"
    assert plan.confidence == 0.3
    assert band_of(plan.confidence) == "red"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )


def test_low_confidence_not_shadowed_by_confident_schema():
    # domain present -> confident_schema wins; domain absent + high null -> low_confidence.
    with_domain = plan_pipeline(_planner_input(inferred_domain="finance",
                                               domain_confidence=0.8, max_null_density=0.9))
    assert with_domain.rule_name == "confident_schema"
    no_domain = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.7))
    assert no_domain.rule_name == "low_confidence"


def test_low_confidence_only_above_null_threshold():
    # domain absent but low null density -> falls through to default (not RED).
    plan = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.5))
    assert plan.rule_name == "default"


def test_default_evidence_records_null_density():
    plan = plan_pipeline(_planner_input(inferred_domain=None, max_null_density=0.5,
                                        mean_null_density=0.25))
    assert plan.evidence["max_null_density"] == 0.5
    assert plan.evidence["mean_null_density"] == 0.25


def _plan_with_dedupe():
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="default", confidence=0.7, evidence={"n_rows": 2_000_000},
    )


def test_apply_scale_hints_annotates_dedupe_at_scale():
    plan = _plan_with_dedupe()
    out = apply_scale_hints(plan, _profile(n_rows=SCALE_ROUTE_MIN_ROWS))
    dedupe = next(s for s in out.stages if s.name == "goldenmatch.dedupe")
    assert dedupe.config == {"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}
    assert out.evidence["scale_hinted"] is True
    assert [s.name for s in out.stages] == [
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    ]


def test_apply_scale_hints_noop_below_threshold():
    plan = _plan_with_dedupe()
    out = apply_scale_hints(plan, _profile(n_rows=SCALE_ROUTE_MIN_ROWS - 1))
    assert out is plan


def test_apply_scale_hints_noop_without_dedupe():
    plan = PipePlan(
        stages=(PlannedStage("goldencheck.scan", {}), PlannedStage("goldenflow.transform", {})),
        rule_name="pathological", confidence=1.0, evidence={},
    )
    out = apply_scale_hints(plan, _profile(n_rows=5_000_000))
    assert out is plan


def test_apply_scale_hints_is_pure():
    plan = _plan_with_dedupe()
    apply_scale_hints(plan, _profile(n_rows=SCALE_ROUTE_MIN_ROWS))
    orig_dedupe = next(s for s in plan.stages if s.name == "goldenmatch.dedupe")
    assert orig_dedupe.config == {}
    assert "scale_hinted" not in plan.evidence


def test_scale_route_min_rows_constant():
    assert SCALE_ROUTE_MIN_ROWS == 1_000_000
