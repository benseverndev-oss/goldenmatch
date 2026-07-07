from goldenpipe.autoconfig_planner import (
    PipePlan,
    PipePlannerRule,
    PipeProfile,
    PlannedStage,
    plan_pipeline,
)


def _profile(**kw):
    base = dict(n_rows=100, n_cols=3, column_names=("a", "b", "c"),
                dtypes=("String", "Int64", "String"),
                inferred_domain=None, domain_confidence=0.0)
    base.update(kw)
    return PipeProfile(**base)


def test_plan_pipeline_first_match_wins_else_default():
    fired = PipePlannerRule(
        rule_name="fired",
        predicate=lambda p: p.n_rows == 100,
        action=lambda p: PipePlan(stages=(PlannedStage("x", {}),), rule_name="fired",
                                  confidence=0.9, evidence={"n_rows": p.n_rows}),
    )
    plan = plan_pipeline(_profile(), rules=[fired])
    assert plan.rule_name == "fired"
    assert plan.stages == (PlannedStage("x", {}),)
    assert plan.evidence == {"n_rows": 100}


def test_plan_pipeline_falls_through_to_default():
    never = PipePlannerRule("never", lambda p: False,
                            lambda p: PipePlan((), "never", 0.0, {}))
    plan = plan_pipeline(_profile(), rules=[never])
    assert plan.rule_name == "default"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )


def test_structs_are_frozen():
    import dataclasses

    import pytest
    p = _profile()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.n_rows = 5  # type: ignore[misc]


from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES  # noqa: E402


def test_rule_pathological_skips_dedupe():
    plan = plan_pipeline(_profile(n_rows=1))
    assert plan.rule_name == "pathological"
    assert tuple(s.name for s in plan.stages) == (
        "goldencheck.scan", "goldenflow.transform",
    )
    assert plan.confidence == 1.0


def test_rule_confident_schema_prepends_infer_schema():
    plan = plan_pipeline(_profile(inferred_domain="finance", domain_confidence=0.8))
    assert plan.rule_name == "confident_schema"
    assert tuple(s.name for s in plan.stages) == (
        "infer_schema", "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    )
    assert plan.stages[0].config == {"domain": "finance"}
    assert plan.confidence == 0.8


def test_rule_weak_domain_is_default():
    plan = plan_pipeline(_profile(inferred_domain="finance", domain_confidence=0.4))
    assert plan.rule_name == "default"
    assert all(s.name != "infer_schema" for s in plan.stages)


def test_default_rules_is_the_module_table():
    assert plan_pipeline(_profile(n_rows=1)).rule_name == "pathological"
    assert len(DEFAULT_RULES) >= 2
