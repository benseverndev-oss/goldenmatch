"""The JSON face of the pure-Python planner — the SP2 parity surface (SHIPPED).

Each ``*_json`` fn CALLS the real Resolver/Router/decisions/_auto_config and serializes
to goldenpipe-core's exact JSON shapes, so the parity gate tests the actual planner
(not a re-implementation). It does NOT run at pipeline runtime; ordering/validation/
routing stay in the engine modules. Mirrors goldenpipe-core/src/json.rs.
"""
from __future__ import annotations

import json
from typing import Any

from goldenpipe import decisions as _dec
from goldenpipe.engine.resolver import PlannedStage, Resolver, WiringError
from goldenpipe.engine.router import Router
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import Decision, PipeContext
from goldenpipe.models.stage import StageInfo


class _Stub:
    """A minimal Stage object carrying .info (all the planner reads)."""

    def __init__(self, info: StageInfo) -> None:
        self.info = info


class _StubRegistry:
    """Registry keyed EXPLICITLY (bypasses register()'s key-by-info.name) so
    ``key != info.name`` vectors resolve. Provides get()/list_all()."""

    def __init__(self) -> None:
        self._stages: dict[str, Any] = {}

    def add(self, key: str, info: StageInfo) -> None:
        self._stages[key] = _Stub(info)

    def get(self, name: str) -> Any:
        if name not in self._stages:
            raise KeyError(f"Stage '{name}' not found in registry")
        return self._stages[name]

    def list_all(self) -> dict[str, StageInfo]:
        return {k: s.info for k, s in self._stages.items()}


def _info(d: dict) -> StageInfo:
    return StageInfo(name=d["name"], produces=list(d["produces"]), consumes=list(d["consumes"]))


def _planned_to_dict(p: Any) -> dict:
    out = {"name": p.name, "use": p.spec.use, "config": p.config or {}, "on_error": p.spec.on_error}
    if p.spec.skip_if is not None:
        out["skip_if"] = p.spec.skip_if
    return out


def resolve_json(input_str: str) -> str:
    arg = json.loads(input_str)
    reg = _StubRegistry()
    for s in arg["stages"]:
        reg.add(s["key"], _info(s))  # key by the registry KEY, not info.name
    config = PipelineConfig(**arg["config"])
    try:
        plan = Resolver.resolve(config, reg)
    except WiringError as e:
        return json.dumps(
            {"err": {"kind": "wiring", "stage": e.stage, "missing": e.missing, "available": e.available}}
        )
    except KeyError:
        # unknown `use`: the first configured stage whose use isn't registered
        # (Resolver fails on the first such stage, in order).
        for raw in config.stages:
            use = raw if isinstance(raw, str) else raw.use
            if use not in reg._stages:
                return json.dumps({"err": {"kind": "unknown_stage", "use": use}})
        raise  # unreachable
    return json.dumps({"ok": {"stages": [_planned_to_dict(p) for p in plan.stages]}})


def apply_decision_json(input_str: str) -> str:
    arg = json.loads(input_str)
    d = arg["decision"]
    decision = Decision(
        skip=d.get("skip", []),
        abort=d.get("abort", False),
        insert=d.get("insert", []),
        reason=d.get("reason", ""),
    )
    remaining = []
    for r in arg["remaining"]:
        # carry on_error/skip_if through the spec so a remaining stage that has them
        # round-trips faithfully (the core's PlannedSpec round-trips both).
        spec = StageSpec(
            use=r["use"], name=r.get("name"), on_error=r.get("on_error", "continue"),
            skip_if=r.get("skip_if"),
        )
        remaining.append(PlannedStage(name=r["name"], stage=None, spec=spec, config=r.get("config", {})))
    reg = _StubRegistry()
    for name in decision.insert:  # Router.get(name) for each inserted stage
        reg.add(name, StageInfo(name=name, produces=[], consumes=[]))
    ctx = PipeContext()
    new_remaining = Router.apply(decision, remaining, ctx, reg)
    out: dict = {"remaining": [_planned_to_dict(p) for p in new_remaining]}
    note = ctx.reasoning.get("_router")
    if note is not None:
        out["router_note"] = note
    return json.dumps(out)


_BUILTINS = {
    "severity_gate": _dec.severity_gate,
    "pii_router": _dec.pii_router,
    "row_count_gate": _dec.row_count_gate,
}


def evaluate_builtin_json(input_str: str) -> str:
    arg = json.loads(input_str)
    fn = _BUILTINS.get(arg["name"])
    if fn is None:
        return "null"
    ctx_in = arg.get("ctx", {})
    ctx = PipeContext(artifacts=ctx_in.get("artifacts", {}), metadata=ctx_in.get("metadata", {}))
    d = fn(ctx)
    if d is None:
        return "null"
    return json.dumps({"skip": d.skip, "abort": d.abort, "insert": d.insert, "reason": d.reason})


def auto_config_json(input_str: str) -> str:
    from goldenpipe.pipeline import Pipeline

    arg = json.loads(input_str)
    reg = _StubRegistry()
    for name in arg["available"]:
        reg.add(name, StageInfo(name=name, produces=[], consumes=[]))
    p = Pipeline(registry=reg, identity_opts=arg.get("identity_opts"))
    cfg = p._auto_config()
    stages = []
    for spec in cfg.stages:  # each is a StageSpec
        stages.append({"use": spec.use, "needs": spec.needs, "on_error": spec.on_error, "config": spec.config})
    return json.dumps({"pipeline": cfg.pipeline, "stages": stages, "decisions": cfg.decisions})


def skip_if_falsy_json(input_str: str) -> str:
    return json.dumps(not json.loads(input_str))
