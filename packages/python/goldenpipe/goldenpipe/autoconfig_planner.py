"""Plan-first auto-config decision core (portable — NO Polars/Pydantic).

The pyo3-free-portable kernel: PipeProfile (in) -> PipePlan (out) via a pure
rule table. Host glue (Polars profiling, Pydantic config) lives in
`autoconfig_glue.py`. Mirrors goldenmatch's autoconfig_planner (PlannerRule +
first-match plan) so the later `goldenpipe-core` Rust port is mechanical.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PipeProfile:
    """Cheap, up-front signals — no stage execution required."""
    n_rows: int
    n_cols: int
    column_names: tuple[str, ...]
    dtypes: tuple[str, ...]
    inferred_domain: str | None
    domain_confidence: float


@dataclass(frozen=True)
class PlannedStage:
    name: str            # EXACT registry name (e.g. "goldencheck.scan", "infer_schema")
    config: dict         # per-stage config


@dataclass(frozen=True)
class PipePlan:
    stages: tuple[PlannedStage, ...]
    rule_name: str
    confidence: float
    evidence: dict


Predicate = Callable[[PipeProfile], bool]
Action = Callable[[PipeProfile], "PipePlan"]


@dataclass(frozen=True)
class PipePlannerRule:
    rule_name: str
    predicate: Predicate
    action: Action


def default_evidence(p: PipeProfile) -> dict:
    """Signal snapshot attached to every plan (evidence for humans/telemetry)."""
    return {
        "n_rows": p.n_rows,
        "n_cols": p.n_cols,
        "inferred_domain": p.inferred_domain,
        "domain_confidence": p.domain_confidence,
    }


def _default_plan(p: PipeProfile) -> PipePlan:
    """The current static shape: scan -> flow -> dedupe (no infer_schema)."""
    return PipePlan(
        stages=(
            PlannedStage("goldencheck.scan", {}),
            PlannedStage("goldenflow.transform", {}),
            PlannedStage("goldenmatch.dedupe", {}),
        ),
        rule_name="default",
        confidence=0.7,
        evidence=default_evidence(p),
    )


def plan_pipeline(
    profile: PipeProfile,
    rules: Sequence[PipePlannerRule] | None = None,
) -> PipePlan:
    """First matching rule's action builds the plan; else the default shape."""
    if rules is None:
        from goldenpipe.autoconfig_planner_rules import DEFAULT_RULES
        rules = DEFAULT_RULES
    for rule in rules:
        if rule.predicate(profile):
            return rule.action(profile)
    return _default_plan(profile)
