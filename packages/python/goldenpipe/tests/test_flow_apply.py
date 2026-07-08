import types

from goldenpipe.models.context import PipeContext


class _SpyResult:
    def __init__(self):
        self.df = "DF_OUT"
        self.manifest = types.SimpleNamespace(records=[])


def _install_spy(monkeypatch):
    calls = {}
    def spy(df, config=None, **kw):
        calls["df"] = df
        calls["config"] = config
        calls["kw"] = kw
        return _SpyResult()
    import goldenpipe.adapters.flow as flowmod
    monkeypatch.setattr(flowmod, "_transform", spy)
    monkeypatch.setattr(flowmod, "HAS_FLOW", True)
    return calls


def _ctx(stage_config=None, repair_plan=None):
    ctx = PipeContext(df="DF_IN")
    ctx.stage_config = stage_config or {}
    if repair_plan is not None:
        ctx.artifacts["repair_plan"] = repair_plan
    return ctx


def _run(ctx):
    from goldenpipe.adapters.flow import TransformStage
    return TransformStage().run(ctx)


def test_gate_off_no_config_calls_autodetect(monkeypatch):
    calls = _install_spy(monkeypatch)
    _run(_ctx())
    assert calls["config"] is None and calls["kw"] == {}


def test_gate_off_with_user_config_unchanged(monkeypatch):
    calls = _install_spy(monkeypatch)
    _run(_ctx(stage_config={"config": {"transforms": [{"column": "a", "ops": ["strip"]}]}}))
    assert calls["config"] == {"transforms": [{"column": "a", "ops": ["strip"]}]}


def test_gate_on_injects_fixer_specs(monkeypatch):
    calls = _install_spy(monkeypatch)
    plan = {"repairs": [{"column": "email", "check": "format_detection", "suggested_transforms": ["email_normalize"], "reason": "x"}]}
    _run(_ctx(stage_config={"apply_repairs": True}, repair_plan=plan))
    assert calls["config"] == {"transforms": [{"column": "email", "ops": ["email_normalize"]}]}


def test_gate_on_all_assertion_falls_through_to_autodetect(monkeypatch):
    calls = _install_spy(monkeypatch)
    plan = {"repairs": [{"column": "iban", "check": "pattern_consistency", "suggested_transforms": ["iban_validate"], "reason": "b"}]}
    ctx = _ctx(stage_config={"apply_repairs": True}, repair_plan=plan)
    _run(ctx)
    assert calls["config"] is None
    assert "iban_validate" in ctx.reasoning.get("repair_skipped", "")


def test_gate_on_merges_user_and_repair(monkeypatch):
    calls = _install_spy(monkeypatch)
    plan = {"repairs": [{"column": "email", "check": "pattern_consistency", "suggested_transforms": ["email_canonical"], "reason": "y"}]}
    sc = {"apply_repairs": True, "config": {"transforms": [{"column": "email", "ops": ["email_lowercase"]}]}}
    _run(_ctx(stage_config=sc, repair_plan=plan))
    assert calls["config"] == {"transforms": [{"column": "email", "ops": ["email_lowercase", "email_canonical"]}]}


def test_gate_pop_does_not_mutate_ctx_stage_config(monkeypatch):
    _install_spy(monkeypatch)
    sc = {"apply_repairs": True}
    ctx = _ctx(stage_config=sc, repair_plan={"repairs": []})
    _run(ctx)
    assert sc == {"apply_repairs": True}
